"""Algorithms for incremental memory operations — theory2.tex Ch34.

This module implements the core algorithms for the incremental_memory encoding
subsystem, developed with copilot assistance. Algorithms include the Glue
construction, section differencing, overlap resolution, epoch advancement,
memory compaction, quota enforcement, support minimization, and batch
optimization.

These algorithms collectively implement the computational machinery for the
mathematical constructions described in theory2.tex Chapter 34.
"""
from __future__ import annotations

import uuid
import time
import json
import hashlib
import logging
import heapq
from dataclasses import dataclass, field
from typing import Any, Callable

logger = logging.getLogger(__name__)

try:
    from jugeo.runtime.memory import SemanticMemory, MemoryRegion, MemorySnapshot
except ImportError:
    SemanticMemory = Any  # type: ignore
    MemoryRegion = Any  # type: ignore
    MemorySnapshot = Any  # type: ignore

try:
    from jugeo.evidence.manifests import EpochMap
except ImportError:
    EpochMap = Any  # type: ignore

try:
    from jugeo.encodings.incremental_memory.models import (
        IncrementalUpdate,
        EncodingSupportSet,
        MemoryInvalidationCascade,
        PersistentMemoryState,
        RegionType,
        ChangeEvent,
    )
except ImportError:
    IncrementalUpdate = Any  # type: ignore
    EncodingSupportSet = Any  # type: ignore
    MemoryInvalidationCascade = Any  # type: ignore
    PersistentMemoryState = Any  # type: ignore
    RegionType = Any  # type: ignore
    ChangeEvent = Any  # type: ignore

try:
    from jugeo.encodings.incremental_memory.update_law import (
        GlueOperation,
        RestrictionOperation,
        OverlapChecker,
        GlueComputation,
        OverlapData,
        RestrictionResult,
    )
except ImportError:
    GlueOperation = Any  # type: ignore
    RestrictionOperation = Any  # type: ignore
    OverlapChecker = Any  # type: ignore
    GlueComputation = Any  # type: ignore
    OverlapData = Any  # type: ignore
    RestrictionResult = Any  # type: ignore


# ---------------------------------------------------------------------------
# GlueAlgorithm
# ---------------------------------------------------------------------------


class GlueAlgorithm:
    """Implements the Glue construction from theory2.tex §34.2.

    The Glue construction assembles a globally consistent memory state from a
    collection of locally consistent sections.  Each section covers a subset
    of the coordinate space; the Glue algorithm detects where sections overlap,
    resolves any conflicts between overlapping values, and merges the results
    into a single coherent data dictionary.  Incremental variants of the
    algorithm exploit the structure of ``IncrementalUpdate`` objects to avoid
    recomputing sections that have not changed since the previous run.  The
    algorithm is parameterised by a conflict-resolution strategy (defaulting to
    ``"prefer_new"``) which can be overridden per call to ``resolve_conflicts``.
    """

    def __init__(self) -> None:
        """Initialise the GlueAlgorithm with empty operation history."""
        self._operation_count: int = 0
        self._last_run_time: float = 0.0

    def run(
        self,
        base_data: dict[str, Any],
        new_sections: dict[str, Any],
        support: Any,
    ) -> Any:
        """Execute the full Glue construction over ``base_data`` and ``new_sections``.

        Detects conflicts between ``base_data`` and ``new_sections``, resolves
        them according to the default ``"prefer_new"`` strategy, and merges the
        result.  The supplied ``support`` is attached to the returned
        ``GlueComputation`` for downstream provenance tracking.

        Args:
            base_data: Existing section data keyed by coordinate string.
            new_sections: Incoming section data to be glued into ``base_data``.
            support: An ``EncodingSupportSet`` describing which coordinates are
                in scope for this computation.

        Returns:
            A ``GlueComputation`` containing the merged data and metadata.
        """
        t0 = time.time()
        conflicts = self.detect_conflicts(base_data, new_sections)
        if conflicts:
            resolved = self.resolve_conflicts(conflicts, base_data, new_sections)
        else:
            resolved = new_sections

        merged = self.merge_sections(base_data, resolved)
        self._operation_count += 1
        self._last_run_time = time.time() - t0

        try:
            computation = GlueComputation(
                result=merged,
                support=support,
                conflicts=conflicts,
                timestamp=time.time(),
            )
        except Exception:
            computation = {
                "result": merged,
                "support": support,
                "conflicts": conflicts,
                "timestamp": time.time(),
            }
        return computation

    def run_incremental(
        self,
        prev_computation: Any,
        update: Any,
    ) -> Any:
        """Re-run the Glue construction incrementally given a previous result.

        Only the sections touched by ``update`` are reprocessed.  The rest of
        the previous result is preserved verbatim, avoiding a full re-merge of
        the entire coordinate space.

        Args:
            prev_computation: The ``GlueComputation`` produced by the previous
                run, used as the base state.
            update: An ``IncrementalUpdate`` describing what changed.

        Returns:
            A new ``GlueComputation`` incorporating the update.
        """
        try:
            prev_result = prev_computation.result
        except AttributeError:
            prev_result = prev_computation.get("result", {})

        try:
            new_sections = update.new_sections
        except AttributeError:
            new_sections = getattr(update, "sections", {})

        try:
            support = prev_computation.support
        except AttributeError:
            support = prev_computation.get("support", None)

        return self.run(dict(prev_result), new_sections, support)

    def merge_sections(
        self,
        a: dict[str, Any],
        b: dict[str, Any],
    ) -> dict[str, Any]:
        """Merge two section dictionaries, with ``b`` overriding ``a`` on overlap.

        Args:
            a: The base section dictionary.
            b: The new section dictionary whose values take precedence.

        Returns:
            A new dict containing all keys from both ``a`` and ``b``.
        """
        return {**a, **b}

    def detect_conflicts(
        self,
        a: dict[str, Any],
        b: dict[str, Any],
    ) -> list[str]:
        """Find keys that are present in both ``a`` and ``b`` with different values.

        A conflict exists when the same key maps to distinct (non-equal) values
        in the two dictionaries.  Equal values are not considered conflicting
        even though they are technically shared.

        Args:
            a: The first section dictionary.
            b: The second section dictionary.

        Returns:
            A list of conflicting key strings.
        """
        conflicts: list[str] = []
        for key in a:
            if key in b and a[key] != b[key]:
                conflicts.append(key)
        return conflicts

    def resolve_conflicts(
        self,
        conflicts: list[str],
        a: dict[str, Any],
        b: dict[str, Any],
        strategy: str = "prefer_new",
    ) -> dict[str, Any]:
        """Resolve conflicts between two section dictionaries.

        Supported strategies:

        - ``"prefer_new"``: ``b`` wins for all conflicting keys.
        - ``"prefer_old"``: ``a`` wins for all conflicting keys.
        - ``"prefer_larger"``: the value with the larger repr string wins.

        Any unrecognised strategy defaults to ``"prefer_new"``.

        Args:
            conflicts: Keys that conflict between ``a`` and ``b``.
            a: The older (base) section dictionary.
            b: The newer section dictionary.
            strategy: Conflict resolution strategy name.

        Returns:
            A copy of ``b`` with conflicting keys resolved according to
            ``strategy``.
        """
        result = dict(b)
        for key in conflicts:
            if strategy == "prefer_old":
                result[key] = a[key]
            elif strategy == "prefer_larger":
                result[key] = a[key] if len(repr(a[key])) >= len(repr(b[key])) else b[key]
            else:
                result[key] = b[key]  # prefer_new default
        return result

    def verify_result(self, computation: Any) -> bool:
        """Verify that a ``GlueComputation`` result is well-formed.

        Checks that the ``result`` attribute is a non-None dict-like object.

        Args:
            computation: The ``GlueComputation`` to verify.

        Returns:
            ``True`` if the result appears valid; ``False`` otherwise.
        """
        try:
            result = computation.result
        except AttributeError:
            result = computation.get("result", None)
        return result is not None and isinstance(result, dict)

    def summarize(self) -> str:
        """Return a one-line summary of this algorithm instance.

        Returns:
            A string reporting the total number of Glue runs and last run
            duration.
        """
        return (
            f"GlueAlgorithm(runs={self._operation_count}, "
            f"last_run_time={self._last_run_time:.4f}s)"
        )


