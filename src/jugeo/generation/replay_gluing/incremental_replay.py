"""Chapter 43, Section 2 — Incremental Replay Execution.

This module implements the incremental replay subsystem for the JuGeo generation
pipeline.  When a geometric construction is modified, it is often wasteful to
re-execute every patch from scratch.  Instead, the incremental replayer:

  1. Inspects a *prior gluing* (a snapshot of previously computed section data
     plus overlap conditions) and a *ReplayGluingPlan* that describes which
     patches changed and what the desired replay strategy is.
  2. For every patch that did NOT change, it retrieves the cached section result
     directly from the ReplayCache, skipping expensive re-computation.
  3. For every patch that DID change, it re-executes the section logic (calling
     into jugeo.geometry.descent when available, or using a self-contained
     fallback), and stores the fresh result back in the cache.
  4. After all patches have been processed it runs the OverlapReconciler to
     verify that neighbouring sections still agree on their shared boundary
     conditions.  If an incompatibility is detected, an OverlapIncompatibilityError
     is raised or an automatic fix is suggested.
  5. The final state is recorded in a GluingUnderReplay object and returned to
     the caller.

The module is intentionally written to be importable even when the broader
jugeo.geometry and jugeo.generation packages are absent (HAS_JUGEO_DEPS = False),
allowing it to be used in lightweight testing environments.

Public API
----------
IncrementalReplayer      – main façade; create one per replay session
ReplayCache              – LRU cache keyed on patch name
OverlapReconciler        – verifies and repairs inter-patch overlap conditions
GluingSnapshot           – immutable (by convention) record of a gluing state
create_snapshot_from_gluing / restore_snapshot – snapshot ↔ dict round-trip
compute_incremental_delta / merge_snapshots    – diffing and merging utilities
validate_incremental_gluing                    – sanity-check helper

Error hierarchy
---------------
ReplayError                  – base exception
OverlapIncompatibilityError  – raised when two patches cannot be reconciled
"""

from __future__ import annotations

import hashlib
import logging
import math
import time
import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Optional jugeo dependencies
# ---------------------------------------------------------------------------

try:
    from jugeo.generation.replay_gluing.models import (
        ReplayGluingPlan,
        GluingUnderReplay,
        IncrementalGluing,
        ReplayPhase,
        ReplayStrategy,
    )
    HAS_MODELS = True
except ImportError:
    HAS_MODELS = False

try:
    from jugeo.geometry.descent import DescentEngine, LocalSection, OverlapCondition, GluingData
    from jugeo.geometry.supports import SupportRegion
    from jugeo.geometry.site import CoordinateObject, CoordinateKind
    from jugeo.generation.construction import (
        Candidate, ConstructionLoop, ConstructionResult, ConstructionContext,
    )
    from jugeo.generation.treaties import OverlapTreaty, TreatyClause, TreatyStatus, evaluate_treaty
    HAS_JUGEO_DEPS = True
except ImportError:
    HAS_JUGEO_DEPS = False

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

DEFAULT_CACHE_SIZE: int = 500
REPLAY_SECTION_VERSION: str = "1.0"
_SECTION_KEY = "sections"
_OVERLAP_KEY = "overlaps"
_TREATY_KEY = "treaties"


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class ReplayError(Exception):
    """Base class for all errors raised by the incremental replay subsystem."""


class OverlapIncompatibilityError(ReplayError):
    """Raised when two adjacent patches produce irreconcilable section data.

    Attributes
    ----------
    patches : tuple[str, str]
        The (left, right) patch names whose overlap is incompatible.
    """

    def __init__(self, patches: tuple[str, str], message: str = "") -> None:
        self.patches = patches
        super().__init__(
            message or f"Overlap incompatibility between patches {patches[0]!r} and {patches[1]!r}"
        )


# ---------------------------------------------------------------------------
# Helper dataclasses
# ---------------------------------------------------------------------------


@dataclass
class ReplayStep:
    """Record of a single patch replay action within a session.

    Attributes
    ----------
    step_id : str
        Unique identifier for this step.
    patch : str
        The patch name being processed.
    action : str
        One of ``"replay"``, ``"skip"``, ``"reconcile"``.
    success : bool
        Whether the action completed without error.
    duration_ms : float
        Wall-clock time consumed by the action, in milliseconds.
    message : str
        Human-readable description of the outcome.
    section_data : Any
        The section data produced or retrieved during this step.
    """

    step_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    patch: str = ""
    action: str = "replay"
    success: bool = True
    duration_ms: float = 0.0
    message: str = ""
    section_data: Any = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "patch": self.patch,
            "action": self.action,
            "success": self.success,
            "duration_ms": self.duration_ms,
            "message": self.message,
        }


@dataclass
class ReconciliationResult:
    """Summary of an overlap reconciliation attempt.

    Attributes
    ----------
    pair : tuple[str, str]
        The pair of patch names whose overlap was checked.
    compatible : bool
        Whether the overlap is compatible.
    action_taken : str
        Description of what the reconciler did (e.g. ``"none"``, ``"auto_fixed"``).
    details : dict[str, Any]
        Additional details, including hash, distance, and suggested fixes.
    """

    pair: tuple[str, str] = ("", "")
    compatible: bool = True
    action_taken: str = "none"
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "pair": list(self.pair),
            "compatible": self.compatible,
            "action_taken": self.action_taken,
            "details": self.details,
        }