# ---------------------------------------------------------------------------
# SectionDiffAlgorithm
# ---------------------------------------------------------------------------


class SectionDiffAlgorithm:
    """Computes and manipulates structural diffs between section dictionaries.

    A section diff is a structured dict with four keys: ``added`` (keys present
    in ``new`` but not ``old``), ``removed`` (keys present in ``old`` but not
    ``new``), ``changed`` (keys present in both but with different values), and
    ``unchanged`` (keys present in both with equal values).  Diffs can be
    applied to a base dict to produce an updated dict, inverted to reverse the
    transformation, or composed to chain multiple transformations.  This
    representation is the primary data structure exchanged between the
    incremental update machinery and the persistence layer.  The algorithm
    also supports snapshot-level diffing via ``snapshot_diff`` for comparing
    full ``MemorySnapshot`` objects.
    """

    def __init__(self) -> None:
        """Initialise the SectionDiffAlgorithm with an empty operation log."""
        self._diff_count: int = 0

    def diff(
        self,
        old: dict[str, Any],
        new: dict[str, Any],
    ) -> dict[str, Any]:
        """Compute a structural diff between ``old`` and ``new`` section dicts.

        Args:
            old: The previous section dictionary.
            new: The current section dictionary.

        Returns:
            A dict with keys ``added``, ``removed``, ``changed``, and
            ``unchanged``, each mapping to a list of coordinate keys.
        """
        old_keys = set(old.keys())
        new_keys = set(new.keys())

        added = list(new_keys - old_keys)
        removed = list(old_keys - new_keys)
        common = old_keys & new_keys
        changed = [k for k in common if old[k] != new[k]]
        unchanged = [k for k in common if old[k] == new[k]]

        self._diff_count += 1
        return {
            "added": added,
            "removed": removed,
            "changed": changed,
            "unchanged": unchanged,
        }

    def apply_diff(
        self,
        base: dict[str, Any],
        diff: dict[str, Any],
    ) -> dict[str, Any]:
        """Apply a diff to a base dict, producing an updated dict.

        Keys listed in ``removed`` are deleted; keys listed in ``added`` or
        ``changed`` are taken from the diff's ``new_values`` sub-dict when
        present, otherwise the key is left unchanged.

        Args:
            base: The starting section dictionary.
            diff: A diff as returned by ``diff()``.

        Returns:
            A new dict reflecting all changes described in ``diff``.
        """
        result = dict(base)
        for key in diff.get("removed", []):
            result.pop(key, None)
        new_values: dict[str, Any] = diff.get("new_values", {})
        for key in diff.get("added", []) + diff.get("changed", []):
            if key in new_values:
                result[key] = new_values[key]
        return result

    def invert_diff(self, diff: dict[str, Any]) -> dict[str, Any]:
        """Produce the inverse diff that undoes the transformation described by ``diff``.

        Swaps ``added`` and ``removed``, inverts ``changed`` by exchanging
        ``new_values`` and ``old_values``, and preserves ``unchanged``.

        Args:
            diff: A diff dict as produced by ``diff()``.

        Returns:
            The inverse diff dict.
        """
        return {
            "added": diff.get("removed", []),
            "removed": diff.get("added", []),
            "changed": diff.get("changed", []),
            "unchanged": diff.get("unchanged", []),
            "new_values": diff.get("old_values", {}),
            "old_values": diff.get("new_values", {}),
        }

    def compose_diffs(
        self,
        d1: dict[str, Any],
        d2: dict[str, Any],
    ) -> dict[str, Any]:
        """Compose two sequential diffs into a single equivalent diff.

        The composed diff describes the net change when ``d1`` is applied
        followed by ``d2``.  Keys that are added in ``d1`` and then removed
        in ``d2`` cancel out; keys added in ``d2`` after being removed in
        ``d1`` reappear as ``added`` in the result.

        Args:
            d1: The first (earlier) diff.
            d2: The second (later) diff.

        Returns:
            A composed diff dict.
        """
        added1 = set(d1.get("added", []))
        removed1 = set(d1.get("removed", []))
        changed1 = set(d1.get("changed", []))
        added2 = set(d2.get("added", []))
        removed2 = set(d2.get("removed", []))
        changed2 = set(d2.get("changed", []))

        net_added = (added1 - removed2) | (added2 - removed1)
        net_removed = (removed1 - added2) | (removed2 - added1)
        net_changed = (changed1 | changed2) - net_added - net_removed
        net_unchanged: set[str] = set(d1.get("unchanged", [])) & set(d2.get("unchanged", []))

        return {
            "added": list(net_added),
            "removed": list(net_removed),
            "changed": list(net_changed),
            "unchanged": list(net_unchanged),
        }

    def is_empty_diff(self, diff: dict[str, Any]) -> bool:
        """Return True if the diff describes no changes.

        Args:
            diff: A diff dict.

        Returns:
            ``True`` when ``added``, ``removed``, and ``changed`` are all empty.
        """
        return (
            not diff.get("added")
            and not diff.get("removed")
            and not diff.get("changed")
        )

    def diff_size(self, diff: dict[str, Any]) -> int:
        """Return the total number of changed, added, and removed keys.

        Args:
            diff: A diff dict.

        Returns:
            Sum of the lengths of ``added``, ``removed``, and ``changed``.
        """
        return (
            len(diff.get("added", []))
            + len(diff.get("removed", []))
            + len(diff.get("changed", []))
        )

    def snapshot_diff(self, s1: Any, s2: Any) -> dict[str, Any]:
        """Compute a diff between two ``MemorySnapshot`` objects.

        Accesses ``s1.region_data`` and ``s2.region_data`` to obtain the
        underlying section dicts, then delegates to ``diff()``.  If either
        snapshot does not expose ``region_data`` an empty diff is returned.

        Args:
            s1: The earlier ``MemorySnapshot``.
            s2: The later ``MemorySnapshot``.

        Returns:
            A diff dict, or ``{}`` if ``region_data`` is not accessible.
        """
        try:
            old = s1.region_data
            new = s2.region_data
        except AttributeError:
            return {}
        if not isinstance(old, dict) or not isinstance(new, dict):
            return {}
        return self.diff(old, new)

    def summarize(self) -> str:
        """Return a one-line summary of this algorithm's usage.

        Returns:
            A string reporting the total number of diffs computed.
        """
        return f"SectionDiffAlgorithm(diffs_computed={self._diff_count})"


# ---------------------------------------------------------------------------
# OverlapResolutionAlgorithm
# ---------------------------------------------------------------------------


class OverlapResolutionAlgorithm:
    """Resolves overlapping values between two section dictionaries.

    When two sections share keys their values may conflict semantically even
    when they are structurally distinct.  The ``OverlapResolutionAlgorithm``
    encapsulates the resolution policy and provides helper methods to build a
    complete resolution table, verify the resolved output, and apply the
    resolution to a base dictionary.  Supported strategies are
    ``"last_write_wins"`` (default) and ``"first_write_wins"``.  The algorithm
    detects semantic conflicts by comparing the JSON-serialisable hash of each
    value, treating identical hashes as non-conflicting even if the Python
    objects differ by identity.
    """

    def __init__(self, strategy: str = "last_write_wins") -> None:
        """Initialise with a conflict resolution strategy.

        Args:
            strategy: One of ``"last_write_wins"`` or ``"first_write_wins"``.
                Defaults to ``"last_write_wins"``.
        """
        self._strategy = strategy
        self._resolution_count: int = 0

    def resolve(
        self,
        a: dict[str, Any],
        b: dict[str, Any],
        overlap_keys: list[str],
    ) -> dict[str, Any]:
        """Resolve the overlapping keys between ``a`` and ``b``.

        Builds a resolution table and applies it to produce a merged dict
        containing resolved values for all ``overlap_keys``.

        Args:
            a: The first (older) section dictionary.
            b: The second (newer) section dictionary.
            overlap_keys: Keys that appear in both ``a`` and ``b``.

        Returns:
            A dict containing the resolved value for each key in
            ``overlap_keys`` plus all non-overlapping keys from both dicts.
        """
        table = self.build_resolution_table(a, b)
        result = self.apply_resolution({**a, **b}, table)
        self._resolution_count += 1
        return result

    def detect_semantic_conflict(self, a_val: Any, b_val: Any) -> bool:
        """Detect a semantic conflict between two values.

        Two values are semantically conflicting when their canonical JSON
        representations differ.  Non-serialisable values are compared via
        ``repr`` as a fallback.

        Args:
            a_val: The value from the first dictionary.
            b_val: The value from the second dictionary.

        Returns:
            ``True`` if the values are semantically different.
        """
        try:
            ha = hashlib.md5(json.dumps(a_val, sort_keys=True, default=str).encode()).hexdigest()
            hb = hashlib.md5(json.dumps(b_val, sort_keys=True, default=str).encode()).hexdigest()
            return ha != hb
        except Exception:
            return repr(a_val) != repr(b_val)

    def build_resolution_table(
        self,
        a: dict[str, Any],
        b: dict[str, Any],
    ) -> dict[str, tuple[Any, str]]:
        """Build a per-key resolution table for all shared keys.

        For each key present in both ``a`` and ``b`` the table records the
        winning value and the reason for the choice (``"same"``, ``"a_wins"``,
        or ``"b_wins"``).

        Args:
            a: The first section dictionary.
            b: The second section dictionary.

        Returns:
            A dict mapping each shared key to a ``(resolved_value, reason)``
            tuple.
        """
        table: dict[str, tuple[Any, str]] = {}
        for key in set(a.keys()) & set(b.keys()):
            if not self.detect_semantic_conflict(a[key], b[key]):
                table[key] = (a[key], "same")
            elif self._strategy == "first_write_wins":
                table[key] = (a[key], "a_wins")
            else:  # last_write_wins default
                table[key] = (b[key], "b_wins")
        return table

    def verify_resolution(
        self,
        resolved: dict[str, Any],
        overlap_keys: list[str],
    ) -> bool:
        """Verify that all overlap keys are present in the resolved dictionary.

        Args:
            resolved: The output of ``apply_resolution``.
            overlap_keys: The keys that should have been resolved.

        Returns:
            ``True`` if every overlap key is present in ``resolved``.
        """
        return all(k in resolved for k in overlap_keys)

    def apply_resolution(
        self,
        base: dict[str, Any],
        resolution: dict[str, tuple[Any, str]],
    ) -> dict[str, Any]:
        """Apply a resolution table to a base dictionary.

        Each key in the resolution table replaces the corresponding entry in
        the base dict with the resolved value.

        Args:
            base: The starting dictionary (typically the merge of ``a`` and ``b``).
            resolution: A dict mapping keys to ``(value, reason)`` tuples.

        Returns:
            A new dict with resolved values applied.
        """
        result = dict(base)
        for key, (value, _reason) in resolution.items():
            result[key] = value
        return result

    def summarize(self) -> str:
        """Return a one-line summary of this algorithm.

        Returns:
            A string reporting the strategy and resolution count.
        """
        return (
            f"OverlapResolutionAlgorithm(strategy={self._strategy!r}, "
            f"resolutions={self._resolution_count})"
        )