# ---------------------------------------------------------------------------
# GluingSnapshot
# ---------------------------------------------------------------------------


@dataclass
class GluingSnapshot:
    """A point-in-time record of a gluing state.

    Because the dataclass is mutable (``frozen=False``) callers should treat
    instances as logically immutable after creation.

    Attributes
    ----------
    snapshot_id : str
        Unique identifier, auto-generated.
    patch_sections : dict[str, Any]
        Mapping from patch name → section data.
    overlap_conditions : dict[tuple[str, str], Any]
        Mapping from patch-pair → overlap condition data.
    treaties : dict[str, Any]
        Mapping from overlap key string → treaty data.
    timestamp : float
        Unix timestamp at creation.
    """

    snapshot_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    patch_sections: dict[str, Any] = field(default_factory=dict)
    overlap_conditions: dict[tuple[str, str], Any] = field(default_factory=dict)
    treaties: dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)

    # ------------------------------------------------------------------
    # Accessors
    # ------------------------------------------------------------------

    def get_section(self, patch: str) -> Any | None:
        """Return the section data for *patch*, or ``None`` if absent."""
        return self.patch_sections.get(patch)

    def get_overlap(self, pair: tuple[str, str]) -> Any | None:
        """Return the overlap condition for *pair*, trying both orderings."""
        result = self.overlap_conditions.get(pair)
        if result is None:
            result = self.overlap_conditions.get((pair[1], pair[0]))
        return result

    # ------------------------------------------------------------------
    # Diff / merge utilities
    # ------------------------------------------------------------------

    def diff_from(self, other: GluingSnapshot) -> dict[str, Any]:
        """Compute the difference between *self* and *other*.

        Returns a dict with keys:

        * ``added_patches``    – in self but not in other
        * ``removed_patches``  – in other but not in self
        * ``modified_patches`` – present in both but with different values
        * ``added_overlaps``   – in self.overlap_conditions but not in other
        * ``removed_overlaps`` – in other.overlap_conditions but not in self
        """
        self_patches = set(self.patch_sections)
        other_patches = set(other.patch_sections)

        added_patches = sorted(self_patches - other_patches)
        removed_patches = sorted(other_patches - self_patches)
        modified_patches = sorted(
            p for p in self_patches & other_patches
            if self.patch_sections[p] != other.patch_sections[p]
        )

        # Normalise overlap keys to frozensets for comparison.
        def _norm_keys(d: dict) -> set[frozenset]:
            return {frozenset(k) for k in d}

        self_ov = _norm_keys(self.overlap_conditions)
        other_ov = _norm_keys(other.overlap_conditions)

        added_overlaps = [sorted(k) for k in (self_ov - other_ov)]
        removed_overlaps = [sorted(k) for k in (other_ov - self_ov)]

        return {
            "added_patches": added_patches,
            "removed_patches": removed_patches,
            "modified_patches": modified_patches,
            "added_overlaps": added_overlaps,
            "removed_overlaps": removed_overlaps,
        }

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def restore(self) -> dict[str, Any]:
        """Return a dict suitable for use as ``prior_gluing`` in a replay."""
        overlap_conditions: dict[str, Any] = {}
        for key, value in self.overlap_conditions.items():
            if isinstance(key, tuple) and len(key) == 2:
                overlap_key = f"{key[0]}::{key[1]}"
            else:
                overlap_key = str(key)
            overlap_conditions[overlap_key] = value
        return {
            _SECTION_KEY: dict(self.patch_sections),
            _OVERLAP_KEY: overlap_conditions,
            _TREATY_KEY: dict(self.treaties),
            "snapshot_id": self.snapshot_id,
            "timestamp": self.timestamp,
            "version": REPLAY_SECTION_VERSION,
        }

    def to_dict(self) -> dict[str, Any]:
        overlap_conditions: dict[str, Any] = {}
        for key, value in self.overlap_conditions.items():
            if isinstance(key, tuple) and len(key) == 2:
                overlap_key = f"{key[0]}::{key[1]}"
            else:
                overlap_key = str(key)
            overlap_conditions[overlap_key] = value
        return {
            "snapshot_id": self.snapshot_id,
            "patch_sections": self.patch_sections,
            "overlap_conditions": overlap_conditions,
            "treaties": self.treaties,
            "timestamp": self.timestamp,
            "version": REPLAY_SECTION_VERSION,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> GluingSnapshot:
        """Reconstruct a snapshot from a dict produced by :meth:`to_dict`."""
        raw_overlaps: dict[str, Any] = d.get("overlap_conditions", {})
        parsed_overlaps: dict[tuple[str, str], Any] = {}
        for key, val in raw_overlaps.items():
            if "::" in key:
                a, b = key.split("::", 1)
                parsed_overlaps[(a, b)] = val
            else:
                parsed_overlaps[(key, "")] = val

        return cls(
            snapshot_id=d.get("snapshot_id", str(uuid.uuid4())),
            patch_sections=dict(d.get("patch_sections", {})),
            overlap_conditions=parsed_overlaps,
            treaties=dict(d.get("treaties", {})),
            timestamp=float(d.get("timestamp", time.time())),
        )


# ---------------------------------------------------------------------------
# ReplayCache
# ---------------------------------------------------------------------------


class ReplayCache:
    """Least-recently-used cache for patch section results.

    Parameters
    ----------
    max_size : int
        Maximum number of entries before eviction begins.
    """

    def __init__(self, max_size: int = DEFAULT_CACHE_SIZE) -> None:
        self.max_size = max_size
        self._cache: dict[str, Any] = {}
        self._hits: int = 0
        self._misses: int = 0
        self._access_order: list[str] = []

    # ------------------------------------------------------------------
    # Core operations
    # ------------------------------------------------------------------

    def store(self, patch: str, result: Any) -> None:
        """Store *result* under *patch*.  Evicts the least-recently-used
        entry if the cache has reached :attr:`max_size`."""
        if patch in self._cache:
            self._access_order.remove(patch)
        elif len(self._cache) >= self.max_size:
            lru = self._access_order.pop(0)
            del self._cache[lru]
            logger.debug("ReplayCache: evicted %r (LRU)", lru)

        self._cache[patch] = result
        self._access_order.append(patch)

    def lookup(self, patch: str) -> Any | None:
        """Return the cached result for *patch*, updating access order.

        Returns ``None`` on a cache miss.
        """
        if patch in self._cache:
            self._hits += 1
            self._access_order.remove(patch)
            self._access_order.append(patch)
            return self._cache[patch]
        self._misses += 1
        return None

    def invalidate(self, patch: str) -> None:
        """Remove the entry for *patch*, if present."""
        if patch in self._cache:
            del self._cache[patch]
            if patch in self._access_order:
                self._access_order.remove(patch)

    def invalidate_all(self, patches: Iterable[str]) -> None:
        """Invalidate every patch in *patches*."""
        for patch in patches:
            self.invalidate(patch)

    # ------------------------------------------------------------------
    # Statistics
    # ------------------------------------------------------------------

    def get_hit_rate(self) -> float:
        """Return the fraction of lookups that were cache hits."""
        total = self._hits + self._misses
        return self._hits / total if total > 0 else 0.0

    def get_stats(self) -> dict[str, Any]:
        """Return a statistics dictionary."""
        return {
            "cache_size": len(self._cache),
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate": self.get_hit_rate(),
            "max_size": self.max_size,
        }

    def clear(self) -> None:
        """Remove all entries and reset statistics."""
        self._cache.clear()
        self._access_order.clear()
        self._hits = 0
        self._misses = 0


# ---------------------------------------------------------------------------
# OverlapReconciler
# ---------------------------------------------------------------------------


class OverlapReconciler:
    """Verifies and optionally repairs inter-patch overlap conditions.

    The reconciler operates on plain Python dicts because the concrete jugeo
    geometry types may not be available.  When HAS_JUGEO_DEPS is True the
    ``_extract_section`` method attempts to unwrap richer objects.
    """

    def __init__(self) -> None:
        self._reconciliation_log: list[ReconciliationResult] = []

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def reconcile(
        self,
        changed_patch: str,
        neighbors: list[str],
        context: dict[str, Any],
    ) -> dict[str, Any]:
        """Check *changed_patch* against each of its *neighbors*.

        Returns a dict mapping ``(patch, neighbor)`` string key → result dict
        with keys ``compatible`` (bool) and ``action`` (str).
        """
        results: dict[str, Any] = {}
        s1 = self._extract_section(changed_patch, context)

        for neighbor in neighbors:
            s2 = self._extract_section(neighbor, context)
            compatible = self.check_compatibility(s1, s2)
            action = "none" if compatible else self.suggest_fix(
                {"patch": changed_patch, "neighbor": neighbor, "s1": s1, "s2": s2}
            )
            key = f"{changed_patch}::{neighbor}"
            results[key] = {"compatible": compatible, "action": action}

            rec = ReconciliationResult(
                pair=(changed_patch, neighbor),
                compatible=compatible,
                action_taken=action,
                details={
                    "hash": self.compute_overlap_hash(s1, s2),
                    "s1_type": type(s1).__name__,
                    "s2_type": type(s2).__name__,
                },
            )
            self._reconciliation_log.append(rec)

        return results

    def check_compatibility(self, s1: Any, s2: Any) -> bool:
        """Return ``True`` if section representations *s1* and *s2* are compatible.

        Compatibility rules (in order):
        1. If either is ``None``, they are trivially compatible (missing data).
        2. If both are dicts: compatible when they share no key whose values
           contradict each other (same key → same value, or one is absent).
        3. If both have a ``"value"`` key, compare those values numerically
           with a small tolerance.
        4. Otherwise fall back to ``repr`` equality.
        """
        if s1 is None or s2 is None:
            return True

        if isinstance(s1, dict) and isinstance(s2, dict):
            # Check for value key first.
            if "value" in s1 and "value" in s2:
                v1 = s1["value"]
                v2 = s2["value"]
                if isinstance(v1, (int, float)) and isinstance(v2, (int, float)):
                    return abs(float(v1) - float(v2)) < 1e-9
                return v1 == v2
            # Check shared keys.
            for key in set(s1) & set(s2):
                if s1[key] != s2[key]:
                    return False
            return True

        return repr(s1) == repr(s2)

    def suggest_fix(self, incompatibility: dict[str, Any]) -> str:
        """Return a human-readable suggestion for resolving an incompatibility."""
        patch = incompatibility.get("patch", "?")
        neighbor = incompatibility.get("neighbor", "?")
        s1 = incompatibility.get("s1")
        s2 = incompatibility.get("s2")

        if s1 is None:
            return f"Re-compute section for patch {patch!r} (missing data)"
        if s2 is None:
            return f"Re-compute section for patch {neighbor!r} (missing data)"
        if isinstance(s1, dict) and isinstance(s2, dict):
            conflicting = [k for k in set(s1) & set(s2) if s1[k] != s2[k]]
            if conflicting:
                return (
                    f"Resolve conflicting keys {conflicting} between "
                    f"{patch!r} and {neighbor!r} by re-running descent"
                )
        return f"Re-run overlap resolution between {patch!r} and {neighbor!r}"

    def _extract_section(self, patch: str, context: dict[str, Any]) -> Any:
        """Extract the section data for *patch* from *context*."""
        sections = context.get(_SECTION_KEY, {})
        if isinstance(sections, dict):
            return sections.get(patch)
        return None

    def compute_overlap_hash(self, s1: Any, s2: Any) -> str:
        """Return a short SHA-256 hex digest of the combined section representations."""
        combined = repr(s1) + "||" + repr(s2)
        return hashlib.sha256(combined.encode()).hexdigest()[:16]

    def get_log(self) -> list[ReconciliationResult]:
        """Return the accumulated reconciliation log."""
        return list(self._reconciliation_log)


# ---------------------------------------------------------------------------
# IncrementalReplayer
# ---------------------------------------------------------------------------


class IncrementalReplayer:
    """Orchestrates an incremental replay session.

    Parameters
    ----------
    cache : ReplayCache | None
        If ``None``, a new cache of size :data:`DEFAULT_CACHE_SIZE` is created.
    reconciler : OverlapReconciler | None
        If ``None``, a new reconciler is created.
    """

    def __init__(
        self,
        cache: ReplayCache | None = None,
        reconciler: OverlapReconciler | None = None,
    ) -> None:
        self.cache: ReplayCache = cache if cache is not None else ReplayCache()
        self.reconciler: OverlapReconciler = (
            reconciler if reconciler is not None else OverlapReconciler()
        )
        self._steps: list[ReplayStep] = []
        self._replayed: int = 0
        self._skipped: int = 0
        self._reconciled: int = 0
        self._failed: int = 0

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    def replay(
        self,
        plan: Any,
        prior_gluing: dict[str, Any],
    ) -> Any:
        """Execute an incremental replay.

        Parameters
        ----------
        plan : ReplayGluingPlan
            The replay plan describing which patches changed.
        prior_gluing : dict[str, Any]
            The gluing state from the previous round (used for unchanged patches).

        Returns
        -------
        GluingUnderReplay
            The updated gluing object after the replay.
        """
        logger.info("IncrementalReplayer.replay: starting")
        context = self._build_context(plan, prior_gluing)
        overlap_pairs = self._extract_overlap_pairs(plan, prior_gluing)

        # Determine which patches changed vs. stayed the same.
        all_patches = _get_plan_patches(plan)
        changed_patches = _get_changed_patches(plan)
        unchanged_patches = [p for p in all_patches if p not in changed_patches]

        new_sections: dict[str, Any] = {}

        # --- replay changed patches ---
        for patch in changed_patches:
            t0 = time.monotonic()
            try:
                section = self._replay_changed_patch(patch, context)
                new_sections[patch] = section
                duration = (time.monotonic() - t0) * 1000
                step = ReplayStep(
                    patch=patch,
                    action="replay",
                    success=True,
                    duration_ms=duration,
                    message="replayed",
                    section_data=section,
                )
                self._steps.append(step)
                self._replayed += 1
            except Exception as exc:
                duration = (time.monotonic() - t0) * 1000
                self._failed += 1
                step = ReplayStep(
                    patch=patch,
                    action="replay",
                    success=False,
                    duration_ms=duration,
                    message=str(exc),
                )
                self._steps.append(step)
                logger.warning("Replay failed for patch %r: %s", patch, exc)
                raise ReplayError(f"Failed to replay patch {patch!r}: {exc}") from exc

        # --- skip unchanged patches ---
        for patch in unchanged_patches:
            t0 = time.monotonic()
            reason = self._skip_unchanged_patch(patch)
            cached = self.cache.lookup(patch)
            if cached is not None:
                new_sections[patch] = cached
            else:
                prior_sec = prior_gluing.get(_SECTION_KEY, {}).get(patch)
                new_sections[patch] = prior_sec
            duration = (time.monotonic() - t0) * 1000
            self._steps.append(
                ReplayStep(
                    patch=patch,
                    action="skip",
                    success=True,
                    duration_ms=duration,
                    message=reason,
                    section_data=new_sections.get(patch),
                )
            )
            self._skipped += 1

        # Update context with freshly computed sections for reconciliation.
        context[_SECTION_KEY] = new_sections

        # --- reconcile overlaps ---
        new_overlaps: dict[str, Any] = {}
        incompatibilities: list[tuple[str, str]] = []

        for pair in overlap_pairs:
            compatible = self._reconcile_overlap(pair, context, context)
            if compatible:
                overlap_key = f"{pair[0]}::{pair[1]}"
                new_overlaps[overlap_key] = _build_overlap_data(
                    pair, new_sections
                )
                self._reconciled += 1
            else:
                incompatibilities.append(pair)

        if incompatibilities:
            first = incompatibilities[0]
            raise OverlapIncompatibilityError(
                first,
                f"{len(incompatibilities)} overlap incompatibility(ies) detected; "
                f"first: {first}",
            )

        result_dict = {
            _SECTION_KEY: new_sections,
            _OVERLAP_KEY: new_overlaps,
            "replayed_patches": changed_patches,
            "skipped_patches": unchanged_patches,
            "plan_id": _get_plan_id(plan),
        }

        if HAS_MODELS:
            return _build_gluing_under_replay(result_dict, plan)
        return result_dict

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _replay_changed_patch(self, patch: str, context: dict[str, Any]) -> Any:
        """Re-compute a single changed patch section.

        Checks the cache first; if a cache entry exists for the *exact* patch
        key with the same context hash it is returned without re-computation.
        """
        ctx_hash = _hash_context(patch, context)
        cache_key = f"{patch}@{ctx_hash}"
        cached = self.cache.lookup(cache_key)
        if cached is not None:
            logger.debug("_replay_changed_patch: cache hit for %r", patch)
            return cached

        section = _execute_section_replay(patch, context)
        self.cache.store(cache_key, section)
        return section

    def _skip_unchanged_patch(self, patch: str) -> str:
        """Return the reason an unchanged patch is being skipped."""
        if self.cache.lookup(patch) is not None:
            return "cache_hit"
        return "unchanged"

    def _reconcile_overlap(
        self,
        pair: tuple[str, str],
        gluing: Any,
        context: dict[str, Any],
    ) -> bool:
        """Check compatibility of a single overlap pair.

        Returns ``True`` if the two patches are compatible at their shared boundary.
        """
        sections = context.get(_SECTION_KEY, {})
        s1 = sections.get(pair[0])
        s2 = sections.get(pair[1])
        return self.reconciler.check_compatibility(s1, s2)

    def _build_context(
        self,
        plan: Any,
        prior_gluing: dict[str, Any],
    ) -> dict[str, Any]:
        """Assemble the execution context from plan metadata and prior state."""
        ctx: dict[str, Any] = {}
        ctx["plan_id"] = _get_plan_id(plan)
        ctx["strategy"] = _get_plan_strategy(plan)
        ctx[_SECTION_KEY] = dict(prior_gluing.get(_SECTION_KEY, {}))
        ctx[_OVERLAP_KEY] = dict(prior_gluing.get(_OVERLAP_KEY, {}))
        ctx[_TREATY_KEY] = dict(prior_gluing.get(_TREATY_KEY, {}))
        ctx["version"] = prior_gluing.get("version", REPLAY_SECTION_VERSION)
        return ctx

    def _extract_overlap_pairs(
        self,
        plan: Any,
        prior_gluing: dict[str, Any],
    ) -> list[tuple[str, str]]:
        """Build the list of overlap pairs that must be reconciled.

        The list is the union of:
        - pairs declared in the plan's overlap adjacency (if available), and
        - pairs inferred from the prior gluing's overlap keys.
        """
        pairs: list[tuple[str, str]] = []

        # From plan
        if hasattr(plan, "overlap_pairs"):
            for pair in plan.overlap_pairs:
                if isinstance(pair, (list, tuple)) and len(pair) == 2:
                    pairs.append((str(pair[0]), str(pair[1])))

        # From prior gluing
        for key in prior_gluing.get(_OVERLAP_KEY, {}):
            if "::" in key:
                a, b = key.split("::", 1)
                pair = (a, b)
                if pair not in pairs:
                    pairs.append(pair)

        return pairs

    # ------------------------------------------------------------------
    # Statistics
    # ------------------------------------------------------------------

    def get_statistics(self) -> dict[str, Any]:
        """Return a summary of replay activity."""
        return {
            "replayed": self._replayed,
            "skipped": self._skipped,
            "reconciled": self._reconciled,
            "failed": self._failed,
            "total_steps": len(self._steps),
            "cache_stats": self.cache.get_stats(),
        }

    def get_steps(self) -> list[ReplayStep]:
        """Return the ordered list of replay steps taken so far."""
        return list(self._steps)


# ---------------------------------------------------------------------------
# Module-level helper functions
# ---------------------------------------------------------------------------


def create_snapshot_from_gluing(gluing: Any) -> GluingSnapshot:
    """Build a :class:`GluingSnapshot` from a GluingUnderReplay (or dict).

    Parameters
    ----------
    gluing : GluingUnderReplay | dict
        The gluing state to snapshot.
    """
    if isinstance(gluing, dict):
        patch_sections = dict(gluing.get(_SECTION_KEY, {}))
        raw_overlaps: dict[str, Any] = gluing.get(_OVERLAP_KEY, {})
        treaties: dict[str, Any] = gluing.get(_TREATY_KEY, {})
    else:
        patch_sections = {}
        raw_overlaps = {}
        treaties = {}
        if hasattr(gluing, "patch_sections"):
            patch_sections = dict(getattr(gluing, "patch_sections", {}))
        if hasattr(gluing, "overlap_conditions"):
            raw_overlaps = {
                f"{a}::{b}": v
                for (a, b), v in getattr(gluing, "overlap_conditions", {}).items()
            }
        if hasattr(gluing, "treaties"):
            treaties = dict(getattr(gluing, "treaties", {}))

    parsed_overlaps: dict[tuple[str, str], Any] = {}
    for key, val in raw_overlaps.items():
        if "::" in key:
            a, b = key.split("::", 1)
            parsed_overlaps[(a, b)] = val

    return GluingSnapshot(
        patch_sections=patch_sections,
        overlap_conditions=parsed_overlaps,
        treaties=treaties,
    )


def restore_snapshot(snapshot: GluingSnapshot) -> dict[str, Any]:
    """Convert a :class:`GluingSnapshot` back into a ``prior_gluing`` dict."""
    return snapshot.restore()


def compute_incremental_delta(
    snapshot: GluingSnapshot,
    change_set_dict: dict[str, Any],
) -> dict[str, Any]:
    """Compute the incremental delta needed to go from *snapshot* to *change_set_dict*.

    Parameters
    ----------
    snapshot : GluingSnapshot
        The baseline snapshot.
    change_set_dict : dict[str, Any]
        A partial dict (``{patch_name: new_section_data}``).

    Returns
    -------
    dict[str, Any]
        A dict with keys ``changed``, ``added``, ``removed``, ``unchanged``.
    """
    baseline = snapshot.patch_sections
    changed: list[str] = []
    added: list[str] = []
    removed: list[str] = []
    unchanged: list[str] = []

    for patch, new_data in change_set_dict.items():
        if patch not in baseline:
            added.append(patch)
        elif baseline[patch] != new_data:
            changed.append(patch)
        else:
            unchanged.append(patch)

    for patch in baseline:
        if patch not in change_set_dict:
            removed.append(patch)

    return {
        "changed": sorted(changed),
        "added": sorted(added),
        "removed": sorted(removed),
        "unchanged": sorted(unchanged),
        "total_patches": len(set(list(baseline) + list(change_set_dict))),
    }


def merge_snapshots(s1: GluingSnapshot, s2: GluingSnapshot) -> GluingSnapshot:
    """Produce a new snapshot combining *s1* and *s2*.

    *s2* takes precedence for any overlapping patch or overlap key.
    """
    merged_sections = {**s1.patch_sections, **s2.patch_sections}
    merged_overlaps = {**s1.overlap_conditions, **s2.overlap_conditions}
    merged_treaties = {**s1.treaties, **s2.treaties}

    return GluingSnapshot(
        patch_sections=merged_sections,
        overlap_conditions=merged_overlaps,
        treaties=merged_treaties,
    )


def validate_incremental_gluing(ig: Any) -> list[str]:
    """Run basic sanity checks on an IncrementalGluing object.

    Returns a (possibly empty) list of error strings.
    """
    errors: list[str] = []

    if ig is None:
        return ["IncrementalGluing is None"]

    if HAS_MODELS and isinstance(ig, IncrementalGluing):
        if not getattr(ig, "gluing_id", None):
            errors.append("gluing_id is empty or None")
        rounds = getattr(ig, "rounds_completed", -1)
        if rounds < 0:
            errors.append(f"rounds_completed is negative: {rounds}")
        patches = getattr(ig, "patches", [])
        if not patches:
            errors.append("no patches in IncrementalGluing")
        sections = getattr(ig, "patch_sections", {})
        for patch in patches:
            if patch not in sections:
                errors.append(f"patch {patch!r} has no section data")
    else:
        # Duck-typed validation
        if not getattr(ig, "gluing_id", None):
            errors.append("gluing_id is empty or None")

    return errors


# ---------------------------------------------------------------------------
# Private helper functions (module-level)
# ---------------------------------------------------------------------------


def _get_plan_patches(plan: Any) -> list[str]:
    """Return all patch names referenced by *plan*."""
    if hasattr(plan, "patches"):
        return list(getattr(plan, "patches", []))
    if hasattr(plan, "all_patches"):
        return list(getattr(plan, "all_patches", []))
    if isinstance(plan, dict):
        return list(plan.get("patches", []))
    return []


def _get_changed_patches(plan: Any) -> list[str]:
    """Return the list of patches that changed according to *plan*."""
    if hasattr(plan, "changed_patches"):
        return list(getattr(plan, "changed_patches", []))
    if isinstance(plan, dict):
        return list(plan.get("changed_patches", []))
    return []


def _get_plan_id(plan: Any) -> str:
    """Extract a plan identifier string."""
    if hasattr(plan, "plan_id"):
        return str(getattr(plan, "plan_id", ""))
    if isinstance(plan, dict):
        return str(plan.get("plan_id", ""))
    return ""


def _get_plan_strategy(plan: Any) -> str:
    """Extract the replay strategy string from *plan*."""
    if hasattr(plan, "strategy"):
        strategy = getattr(plan, "strategy")
        return str(strategy.value) if hasattr(strategy, "value") else str(strategy)
    if isinstance(plan, dict):
        return str(plan.get("strategy", "incremental"))
    return "incremental"


def _hash_context(patch: str, context: dict[str, Any]) -> str:
    """Compute a short hash of the patch-relevant portion of *context*."""
    relevant = {
        "patch": patch,
        "strategy": context.get("strategy", ""),
        "version": context.get("version", REPLAY_SECTION_VERSION),
    }
    raw = repr(sorted(relevant.items()))
    return hashlib.md5(raw.encode()).hexdigest()[:8]  # noqa: S324


def _execute_section_replay(patch: str, context: dict[str, Any]) -> dict[str, Any]:
    """Low-level section re-computation for *patch*.

    When HAS_JUGEO_DEPS is True this would invoke the DescentEngine; in the
    fallback path it synthesises deterministic placeholder data based on the
    patch name and context hash.
    """
    ctx_hash = _hash_context(patch, context)
    if HAS_JUGEO_DEPS:
        try:
            engine = DescentEngine()
            result = engine.compute_section(patch, context)
            return {"value": result, "patch": patch, "version": REPLAY_SECTION_VERSION}
        except Exception:
            pass

    # Fallback: deterministic synthetic section.
    seed = int(hashlib.sha256(f"{patch}:{ctx_hash}".encode()).hexdigest()[:8], 16)
    value = (seed % 10000) / 10000.0
    return {
        "patch": patch,
        "value": value,
        "ctx_hash": ctx_hash,
        "version": REPLAY_SECTION_VERSION,
        "synthetic": True,
    }


def _build_overlap_data(pair: tuple[str, str], sections: dict[str, Any]) -> dict[str, Any]:
    """Build the overlap data dict for a compatible patch pair."""
    s1 = sections.get(pair[0], {})
    s2 = sections.get(pair[1], {})
    return {
        "patches": list(pair),
        "compatible": True,
        "hash": hashlib.md5(
            (repr(s1) + repr(s2)).encode()
        ).hexdigest()[:12],  # noqa: S324
    }


def _build_gluing_under_replay(result_dict: dict[str, Any], plan: Any) -> Any:
    """Attempt to construct a GluingUnderReplay from *result_dict*."""
    try:
        return GluingUnderReplay(  # type: ignore[call-arg]
            plan_id=result_dict.get("plan_id", ""),
            patch_sections=result_dict.get(_SECTION_KEY, {}),
            overlap_conditions=result_dict.get(_OVERLAP_KEY, {}),
            replayed_patches=result_dict.get("replayed_patches", []),
            skipped_patches=result_dict.get("skipped_patches", []),
            phase=ReplayPhase.COMPLETED,  # type: ignore[name-defined]
        )
    except Exception:
        return result_dict


def _parse_overlap_key(key: str) -> tuple[str, str] | None:
    """Parse a ``"patch_a::patch_b"`` string into a tuple, or return ``None``."""
    if "::" in key:
        a, b = key.split("::", 1)
        return (a, b)
    return None


def _section_repr_hash(section: Any) -> str:
    """Return a hex digest of the repr of *section*."""
    return hashlib.sha256(repr(section).encode()).hexdigest()[:16]


def _patches_are_adjacent(p1: str, p2: str, adjacency: dict[str, list[str]]) -> bool:
    """Return True if *p1* and *p2* are listed as adjacent in *adjacency*."""
    return p2 in adjacency.get(p1, []) or p1 in adjacency.get(p2, [])


def _sections_distance(s1: Any, s2: Any) -> float:
    """Compute a normalised [0,1] distance between two section representations."""
    if s1 is None and s2 is None:
        return 0.0
    if s1 is None or s2 is None:
        return 1.0
    if isinstance(s1, dict) and isinstance(s2, dict):
        all_keys = set(s1) | set(s2)
        if not all_keys:
            return 0.0
        differences = sum(1 for k in all_keys if s1.get(k) != s2.get(k))
        return differences / len(all_keys)
    return 0.0 if repr(s1) == repr(s2) else 1.0


def _plan_has_full_replay(plan: Any) -> bool:
    """Return True when the plan requests a full (non-incremental) replay."""
    strategy = _get_plan_strategy(plan)
    return strategy in ("full", "FULL", "full_replay")


def _format_step_summary(steps: list[ReplayStep]) -> str:
    """Produce a short human-readable summary of replay steps."""
    replayed = sum(1 for s in steps if s.action == "replay" and s.success)
    skipped = sum(1 for s in steps if s.action == "skip")
    failed = sum(1 for s in steps if not s.success)
    total_ms = sum(s.duration_ms for s in steps)
    return (
        f"replayed={replayed} skipped={skipped} failed={failed} "
        f"total_ms={total_ms:.1f}"
    )


def _assert_no_duplicate_patches(patches: list[str]) -> None:
    """Raise ValueError if *patches* contains duplicates."""
    seen: set[str] = set()
    for p in patches:
        if p in seen:
            raise ValueError(f"Duplicate patch name: {p!r}")
        seen.add(p)


def _coerce_to_section_dict(raw: Any) -> dict[str, Any]:
    """Attempt to coerce *raw* to a section dict; return empty dict on failure."""
    if isinstance(raw, dict):
        return raw
    if raw is None:
        return {}
    return {"raw": repr(raw), "version": REPLAY_SECTION_VERSION}


def _annotate_steps_with_index(steps: list[ReplayStep]) -> list[ReplayStep]:
    """Return a copy of *steps* with step_ids replaced by sequential indices."""
    annotated = []
    for i, step in enumerate(steps):
        new_step = ReplayStep(
            step_id=str(i),
            patch=step.patch,
            action=step.action,
            success=step.success,
            duration_ms=step.duration_ms,
            message=step.message,
            section_data=step.section_data,
        )
        annotated.append(new_step)
    return annotated


def build_replayer_from_config(config: dict[str, Any]) -> IncrementalReplayer:
    """Construct an :class:`IncrementalReplayer` from a configuration dict.

    Recognised keys
    ---------------
    cache_size : int
        Maximum cache size (default :data:`DEFAULT_CACHE_SIZE`).
    """
    cache_size = int(config.get("cache_size", DEFAULT_CACHE_SIZE))
    cache = ReplayCache(max_size=cache_size)
    reconciler = OverlapReconciler()
    return IncrementalReplayer(cache=cache, reconciler=reconciler)


def summarise_replay_result(result: Any) -> dict[str, Any]:
    """Extract a lightweight summary dict from a replay result."""
    if isinstance(result, dict):
        sections = result.get(_SECTION_KEY, {})
        overlaps = result.get(_OVERLAP_KEY, {})
    else:
        sections = getattr(result, "patch_sections", {})
        overlaps = getattr(result, "overlap_conditions", {})
    return {
        "patch_count": len(sections),
        "overlap_count": len(overlaps),
        "replayed": len(result.get("replayed_patches", []) if isinstance(result, dict) else []),
    }


# ---------------------------------------------------------------------------
# Convenience constructors
# ---------------------------------------------------------------------------


def make_empty_snapshot(patch_names: list[str] | None = None) -> GluingSnapshot:
    """Create a blank snapshot optionally pre-populated with *patch_names*."""
    sections: dict[str, Any] = {p: None for p in (patch_names or [])}
    return GluingSnapshot(patch_sections=sections)


def make_replay_cache(max_size: int = DEFAULT_CACHE_SIZE) -> ReplayCache:
    """Create a fresh :class:`ReplayCache` with *max_size* entries."""
    return ReplayCache(max_size=max_size)


# ---------------------------------------------------------------------------
# Snapshot round-trip utilities
# ---------------------------------------------------------------------------


def snapshot_round_trip(data: dict[str, Any]) -> dict[str, Any]:
    """Serialise and deserialise *data* through :class:`GluingSnapshot`.

    Useful as a normalisation step to ensure keys are consistently formatted.
    """
    snap = GluingSnapshot.from_dict(data)
    return snap.to_dict()


def diff_gluings(
    old: dict[str, Any],
    new: dict[str, Any],
) -> dict[str, Any]:
    """Compute the difference between two raw gluing dicts.

    Returns a dict with keys: ``changed_patches``, ``added_patches``,
    ``removed_patches``.
    """
    old_sections: dict[str, Any] = old.get(_SECTION_KEY, {})
    new_sections: dict[str, Any] = new.get(_SECTION_KEY, {})

    old_keys = set(old_sections)
    new_keys = set(new_sections)

    changed = [k for k in old_keys & new_keys if old_sections[k] != new_sections[k]]
    added = sorted(new_keys - old_keys)
    removed = sorted(old_keys - new_keys)

    return {
        "changed_patches": sorted(changed),
        "added_patches": added,
        "removed_patches": removed,
    }


def estimate_replay_cost(plan: Any, cache: ReplayCache) -> dict[str, Any]:
    """Estimate the computation cost of executing *plan*.

    Returns a dict with ``estimated_replays``, ``estimated_skips``,
    ``cache_hit_rate``.
    """
    all_patches = _get_plan_patches(plan)
    changed = _get_changed_patches(plan)
    unchanged = [p for p in all_patches if p not in changed]

    # Count how many unchanged patches are already in cache.
    cache_hits = sum(1 for p in unchanged if cache.lookup(p) is not None)

    return {
        "estimated_replays": len(changed),
        "estimated_skips": len(unchanged),
        "cache_hit_rate": cache_hits / max(len(unchanged), 1),
        "total_patches": len(all_patches),
    }


def log_replay_summary(
    replayer: IncrementalReplayer,
    logger: logging.Logger | None = None,
) -> None:
    """Emit an INFO-level summary of *replayer* statistics."""
    lg = logger or logging.getLogger(__name__)
    stats = replayer.get_statistics()
    steps = replayer.get_steps()
    lg.info(
        "Replay summary: %s | cache=%s",
        _format_step_summary(steps),
        stats.get("cache_stats", {}),
    )


# ---------------------------------------------------------------------------
# Module self-test (run with: python -m jugeo.generation.replay_gluing.incremental_replay)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)

    # Quick smoke test.
    cache = ReplayCache(max_size=10)
    cache.store("p1", {"value": 0.5})
    assert cache.lookup("p1") == {"value": 0.5}
    assert cache.get_hit_rate() == 1.0

    reconciler = OverlapReconciler()
    ctx = {_SECTION_KEY: {"p1": {"value": 0.5}, "p2": {"value": 0.5}}}
    result = reconciler.reconcile("p1", ["p2"], ctx)
    assert result["p1::p2"]["compatible"] is True

    snap = GluingSnapshot(patch_sections={"p1": {"value": 1}})
    assert snap.get_section("p1") == {"value": 1}
    assert snap.get_section("p99") is None

    d = snap.to_dict()
    snap2 = GluingSnapshot.from_dict(d)
    assert snap2.patch_sections == snap.patch_sections

    delta = compute_incremental_delta(snap, {"p1": {"value": 2}, "p3": {"value": 3}})
    assert "p1" in delta["changed"]
    assert "p3" in delta["added"]

    merged = merge_snapshots(snap, snap2)
    assert merged.patch_sections == snap.patch_sections

    print("incremental_replay: smoke tests passed ✓")