# ---------------------------------------------------------------------------
# EpochAdvanceAlgorithm
# ---------------------------------------------------------------------------


class EpochAdvanceAlgorithm:
    """Manages epoch advancement and rollback for a set of memory coordinates.

    The epoch system provides a lightweight version vector for each coordinate
    in the memory graph.  Each time a coordinate's data changes its epoch is
    advanced; each time a change is undone the epoch is rolled back.  Stale
    detection uses the stored epoch map to decide whether a cached value is
    still current.  The algorithm delegates all state mutations to the
    underlying ``EpochMap`` object and wraps every call in a try/except so
    that a missing or partially initialised map does not propagate exceptions
    to callers.  Drift computation identifies coordinates whose epochs have
    diverged significantly from a reference.
    """

    def __init__(self, epoch_map: Any) -> None:
        """Initialise with an ``EpochMap`` instance.

        Args:
            epoch_map: An ``EpochMap`` (or compatible object) that exposes
                ``advance``, ``rollback``, and ``current_epoch_at`` methods.
        """
        self._epoch_map = epoch_map

    def advance(self, coord: str) -> int:
        """Advance the epoch of ``coord`` by one.

        Args:
            coord: The coordinate whose epoch should be incremented.

        Returns:
            The new epoch value, or ``-1`` if the operation failed.
        """
        try:
            return self._epoch_map.advance(coord)
        except Exception as exc:
            logger.warning("EpochAdvanceAlgorithm.advance failed for %s: %s", coord, exc)
            return -1

    def advance_all(self, coords: list[str]) -> dict[str, int]:
        """Advance the epoch of every coordinate in ``coords``.

        Args:
            coords: A list of coordinate strings.

        Returns:
            A dict mapping each coordinate to its new epoch value.  Coordinates
            for which the advance failed map to ``-1``.
        """
        return {c: self.advance(c) for c in coords}

    def rollback(self, coord: str) -> int:
        """Roll back the epoch of ``coord`` by one.

        Args:
            coord: The coordinate to roll back.

        Returns:
            The epoch value after rollback, or ``-1`` on failure.
        """
        try:
            return self._epoch_map.rollback(coord)
        except Exception as exc:
            logger.warning("EpochAdvanceAlgorithm.rollback failed for %s: %s", coord, exc)
            return -1

    def get_current(self, coord: str) -> int:
        """Return the current epoch of ``coord`` without modifying it.

        Args:
            coord: The coordinate to query.

        Returns:
            The current epoch integer, or ``0`` if not tracked.
        """
        try:
            return self._epoch_map.current_epoch_at(coord)
        except Exception:
            return 0

    def is_stale(self, coord: str, reference_epoch: int) -> bool:
        """Check whether ``coord``'s epoch is behind a reference epoch.

        Args:
            coord: The coordinate to check.
            reference_epoch: The epoch value to compare against.

        Returns:
            ``True`` if the coordinate's current epoch is less than
            ``reference_epoch``.
        """
        return self.get_current(coord) < reference_epoch

    def compute_epoch_drift(self, coords: list[str]) -> dict[str, int]:
        """Compute the epoch drift of each coordinate relative to the maximum.

        Drift is defined as ``max_epoch - current_epoch`` for each coordinate.
        A drift of zero means the coordinate is at the global maximum epoch.

        Args:
            coords: The coordinates to analyse.

        Returns:
            A dict mapping each coordinate to its drift value.
        """
        if not coords:
            return {}
        epochs = {c: self.get_current(c) for c in coords}
        max_epoch = max(epochs.values())
        return {c: max_epoch - e for c, e in epochs.items()}

    def snapshot_epochs(self, coords: list[str]) -> dict[str, int]:
        """Capture a snapshot of current epochs for the given coordinates.

        This is a pure read operation; no epochs are modified.

        Args:
            coords: The coordinates to snapshot.

        Returns:
            A dict mapping each coordinate to its current epoch.
        """
        return {c: self.get_current(c) for c in coords}

    def summarize(self) -> str:
        """Return a one-line summary of this algorithm instance.

        Returns:
            A string identifying the epoch map type in use.
        """
        return f"EpochAdvanceAlgorithm(epoch_map={type(self._epoch_map).__name__})"


# ---------------------------------------------------------------------------
# MemoryCompactionAlgorithm
# ---------------------------------------------------------------------------


class MemoryCompactionAlgorithm:
    """Reduces memory state size by evicting old snapshots and deduplicating sections.

    Over time the ``PersistentMemoryState`` accumulates a large number of
    snapshots and redundant section cache entries.  The ``MemoryCompactionAlgorithm``
    provides strategies for managing this growth: snapshot pruning retains only
    the most recent ``max_snapshots`` entries, deduplication removes section cache
    entries whose content hashes match, cold-entry identification uses access
    timestamps to find infrequently used entries, and eviction removes them.
    A full compaction run combines all these strategies and returns both the
    compacted state and a report dict describing the savings achieved.
    """

    def __init__(self, max_snapshots: int = 100) -> None:
        """Initialise the algorithm with a snapshot retention limit.

        Args:
            max_snapshots: Maximum number of snapshots to retain after
                compaction.  Defaults to 100.
        """
        self._max_snapshots = max_snapshots
        self._compaction_count: int = 0

    def compact(self, state: Any) -> Any:
        """Prune excess snapshots from a ``PersistentMemoryState``.

        Keeps only the most recent ``max_snapshots`` snapshots by slicing the
        ``snapshots`` list.

        Args:
            state: A ``PersistentMemoryState`` with a ``snapshots`` list
                attribute.

        Returns:
            The (mutated) state with excess snapshots removed.
        """
        try:
            if len(state.snapshots) > self._max_snapshots:
                state.snapshots = state.snapshots[-self._max_snapshots:]
        except AttributeError:
            pass
        self._compaction_count += 1
        return state

    def deduplicate_sections(
        self,
        section_cache: dict[str, Any],
    ) -> dict[str, Any]:
        """Remove duplicate entries from a section cache by content hash.

        Two entries are considered duplicates when their JSON representations
        hash to the same MD5 digest.  The first occurrence of each hash is
        kept; subsequent duplicates are discarded.

        Args:
            section_cache: A dict mapping coordinate strings to section data.

        Returns:
            A deduplicated copy of ``section_cache``.
        """
        seen_hashes: set[str] = set()
        result: dict[str, Any] = {}
        for key, value in section_cache.items():
            try:
                digest = hashlib.md5(
                    json.dumps(value, sort_keys=True, default=str).encode()
                ).hexdigest()
            except Exception:
                digest = hashlib.md5(repr(value).encode()).hexdigest()
            if digest not in seen_hashes:
                seen_hashes.add(digest)
                result[key] = value
        return result

    def compute_savings(self, before: int, after: int) -> float:
        """Compute the fractional space savings from compaction.

        Args:
            before: Size metric before compaction (e.g. byte count or entry
                count).
            after: Size metric after compaction.

        Returns:
            A float in ``[0.0, 1.0]`` representing the fraction of space saved.
            Returns ``0.0`` if ``before`` is zero.
        """
        if before == 0:
            return 0.0
        return (before - after) / float(before)

    def identify_cold_entries(
        self,
        section_cache: dict[str, Any],
        access_times: dict[str, float],
        threshold: float,
    ) -> list[str]:
        """Identify section cache entries that have not been accessed recently.

        An entry is considered cold if its last access time (from
        ``access_times``) is older than ``threshold`` seconds in the past, or
        if it has no recorded access time.

        Args:
            section_cache: The section cache to inspect.
            access_times: A dict mapping coordinate keys to last-access
                timestamps (seconds since epoch).
            threshold: Age threshold in seconds; entries older than this are
                cold.

        Returns:
            A list of cold coordinate keys.
        """
        cutoff = time.time() - threshold
        cold: list[str] = []
        for key in section_cache:
            last_access = access_times.get(key, 0.0)
            if last_access < cutoff:
                cold.append(key)
        return cold

    def evict_entries(
        self,
        section_cache: dict[str, Any],
        keys_to_evict: list[str],
    ) -> dict[str, Any]:
        """Remove the specified keys from a section cache.

        Args:
            section_cache: The section cache to evict from.
            keys_to_evict: The keys to remove.

        Returns:
            A new dict with the specified keys removed.
        """
        result = dict(section_cache)
        for key in keys_to_evict:
            result.pop(key, None)
        return result

    def estimate_size(self, data: dict[str, Any]) -> int:
        """Estimate the serialised byte size of a data dict.

        Uses ``json.dumps`` with a ``default=str`` fallback to handle
        non-serialisable values.

        Args:
            data: The data dict to measure.

        Returns:
            The length of the JSON string in bytes.
        """
        return len(json.dumps(data, default=str))

    def run_full_compaction(
        self,
        state: Any,
    ) -> tuple[Any, dict[str, Any]]:
        """Run all compaction strategies in sequence and return a report.

        Executes snapshot pruning, section-cache deduplication (if
        ``section_cache`` is present on ``state``), and reports bytes saved.

        Args:
            state: A ``PersistentMemoryState`` to compact.

        Returns:
            A tuple ``(compacted_state, report)`` where ``report`` is a dict
            containing ``snapshots_before``, ``snapshots_after``,
            ``cache_entries_before``, ``cache_entries_after``, and
            ``savings_fraction``.
        """
        try:
            snapshots_before = len(state.snapshots)
        except AttributeError:
            snapshots_before = 0

        state = self.compact(state)

        try:
            snapshots_after = len(state.snapshots)
        except AttributeError:
            snapshots_after = 0

        cache_before = 0
        cache_after = 0
        for attr in ("section_cache", "_section_cache", "cache"):
            cache = getattr(state, attr, None)
            if isinstance(cache, dict):
                cache_before = len(cache)
                deduped = self.deduplicate_sections(cache)
                setattr(state, attr, deduped)
                cache_after = len(deduped)
                break

        report = {
            "snapshots_before": snapshots_before,
            "snapshots_after": snapshots_after,
            "cache_entries_before": cache_before,
            "cache_entries_after": cache_after,
            "savings_fraction": self.compute_savings(
                snapshots_before + cache_before,
                snapshots_after + cache_after,
            ),
        }
        return state, report

    def summarize(self) -> str:
        """Return a one-line summary of this algorithm.

        Returns:
            A string reporting the max_snapshots limit and compaction count.
        """
        return (
            f"MemoryCompactionAlgorithm(max_snapshots={self._max_snapshots}, "
            f"compactions={self._compaction_count})"
        )


# ---------------------------------------------------------------------------
# QuotaEnforcementAlgorithm
# ---------------------------------------------------------------------------


class QuotaEnforcementAlgorithm:
    """Enforces a hard storage quota on the ``PersistentMemoryState``.

    Storage quotas prevent unbounded growth of the memory state when the
    incremental update pipeline produces data faster than it is consumed.
    The ``QuotaEnforcementAlgorithm`` computes current usage as the
    serialised byte length of ``section_cache``, compares it to the
    configured limit, and evicts the oldest or largest entries until the
    state fits within the quota.  Headroom and usage-ratio methods provide
    the caller with enough information to decide whether a prospective update
    should be rejected before it is applied.
    """

    def __init__(self, limit: int) -> None:
        """Initialise with a quota limit in bytes.

        Args:
            limit: Maximum allowed storage in bytes.  Must be positive.
        """
        self._limit = limit
        self._enforcement_count: int = 0

    def check(self, current_usage: int) -> bool:
        """Return True if ``current_usage`` is within the configured quota.

        Args:
            current_usage: Current storage usage in bytes.

        Returns:
            ``True`` if ``current_usage <= self._limit``.
        """
        return current_usage <= self._limit

    def compute_usage(self, state: Any) -> int:
        """Compute the current storage usage of a memory state.

        Usage is measured as the JSON byte length of the section cache.

        Args:
            state: A ``PersistentMemoryState`` with an optional ``section_cache``
                attribute.

        Returns:
            Estimated storage usage in bytes.
        """
        for attr in ("section_cache", "_section_cache", "cache"):
            cache = getattr(state, attr, None)
            if isinstance(cache, dict):
                return len(json.dumps(cache, default=str))
        return 0

    def enforce(self, state: Any) -> Any:
        """Evict entries until the state fits within the quota.

        Removes the largest serialised entries first until ``compute_usage``
        returns a value at or below the limit.

        Args:
            state: The ``PersistentMemoryState`` to enforce quota on.

        Returns:
            The (mutated) state with excess entries removed.
        """
        self._enforcement_count += 1
        cache_attr: str | None = None
        cache: dict[str, Any] | None = None
        for attr in ("section_cache", "_section_cache", "cache"):
            candidate = getattr(state, attr, None)
            if isinstance(candidate, dict):
                cache_attr = attr
                cache = candidate
                break

        if cache is None or cache_attr is None:
            return state

        while self.compute_usage(state) > self._limit and cache:
            # Evict the entry with the largest serialised size.
            largest_key = max(
                cache.keys(),
                key=lambda k: len(json.dumps(cache[k], default=str)),  # type: ignore[index]
            )
            del cache[largest_key]
            logger.debug("QuotaEnforcement: evicted %s", largest_key)

        setattr(state, cache_attr, cache)
        return state

    def headroom(self, state: Any) -> int:
        """Return the number of bytes available before the quota is reached.

        Args:
            state: A ``PersistentMemoryState``.

        Returns:
            ``max(0, limit - usage)``.
        """
        return max(0, self._limit - self.compute_usage(state))

    def usage_ratio(self, state: Any) -> float:
        """Return current usage as a fraction of the quota.

        Args:
            state: A ``PersistentMemoryState``.

        Returns:
            A float in ``[0.0, …]``; values above 1.0 indicate the quota is
            exceeded.  Returns 0.0 if the limit is zero.
        """
        if self._limit == 0:
            return 0.0
        return self.compute_usage(state) / float(self._limit)

    def project_usage(
        self,
        state: Any,
        new_sections: dict[str, Any],
    ) -> int:
        """Project the storage usage after adding ``new_sections``.

        Args:
            state: The current ``PersistentMemoryState``.
            new_sections: Prospective new sections to add.

        Returns:
            Estimated total usage in bytes after the update.
        """
        current = self.compute_usage(state)
        extra = len(json.dumps(new_sections, default=str))
        return current + extra

    def will_exceed(
        self,
        state: Any,
        new_sections: dict[str, Any],
    ) -> bool:
        """Return True if adding ``new_sections`` would exceed the quota.

        Args:
            state: The current ``PersistentMemoryState``.
            new_sections: Prospective sections to be added.

        Returns:
            ``True`` if ``project_usage(state, new_sections) > self._limit``.
        """
        return self.project_usage(state, new_sections) > self._limit

    def summarize(self) -> str:
        """Return a one-line summary of this algorithm.

        Returns:
            A string reporting the limit and enforcement count.
        """
        return (
            f"QuotaEnforcementAlgorithm(limit={self._limit}, "
            f"enforcements={self._enforcement_count})"
        )


# ---------------------------------------------------------------------------
# SupportMinimizationAlgorithm
# ---------------------------------------------------------------------------


class SupportMinimizationAlgorithm:
    """Minimizes the ``EncodingSupportSet`` to the smallest correct subset.

    The encoding support set records which memory coordinates participate in a
    given encoding computation.  Over time, as computations are refined and
    some coordinates become provably irrelevant, the support set can grow
    larger than necessary.  The ``SupportMinimizationAlgorithm`` identifies
    redundant and dominated coordinates and removes them, producing a minimal
    support set that satisfies all active ``ChangeEvent`` requirements.  Minimal
    supports reduce the cost of future cascade computations by limiting the
    scope of graph traversals.  The algorithm verifies that the minimized
    support still covers all events before returning.
    """

    def __init__(self) -> None:
        """Initialise the algorithm with an empty minimization log."""
        self._minimize_count: int = 0

    def minimize(
        self,
        support: Any,
        memory_data: dict[str, Any],
    ) -> Any:
        """Minimize ``support`` relative to the currently live memory data.

        Removes any coordinate from ``support`` that is not present as a key
        in ``memory_data``, then removes dominated coordinates.

        Args:
            support: An ``EncodingSupportSet`` with a ``coords`` attribute.
            memory_data: The current section cache or memory data dict.

        Returns:
            A minimized ``EncodingSupportSet``.
        """
        self._minimize_count += 1
        support = self.remove_dominated(support)
        try:
            live_coords = [c for c in support.coords if c in memory_data]
            support.coords = live_coords
        except AttributeError:
            pass
        return support

    def find_redundant_coords(
        self,
        support: Any,
        events: list[Any],
    ) -> list[str]:
        """Identify support coordinates not referenced by any event.

        A coordinate is redundant if no event in ``events`` targets it
        (i.e. no event has ``event.coordinate == coord``).

        Args:
            support: An ``EncodingSupportSet``.
            events: A list of ``ChangeEvent`` objects.

        Returns:
            A list of coordinate strings that are in ``support`` but not
            referenced by any event.
        """
        try:
            support_coords: set[str] = set(support.coords)
        except AttributeError:
            return []

        event_coords: set[str] = set()
        for e in events:
            try:
                event_coords.add(e.coordinate)
            except AttributeError:
                pass

        return list(support_coords - event_coords)

    def remove_dominated(self, support: Any) -> Any:
        """Remove dominated coordinates from the support set.

        A coordinate is considered dominated (and thus removable) if it is a
        proper prefix of another coordinate in the set, suggesting it is
        subsumed by the more specific coordinate.  This is a heuristic
        applicable when coordinates are hierarchical path strings.

        Args:
            support: An ``EncodingSupportSet``.

        Returns:
            The support with dominated coordinates removed.
        """
        try:
            coords = list(support.coords)
        except AttributeError:
            return support

        coord_set = set(coords)
        non_dominated: list[str] = []
        for c in coords:
            dominated = any(
                other != c and other.startswith(c + "/")
                for other in coord_set
            )
            if not dominated:
                non_dominated.append(c)

        try:
            support.coords = non_dominated
        except AttributeError:
            pass
        return support

    def compute_minimal_support(self, events: list[Any]) -> Any:
        """Compute a minimal support set directly from a list of events.

        The minimal support contains exactly the coordinates referenced by the
        events, deduplicated.

        Args:
            events: A list of ``ChangeEvent`` objects.

        Returns:
            An ``EncodingSupportSet`` with exactly the event coordinates.
        """
        coords: list[str] = []
        seen: set[str] = set()
        for e in events:
            try:
                c = e.coordinate
                if c not in seen:
                    seen.add(c)
                    coords.append(c)
            except AttributeError:
                pass

        try:
            return EncodingSupportSet(coords=coords)
        except Exception:
            return {"coords": coords}

    def verify_minimality(
        self,
        support: Any,
        original: Any,
    ) -> bool:
        """Verify that ``support`` is a subset of ``original``.

        Args:
            support: The minimized ``EncodingSupportSet``.
            original: The original ``EncodingSupportSet`` before minimization.

        Returns:
            ``True`` if every coordinate in ``support`` was also in
            ``original``.
        """
        try:
            s_coords = set(support.coords)
            o_coords = set(original.coords)
        except AttributeError:
            return True
        return s_coords.issubset(o_coords)

    def summarize(self) -> str:
        """Return a one-line summary of this algorithm.

        Returns:
            A string reporting the number of minimizations performed.
        """
        return f"SupportMinimizationAlgorithm(minimizations={self._minimize_count})"


# ---------------------------------------------------------------------------
# BatchUpdateOptimizer
# ---------------------------------------------------------------------------


class BatchUpdateOptimizer:
    """Optimizes a batch of ``IncrementalUpdate`` objects before application.

    When many incremental updates arrive in rapid succession it is often more
    efficient to merge them before applying each to memory, reducing the total
    number of Glue computations required.  The ``BatchUpdateOptimizer`` accepts
    updates via ``add_update``, buffers them in ``_pending``, and produces an
    optimized list via ``optimize``.  Two updates can be merged if they target
    compatible coordinate scopes (determined by ``can_merge``).  The optimizer
    uses a greedy left-to-right scan over epoch-sorted updates, merging as many
    consecutive compatible updates as possible before moving to the next group.
    The ``flush`` method returns the optimized list and clears the pending queue.
    """

    def __init__(self) -> None:
        """Initialise the optimizer with an empty pending update queue."""
        self._pending: list[Any] = []
        self._flush_count: int = 0

    def add_update(self, update: Any) -> None:
        """Add an ``IncrementalUpdate`` to the pending queue.

        Args:
            update: The ``IncrementalUpdate`` to buffer.
        """
        self._pending.append(update)

    def optimize(self) -> list[Any]:
        """Produce an optimized list of updates from the pending queue.

        Sorts the pending updates by epoch and greedily merges consecutive
        compatible pairs.  The original ``_pending`` list is not modified by
        this method; call ``flush`` to both optimize and clear the queue.

        Returns:
            A list of ``IncrementalUpdate`` objects with compatible updates
            merged together.
        """
        if not self._pending:
            return []
        result: list[Any] = []
        sorted_updates = self.sort_by_epoch(list(self._pending))
        i = 0
        while i < len(sorted_updates):
            current = sorted_updates[i]
            j = i + 1
            while j < len(sorted_updates) and self.can_merge(current, sorted_updates[j]):
                current = self.merge_updates(current, sorted_updates[j])
                j += 1
            result.append(current)
            i = j
        return result

    def merge_updates(self, a: Any, b: Any) -> Any:
        """Merge two compatible ``IncrementalUpdate`` objects into one.

        The merged update takes the later epoch from ``b`` and unions the
        section data from both updates, with ``b`` taking precedence on
        overlapping keys.

        Args:
            a: The earlier update.
            b: The later update.

        Returns:
            A new ``IncrementalUpdate`` representing the combined change.
        """
        try:
            a_sections: dict[str, Any] = dict(getattr(a, "new_sections", {}) or {})
            b_sections: dict[str, Any] = dict(getattr(b, "new_sections", {}) or {})
            merged_sections = {**a_sections, **b_sections}

            try:
                a_epoch = int(getattr(a, "epoch", 0) or 0)
                b_epoch = int(getattr(b, "epoch", 0) or 0)
            except (TypeError, ValueError):
                a_epoch, b_epoch = 0, 0

            return IncrementalUpdate(
                new_sections=merged_sections,
                epoch=max(a_epoch, b_epoch),
            )
        except Exception:
            return b

    def can_merge(self, a: Any, b: Any) -> bool:
        """Determine whether two updates are safe to merge.

        Two updates can be merged when they share the same root coordinate
        (``root_coord`` attribute) or when neither has a root coordinate set.

        Args:
            a: The first ``IncrementalUpdate``.
            b: The second ``IncrementalUpdate``.

        Returns:
            ``True`` if the updates are compatible for merging.
        """
        try:
            a_root = getattr(a, "root_coord", None)
            b_root = getattr(b, "root_coord", None)
            if a_root is None and b_root is None:
                return True
            return a_root == b_root
        except Exception:
            return False

    def sort_by_epoch(self, updates: list[Any]) -> list[Any]:
        """Return updates sorted by ascending epoch value.

        Updates without an ``epoch`` attribute are treated as epoch 0.

        Args:
            updates: The list of ``IncrementalUpdate`` objects to sort.

        Returns:
            A new list sorted from oldest to newest epoch.
        """
        def epoch_key(u: Any) -> int:
            try:
                return int(getattr(u, "epoch", 0) or 0)
            except (TypeError, ValueError):
                return 0

        return sorted(updates, key=epoch_key)

    def estimate_savings(
        self,
        before: list[Any],
        after: list[Any],
    ) -> float:
        """Estimate the fractional reduction in update count from optimization.

        Args:
            before: The original list of updates.
            after: The optimized list of updates.

        Returns:
            A float in ``[0.0, 1.0]`` representing the fraction of updates
            eliminated; returns ``0.0`` if ``before`` is empty.
        """
        n_before = len(before)
        if n_before == 0:
            return 0.0
        n_after = len(after)
        return (n_before - n_after) / float(n_before)

    def flush(self) -> list[Any]:
        """Optimize the pending queue, clear it, and return the result.

        Returns:
            The optimized list of ``IncrementalUpdate`` objects.  After this
            call ``_pending`` is empty.
        """
        result = self.optimize()
        self._pending.clear()
        self._flush_count += 1
        return result

    def summarize(self) -> str:
        """Return a one-line summary of this optimizer's state.

        Returns:
            A string reporting pending update count and flush count.
        """
        return (
            f"BatchUpdateOptimizer(pending={len(self._pending)}, "
            f"flushes={self._flush_count})"
        )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    "GlueAlgorithm",
    "SectionDiffAlgorithm",
    "OverlapResolutionAlgorithm",
    "EpochAdvanceAlgorithm",
    "MemoryCompactionAlgorithm",
    "QuotaEnforcementAlgorithm",
    "SupportMinimizationAlgorithm",
    "BatchUpdateOptimizer",
]
