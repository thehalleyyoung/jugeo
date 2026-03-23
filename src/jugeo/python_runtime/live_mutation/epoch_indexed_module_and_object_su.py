from __future__ import annotations

"""s02 — Epoch-Indexed Module and Object Summaries (Ch23 §2).

Epoch indexing assigns a version number to every module snapshot;
hot reload = epoch increment.  This module implements the EpochStore,
ObjectSummaryAnalyzer, and EpochIndexedModuleObjectCoordinator for
tracking module evolution under JuGeo's sheaf-theoretic semantics.

Theory Context (Ch23 §2):
    In the sheaf-theoretic model of a live runtime, every module is
    regarded as a local section of a sheaf of bindings over an open
    cover of the type-space.  When a hot-reload event fires the
    restriction map sends the old section to the new one, and the
    *epoch number* is the discrete monotone counter that witnesses
    this morphism.  Object summaries sit one layer above: they track
    the attribute-level fingerprint of each live object at each epoch
    boundary, enabling change detection, rollback, and incremental
    recompilation without a full re-parse of the source graph.

Usage Example::

    coordinator = EpochIndexedModuleObjectCoordinator()
    rec = coordinator.register_module("myapp.utils", frozenset({"helper", "Config"}))
    print(rec.label())   # myapp.utils@0(INITIAL)

    rec2 = coordinator.update_module("myapp.utils", frozenset({"helper", "Config", "Logger"}))
    print(rec2.label())  # myapp.utils@1(INCREMENT)

    summary = coordinator.snapshot_object("obj:42", "Config", frozenset({"host", "port"}))
    print(summary.label())   # obj:42:Config@ep1
"""

import hashlib
import json
import logging
import math
import re
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

try:
    from jugeo.sheaf import SheafSection  # type: ignore
except ImportError:
    class SheafSection:  # type: ignore
        """Inline stub for jugeo.sheaf.SheafSection.

        Used when the full jugeo.sheaf package is not installed.  The
        stub provides the minimal surface area required by this module
        so that unit tests can import without the full dependency tree.
        """
        def __init__(self, section_id: str = "") -> None:
            self.section_id = section_id

try:
    from jugeo.diagnostics import EpochDiagnostics  # type: ignore
except ImportError:
    class EpochDiagnostics:  # type: ignore
        """Inline stub for jugeo.diagnostics.EpochDiagnostics.

        Provides a no-op ``record`` method so diagnostic calls in this
        module degrade gracefully when the diagnostics subsystem is
        absent.
        """
        def record(self, data: dict[str, Any]) -> None: ...

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def _new_epoch_id() -> str:
    """Generate a compact random identifier for a new epoch record.

    Returns:
        A 12-character lowercase hexadecimal string derived from a
        random UUID4.

    Example::

        eid = _new_epoch_id()
        assert len(eid) == 12
        assert re.fullmatch(r'[0-9a-f]{12}', eid)
    """
    return uuid.uuid4().hex[:12]


def _new_summary_id() -> str:
    """Generate a compact random identifier for a new object summary.

    Returns:
        A 12-character lowercase hexadecimal string derived from a
        random UUID4.

    Example::

        sid = _new_summary_id()
        assert len(sid) == 12
    """
    return uuid.uuid4().hex[:12]


def _hash_keys(keys: frozenset[str]) -> str:
    """Compute a deterministic 16-character hash of a set of binding keys.

    The keys are sorted before hashing so that the result is
    independent of insertion order.  The SHA-256 digest is truncated
    to 16 hex characters (64 bits) — sufficient for collision-
    resistance in module tracking while remaining human-readable in
    log output.

    Args:
        keys: The set of string binding keys to hash.

    Returns:
        A 16-character lowercase hexadecimal string.

    Example::

        h = _hash_keys(frozenset({"alpha", "beta", "gamma"}))
        assert len(h) == 16
        assert h == _hash_keys(frozenset({"gamma", "alpha", "beta"}))
    """
    joined = ",".join(sorted(keys))
    digest = hashlib.sha256(joined.encode("utf-8")).hexdigest()
    return digest[:16]


def _epoch_key(module_name: str, epoch_number: int) -> str:
    """Compose a human-readable lookup key for a specific module epoch.

    Args:
        module_name: Fully-qualified Python module name, e.g.
            ``"myapp.utils"``.
        epoch_number: Non-negative integer epoch counter for that
            module.

    Returns:
        A string of the form ``"<module_name>@<epoch_number>"``.

    Example::

        assert _epoch_key("myapp.utils", 3) == "myapp.utils@3"
    """
    return f"{module_name}@{epoch_number}"


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class EpochKind(str, Enum):
    """Classifies the causal reason behind an epoch boundary.

    Inheriting from ``str`` allows ``EpochKind`` values to be used
    directly in JSON serialisation and f-string formatting without an
    explicit ``.value`` access.

    Attributes:
        INITIAL: The very first epoch recorded for a module; no parent
            epoch exists.
        INCREMENT: A forward hot-reload that adds or removes bindings.
        ROLLBACK: A revert to an earlier known-good snapshot; the new
            epoch's keys are copied from the target historical epoch.
        SNAPSHOT: A read-only checkpoint that does not alter bindings;
            useful for periodic audit trails.
        INVALIDATION: The module's section was invalidated (e.g. due
            to a failed parse) and all bindings were cleared.
    """

    INITIAL = "INITIAL"
    INCREMENT = "INCREMENT"
    ROLLBACK = "ROLLBACK"
    SNAPSHOT = "SNAPSHOT"
    INVALIDATION = "INVALIDATION"


# ---------------------------------------------------------------------------
# Value-type dataclasses (frozen)
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class EpochRecord:
    """Immutable snapshot of a module's binding state at one epoch.

    An ``EpochRecord`` is created every time a module crosses an epoch
    boundary (reload, rollback, snapshot, or invalidation).  It
    carries enough information to reconstruct the module's binding
    environment and to trace the causal chain of changes.

    Attributes:
        epoch_id: Unique random identifier for this record.
        module_name: Fully-qualified Python module name.
        epoch_number: Monotonically increasing counter scoped to this
            module.  The first epoch is 0.
        snapshot_hash: 16-char SHA-256 prefix of the sorted binding
            keys.  Changes when any key is added or removed.
        binding_keys: The complete set of top-level names exported by
            the module at this epoch.
        created_at: POSIX timestamp (seconds since epoch) when this
            record was created.
        parent_epoch_id: The ``epoch_id`` of the immediately preceding
            epoch for this module, or ``None`` for ``INITIAL`` epochs.
        kind: The ``EpochKind`` that triggered this epoch boundary.
    """

    epoch_id: str
    module_name: str
    epoch_number: int
    snapshot_hash: str
    binding_keys: frozenset[str]
    created_at: float
    parent_epoch_id: str | None
    kind: EpochKind

    def age(self) -> float:
        """Compute the number of seconds elapsed since this record was created.

        Returns:
            A non-negative float representing the age in seconds.

        Example::

            rec = EpochRecord(epoch_id="abc", module_name="m", epoch_number=0,
                              snapshot_hash="x"*16, binding_keys=frozenset(),
                              created_at=time.time()-10, parent_epoch_id=None,
                              kind=EpochKind.INITIAL)
            assert 9 < rec.age() < 11
        """
        return time.time() - self.created_at

    def key_count(self) -> int:
        """Return the number of binding keys in this epoch's snapshot.

        Returns:
            Non-negative integer length of ``binding_keys``.
        """
        return len(self.binding_keys)

    def label(self) -> str:
        """Return a compact human-readable label for this epoch record.

        The label combines the module name, epoch number, and kind so
        it can be used in log messages and UI displays without
        importing the full record.

        Returns:
            String of the form ``"<module>@<number>(<kind>)"``.

        Example::

            # For module "myapp.utils", epoch 2, kind INCREMENT:
            # returns "myapp.utils@2(INCREMENT)"
        """
        return f"{self.module_name}@{self.epoch_number}({self.kind.value})"

    def to_dict(self) -> dict[str, Any]:
        """Serialise this record to a JSON-compatible dictionary.

        All fields are included.  ``binding_keys`` is converted from
        ``frozenset`` to a sorted ``list`` for stable serialisation.
        ``kind`` is emitted as its string value.

        Returns:
            A dictionary suitable for ``json.dumps``.

        Example::

            d = rec.to_dict()
            assert d["epoch_number"] == rec.epoch_number
            assert isinstance(d["binding_keys"], list)
        """
        return {
            "epoch_id": self.epoch_id,
            "module_name": self.module_name,
            "epoch_number": self.epoch_number,
            "snapshot_hash": self.snapshot_hash,
            "binding_keys": sorted(self.binding_keys),
            "created_at": self.created_at,
            "parent_epoch_id": self.parent_epoch_id,
            "kind": self.kind.value,
        }

    def is_initial(self) -> bool:
        """Return ``True`` if this is the first epoch for its module.

        Returns:
            ``True`` iff ``kind == EpochKind.INITIAL``.
        """
        return self.kind == EpochKind.INITIAL

    def has_parent(self) -> bool:
        """Return ``True`` if a parent epoch exists.

        Returns:
            ``True`` iff ``parent_epoch_id`` is not ``None``.
        """
        return self.parent_epoch_id is not None


@dataclass(frozen=True, slots=True)
class ObjectSummary:
    """Immutable attribute-level fingerprint of a live object at one epoch.

    Object summaries capture the *shape* (attribute names) of a Python
    object at the moment it is observed, along with the epoch counter
    so its evolution can be correlated with module reloads.

    Attributes:
        summary_id: Unique random identifier for this summary.
        object_id: Application-level identifier of the object being
            tracked (e.g. ``"obj:42"`` or ``"Config@main"``).
        type_name: The ``__class__.__name__`` of the object.
        attribute_keys: Frozenset of attribute names present on the
            object at observation time.
        epoch_number: The module epoch during which this summary was
            taken.
        created_at: POSIX timestamp when this summary was recorded.
        attribute_hash: 16-char SHA-256 prefix of the sorted attribute
            keys.
    """

    summary_id: str
    object_id: str
    type_name: str
    attribute_keys: frozenset[str]
    epoch_number: int
    created_at: float
    attribute_hash: str

    def age(self) -> float:
        """Return seconds elapsed since this summary was created.

        Returns:
            Non-negative float representing age in seconds.
        """
        return time.time() - self.created_at

    def attribute_count(self) -> int:
        """Return the number of attributes in this summary.

        Returns:
            Non-negative integer length of ``attribute_keys``.
        """
        return len(self.attribute_keys)

    def label(self) -> str:
        """Return a compact label for log messages and UI displays.

        Returns:
            String of the form ``"<object_id>:<type_name>@ep<epoch>"``.

        Example::

            # object_id="obj:42", type_name="Config", epoch_number=3
            # returns "obj:42:Config@ep3"
        """
        return f"{self.object_id}:{self.type_name}@ep{self.epoch_number}"

    def to_dict(self) -> dict[str, Any]:
        """Serialise this summary to a JSON-compatible dictionary.

        ``attribute_keys`` is emitted as a sorted list for stable
        round-tripping through JSON.

        Returns:
            A dictionary suitable for ``json.dumps``.
        """
        return {
            "summary_id": self.summary_id,
            "object_id": self.object_id,
            "type_name": self.type_name,
            "attribute_keys": sorted(self.attribute_keys),
            "epoch_number": self.epoch_number,
            "created_at": self.created_at,
            "attribute_hash": self.attribute_hash,
        }

    def shares_attributes(self, other: ObjectSummary) -> bool:
        """Return ``True`` if this summary and ``other`` have any common attributes.

        Performs a non-empty intersection check on the two
        ``attribute_keys`` frozensets.

        Args:
            other: Another ``ObjectSummary`` to compare against.

        Returns:
            ``True`` iff ``self.attribute_keys & other.attribute_keys``
            is non-empty.

        Example::

            s1 = ObjectSummary(..., attribute_keys=frozenset({"a", "b"}), ...)
            s2 = ObjectSummary(..., attribute_keys=frozenset({"b", "c"}), ...)
            assert s1.shares_attributes(s2)  # "b" is shared
        """
        return bool(self.attribute_keys & other.attribute_keys)


@dataclass(frozen=True, slots=True)
class EpochDelta:
    """Immutable diff between two epochs of the same module.

    Captures the keys that were added, removed, or retained when the
    module transitioned from ``from_epoch`` to ``to_epoch``.

    Attributes:
        delta_id: Unique random identifier for this delta record.
        from_epoch: The source epoch number (lower, typically).
        to_epoch: The destination epoch number (higher, typically).
        added_keys: Binding keys present in ``to_epoch`` but absent in
            ``from_epoch``.
        removed_keys: Binding keys present in ``from_epoch`` but absent
            in ``to_epoch``.
        retained_keys: Binding keys present in both epochs.
        computed_at: POSIX timestamp when this delta was computed.
    """

    delta_id: str
    from_epoch: int
    to_epoch: int
    added_keys: frozenset[str]
    removed_keys: frozenset[str]
    retained_keys: frozenset[str]
    computed_at: float

    def change_count(self) -> int:
        """Return the total number of changed keys (added + removed).

        Returns:
            Non-negative integer count of keys that differ between
            the two epochs.

        Example::

            # 2 added, 1 removed → 3
            assert delta.change_count() == len(delta.added_keys) + len(delta.removed_keys)
        """
        return len(self.added_keys) + len(self.removed_keys)

    def retention_ratio(self) -> float:
        """Compute the fraction of keys from ``from_epoch`` that were retained.

        A value of 1.0 means no keys were removed; 0.0 means all were
        removed.  Returns 1.0 when ``from_epoch`` had no keys (vacuous
        stability).

        Returns:
            Float in the range [0.0, 1.0].

        Example::

            # retained=3, from had 4 keys (retained+removed) → 0.75
            assert abs(delta.retention_ratio() - 0.75) < 1e-9
        """
        from_total = len(self.retained_keys) + len(self.removed_keys)
        if from_total == 0:
            return 1.0
        return len(self.retained_keys) / from_total

    def to_dict(self) -> dict[str, Any]:
        """Serialise this delta to a JSON-compatible dictionary.

        All frozensets are converted to sorted lists for stability.

        Returns:
            A dictionary suitable for ``json.dumps``.
        """
        return {
            "delta_id": self.delta_id,
            "from_epoch": self.from_epoch,
            "to_epoch": self.to_epoch,
            "added_keys": sorted(self.added_keys),
            "removed_keys": sorted(self.removed_keys),
            "retained_keys": sorted(self.retained_keys),
            "computed_at": self.computed_at,
            "change_count": self.change_count(),
            "retention_ratio": self.retention_ratio(),
        }


# ---------------------------------------------------------------------------
# EpochStore — mutable manager
# ---------------------------------------------------------------------------

@dataclass
class EpochStore:
    """Mutable registry that stores and indexes all ``EpochRecord`` objects.

    ``EpochStore`` is the single source of truth for module epoch
    history.  It manages three internal indices:

    * ``_epochs`` — primary store keyed by ``epoch_id``.
    * ``_module_epochs`` — secondary index mapping each module name to
      the ordered list of its ``epoch_id`` strings.
    * ``_counters`` — tracks the current (highest) epoch number for
      each module so that the next epoch number can be computed in O(1).

    The store is intentionally not thread-safe; callers that need
    concurrency should use an external lock.

    Attributes:
        _epochs: Primary dict mapping epoch_id → EpochRecord.
        _module_epochs: Secondary dict mapping module_name → [epoch_id, ...].
        _counters: Dict mapping module_name → current epoch number.
    """

    _epochs: dict[str, EpochRecord] = field(default_factory=dict)
    _module_epochs: dict[str, list[str]] = field(default_factory=dict)
    _counters: dict[str, int] = field(default_factory=dict)

    def record_epoch(
        self,
        module_name: str,
        binding_keys: frozenset[str],
        kind: EpochKind,
        parent_id: str | None = None,
    ) -> EpochRecord:
        """Create and store a new epoch record for a module.

        Computes the snapshot hash from ``binding_keys``, determines
        the next epoch number by incrementing the module counter,
        constructs an ``EpochRecord``, and stores it in both indices.

        Args:
            module_name: Fully-qualified Python module name.
            binding_keys: Complete set of top-level names exported by
                the module at this epoch.
            kind: The ``EpochKind`` that triggered this boundary.
            parent_id: ``epoch_id`` of the preceding epoch, or ``None``
                for the very first epoch of a module.

        Returns:
            The newly created and stored ``EpochRecord``.

        Raises:
            ValueError: If ``module_name`` is empty.

        Example::

            store = EpochStore()
            rec = store.record_epoch("app.models", frozenset({"User", "Post"}),
                                     EpochKind.INITIAL)
            assert rec.epoch_number == 0
            assert rec.kind == EpochKind.INITIAL
        """
        if not module_name:
            raise ValueError("module_name must be a non-empty string")

        snapshot_hash = _hash_keys(binding_keys)
        epoch_number = self._counters.get(module_name, -1) + 1
        self._counters[module_name] = epoch_number

        epoch_id = _new_epoch_id()
        rec = EpochRecord(
            epoch_id=epoch_id,
            module_name=module_name,
            epoch_number=epoch_number,
            snapshot_hash=snapshot_hash,
            binding_keys=binding_keys,
            created_at=time.time(),
            parent_epoch_id=parent_id,
            kind=kind,
        )

        self._epochs[epoch_id] = rec
        self._module_epochs.setdefault(module_name, []).append(epoch_id)

        _log.debug(
            "Recorded epoch %s for module %r (kind=%s, keys=%d, hash=%s)",
            rec.label(),
            module_name,
            kind.value,
            len(binding_keys),
            snapshot_hash,
        )
        return rec

    def get_epoch(self, epoch_id: str) -> EpochRecord | None:
        """Retrieve a specific epoch record by its unique ID.

        Args:
            epoch_id: The ``epoch_id`` to look up.

        Returns:
            The matching ``EpochRecord``, or ``None`` if not found.

        Example::

            rec = store.get_epoch("nonexistent")
            assert rec is None
        """
        result = self._epochs.get(epoch_id)
        if result is None:
            _log.debug("get_epoch: epoch_id %r not found", epoch_id)
        return result

    def latest_epoch(self, module_name: str) -> EpochRecord | None:
        """Return the most recent epoch record for a module.

        Args:
            module_name: The module whose latest epoch should be
                retrieved.

        Returns:
            The most recently recorded ``EpochRecord`` for
            ``module_name``, or ``None`` if the module has no epochs.

        Example::

            latest = store.latest_epoch("app.models")
            assert latest is not None
            assert latest.module_name == "app.models"
        """
        ids = self._module_epochs.get(module_name)
        if not ids:
            _log.debug("latest_epoch: no epochs for module %r", module_name)
            return None
        return self._epochs.get(ids[-1])

    def epoch_history(self, module_name: str) -> list[EpochRecord]:
        """Return all epoch records for a module in chronological order.

        Args:
            module_name: The module whose history should be returned.

        Returns:
            Ordered list of ``EpochRecord`` objects from epoch 0
            upwards.  Returns an empty list if the module is unknown.

        Example::

            history = store.epoch_history("app.models")
            assert history[0].epoch_number == 0
        """
        ids = self._module_epochs.get(module_name, [])
        records: list[EpochRecord] = []
        for eid in ids:
            rec = self._epochs.get(eid)
            if rec is not None:
                records.append(rec)
        _log.debug(
            "epoch_history: %d epochs found for module %r", len(records), module_name
        )
        return records

    def increment_epoch(
        self, module_name: str, new_keys: frozenset[str]
    ) -> EpochRecord:
        """Record a forward hot-reload epoch for a module.

        Fetches the latest epoch for ``module_name`` (if any) to use
        as the parent, then calls :meth:`record_epoch` with
        ``EpochKind.INCREMENT``.

        Args:
            module_name: The module being reloaded.
            new_keys: The complete set of binding keys after the reload.

        Returns:
            The newly created ``EpochRecord`` with ``kind=INCREMENT``.

        Raises:
            ValueError: If ``module_name`` is empty.

        Example::

            store.record_epoch("app.utils", frozenset({"a"}), EpochKind.INITIAL)
            rec = store.increment_epoch("app.utils", frozenset({"a", "b"}))
            assert rec.epoch_number == 1
            assert "b" in rec.binding_keys
        """
        latest = self.latest_epoch(module_name)
        parent_id = latest.epoch_id if latest is not None else None
        _log.debug(
            "increment_epoch: %r parent=%r keys=%d",
            module_name,
            parent_id,
            len(new_keys),
        )
        return self.record_epoch(module_name, new_keys, EpochKind.INCREMENT, parent_id)

    def rollback_epoch(
        self, module_name: str, target_number: int
    ) -> EpochRecord | None:
        """Create a new ROLLBACK epoch that reinstates a historical snapshot.

        Searches the epoch history of ``module_name`` for the record
        with ``epoch_number == target_number``, then records a new
        ``ROLLBACK`` epoch whose ``binding_keys`` are copied from that
        historical snapshot.

        Args:
            module_name: The module to roll back.
            target_number: The epoch number to revert to.

        Returns:
            The newly created ``EpochRecord`` with ``kind=ROLLBACK``,
            or ``None`` if ``target_number`` is not found in the
            module's history.

        Raises:
            ValueError: If ``module_name`` is empty.

        Example::

            store.record_epoch("app.cfg", frozenset({"A"}), EpochKind.INITIAL)
            store.increment_epoch("app.cfg", frozenset({"A", "B"}))
            rb = store.rollback_epoch("app.cfg", 0)
            assert rb is not None
            assert rb.binding_keys == frozenset({"A"})
        """
        history = self.epoch_history(module_name)
        target_rec: EpochRecord | None = None
        for rec in history:
            if rec.epoch_number == target_number:
                target_rec = rec
                break

        if target_rec is None:
            _log.warning(
                "rollback_epoch: target epoch %d not found for module %r",
                target_number,
                module_name,
            )
            return None

        latest = self.latest_epoch(module_name)
        parent_id = latest.epoch_id if latest is not None else None
        _log.debug(
            "rollback_epoch: %r → epoch %d (restoring %d keys)",
            module_name,
            target_number,
            len(target_rec.binding_keys),
        )
        return self.record_epoch(
            module_name, target_rec.binding_keys, EpochKind.ROLLBACK, parent_id
        )

    def all_modules(self) -> list[str]:
        """Return a sorted list of all module names known to the store.

        Returns:
            Sorted list of module name strings.

        Example::

            modules = store.all_modules()
            assert modules == sorted(modules)
        """
        return sorted(self._module_epochs.keys())

    def epoch_delta(self, epoch_a_id: str, epoch_b_id: str) -> dict[str, Any]:
        """Compute the key-level diff between two epochs.

        Retrieves the two ``EpochRecord`` objects, computes added /
        removed / retained key sets, constructs an ``EpochDelta``, and
        returns it as a dictionary.

        Args:
            epoch_a_id: The ``epoch_id`` of the *from* epoch.
            epoch_b_id: The ``epoch_id`` of the *to* epoch.

        Returns:
            A dict produced by ``EpochDelta.to_dict()``, or a dict with
            an ``"error"`` key if either epoch_id is not found.

        Example::

            result = store.epoch_delta(rec_a.epoch_id, rec_b.epoch_id)
            assert "added_keys" in result
        """
        rec_a = self._epochs.get(epoch_a_id)
        rec_b = self._epochs.get(epoch_b_id)

        if rec_a is None:
            _log.warning("epoch_delta: epoch_a_id %r not found", epoch_a_id)
            return {"error": f"epoch_a_id {epoch_a_id!r} not found"}
        if rec_b is None:
            _log.warning("epoch_delta: epoch_b_id %r not found", epoch_b_id)
            return {"error": f"epoch_b_id {epoch_b_id!r} not found"}

        added = rec_b.binding_keys - rec_a.binding_keys
        removed = rec_a.binding_keys - rec_b.binding_keys
        retained = rec_a.binding_keys & rec_b.binding_keys

        delta = EpochDelta(
            delta_id=_new_epoch_id(),
            from_epoch=rec_a.epoch_number,
            to_epoch=rec_b.epoch_number,
            added_keys=added,
            removed_keys=removed,
            retained_keys=retained,
            computed_at=time.time(),
        )
        _log.debug(
            "epoch_delta: %r→%r added=%d removed=%d retained=%d",
            epoch_a_id,
            epoch_b_id,
            len(added),
            len(removed),
            len(retained),
        )
        return delta.to_dict()

    def prune_old_epochs(self, module_name: str, keep_count: int) -> int:
        """Remove all but the most recent ``keep_count`` epochs for a module.

        Epochs beyond the keep window are deleted from both ``_epochs``
        and ``_module_epochs``.  The epoch counter is *not* reset so
        that epoch numbers remain monotonically increasing after pruning.

        Args:
            module_name: The module whose epochs should be pruned.
            keep_count: How many recent epochs to retain.  Must be ≥ 1.

        Returns:
            The number of epoch records that were removed.

        Raises:
            ValueError: If ``keep_count < 1``.

        Example::

            pruned = store.prune_old_epochs("app.utils", 3)
            assert len(store.epoch_history("app.utils")) <= 3
        """
        if keep_count < 1:
            raise ValueError(f"keep_count must be ≥ 1, got {keep_count}")

        ids = self._module_epochs.get(module_name, [])
        excess = len(ids) - keep_count
        if excess <= 0:
            _log.debug(
                "prune_old_epochs: nothing to prune for %r (have %d, keep %d)",
                module_name,
                len(ids),
                keep_count,
            )
            return 0

        to_remove = ids[:excess]
        self._module_epochs[module_name] = ids[excess:]

        removed_count = 0
        for eid in to_remove:
            if eid in self._epochs:
                del self._epochs[eid]
                removed_count += 1

        _log.debug(
            "prune_old_epochs: pruned %d epochs for module %r", removed_count, module_name
        )
        return removed_count

    def stats(self) -> dict[str, Any]:
        """Compute aggregate statistics about the epoch store.

        Returns a dictionary with keys:

        * ``total_epochs`` — total number of ``EpochRecord`` objects.
        * ``total_modules`` — number of distinct modules tracked.
        * ``avg_epoch_depth`` — mean number of epochs per module.
        * ``max_epoch_depth`` — maximum epoch count for any single module.
        * ``kind_distribution`` — mapping of kind value → count.
        * ``modules`` — list of module names with their epoch counts.

        Returns:
            A stats dictionary.

        Example::

            s = store.stats()
            assert s["total_epochs"] >= 0
        """
        total_epochs = len(self._epochs)
        total_modules = len(self._module_epochs)

        depths = [len(ids) for ids in self._module_epochs.values()]
        avg_depth = sum(depths) / len(depths) if depths else 0.0
        max_depth = max(depths) if depths else 0

        kind_dist: dict[str, int] = {}
        for rec in self._epochs.values():
            kind_dist[rec.kind.value] = kind_dist.get(rec.kind.value, 0) + 1

        modules_info = [
            {"module": name, "epoch_count": len(ids)}
            for name, ids in sorted(self._module_epochs.items())
        ]

        return {
            "total_epochs": total_epochs,
            "total_modules": total_modules,
            "avg_epoch_depth": round(avg_depth, 4),
            "max_epoch_depth": max_depth,
            "kind_distribution": kind_dist,
            "modules": modules_info,
        }

    def find_epochs_by_kind(self, kind: EpochKind) -> list[EpochRecord]:
        """Return all epoch records that match a given ``EpochKind``.

        Args:
            kind: The ``EpochKind`` to filter on.

        Returns:
            A list of ``EpochRecord`` objects whose ``kind`` matches.
            The list is ordered by creation timestamp ascending.

        Example::

            rollbacks = store.find_epochs_by_kind(EpochKind.ROLLBACK)
        """
        matching = [
            rec for rec in self._epochs.values() if rec.kind == kind
        ]
        matching.sort(key=lambda r: r.created_at)
        _log.debug(
            "find_epochs_by_kind(%s): found %d records", kind.value, len(matching)
        )
        return matching

    def epoch_chain(self, epoch_id: str) -> list[EpochRecord]:
        """Follow the parent chain from a given epoch back to the root.

        Traverses ``parent_epoch_id`` links iteratively, collecting each
        ancestor.  Terminates when a record has no parent or an ID is
        not found.  The returned list is ordered from root (oldest)
        to ``epoch_id`` (newest).

        Args:
            epoch_id: The ``epoch_id`` to start from.

        Returns:
            Ordered list of ``EpochRecord`` objects from the root
            ancestor to the given epoch.  Returns an empty list if
            ``epoch_id`` is not found.

        Raises:
            RuntimeError: If a cycle is detected in the parent chain
                (should not occur in normal usage).

        Example::

            chain = store.epoch_chain(rec3.epoch_id)
            assert chain[0].is_initial()
            assert chain[-1].epoch_id == rec3.epoch_id
        """
        start = self._epochs.get(epoch_id)
        if start is None:
            _log.warning("epoch_chain: epoch_id %r not found", epoch_id)
            return []

        visited: set[str] = set()
        chain: deque[EpochRecord] = deque()
        current: EpochRecord | None = start

        while current is not None:
            if current.epoch_id in visited:
                raise RuntimeError(
                    f"Cycle detected in epoch chain at {current.epoch_id!r}"
                )
            visited.add(current.epoch_id)
            chain.appendleft(current)
            if current.parent_epoch_id is None:
                break
            current = self._epochs.get(current.parent_epoch_id)

        _log.debug(
            "epoch_chain: chain length %d ending at %r", len(chain), epoch_id
        )
        return list(chain)


# ---------------------------------------------------------------------------
# ObjectSummaryAnalyzer — mutable manager
# ---------------------------------------------------------------------------

@dataclass
class ObjectSummaryAnalyzer:
    """Tracks the attribute-level evolution of live objects across epochs.

    Maintains a per-object history of ``ObjectSummary`` records and
    provides analytics for detecting attribute churn, finding stable
    and volatile objects, and computing pairwise attribute similarity.

    Attributes:
        _summaries: Dict mapping object_id → ordered list of
            ``ObjectSummary`` records.
        _current_epoch: The epoch number that will be assigned to new
            summaries when ``epoch_number`` is not explicitly provided.
    """

    _summaries: dict[str, list[ObjectSummary]] = field(default_factory=dict)
    _current_epoch: int = field(default=0)

    def summarize(
        self,
        obj_id: str,
        type_name: str,
        attribute_keys: frozenset[str],
        epoch_number: int,
    ) -> ObjectSummary:
        """Create and record an ``ObjectSummary`` for a live object.

        Computes the ``attribute_hash`` from ``attribute_keys``,
        constructs an ``ObjectSummary``, appends it to the object's
        history, and returns it.

        Args:
            obj_id: Application-level identifier of the object.
            type_name: The ``__class__.__name__`` of the object.
            attribute_keys: Frozenset of attribute names on the object.
            epoch_number: The module epoch at which this observation
                was made.

        Returns:
            The newly created ``ObjectSummary``.

        Example::

            summary = analyzer.summarize(
                "cfg:1", "Config", frozenset({"host", "port"}), epoch_number=0
            )
            assert summary.attribute_count() == 2
        """
        attribute_hash = _hash_keys(attribute_keys) if attribute_keys else "0" * 16
        summary = ObjectSummary(
            summary_id=_new_summary_id(),
            object_id=obj_id,
            type_name=type_name,
            attribute_keys=attribute_keys,
            epoch_number=epoch_number,
            created_at=time.time(),
            attribute_hash=attribute_hash,
        )
        self._summaries.setdefault(obj_id, []).append(summary)
        _log.debug(
            "summarize: recorded %s (attrs=%d, hash=%s)",
            summary.label(),
            len(attribute_keys),
            attribute_hash,
        )
        return summary

    def compare_summaries(
        self, s1: ObjectSummary, s2: ObjectSummary
    ) -> dict[str, Any]:
        """Compute the attribute-level diff between two object summaries.

        Computes added, removed, and retained attribute sets and notes
        whether the type name changed between the two observations.

        Args:
            s1: The *from* summary (earlier observation).
            s2: The *to* summary (later observation).

        Returns:
            A dictionary with keys:

            * ``added_attributes`` — sorted list of new attributes.
            * ``removed_attributes`` — sorted list of dropped attributes.
            * ``retained_attributes`` — sorted list of unchanged attrs.
            * ``type_changed`` — bool, True if type_name differs.
            * ``epoch_delta`` — ``s2.epoch_number - s1.epoch_number``.
            * ``hash_changed`` — bool, True if attribute_hash differs.

        Example::

            diff = analyzer.compare_summaries(s_old, s_new)
            assert isinstance(diff["added_attributes"], list)
        """
        added = s2.attribute_keys - s1.attribute_keys
        removed = s1.attribute_keys - s2.attribute_keys
        retained = s1.attribute_keys & s2.attribute_keys

        type_changed = s1.type_name != s2.type_name
        epoch_diff = s2.epoch_number - s1.epoch_number
        hash_changed = s1.attribute_hash != s2.attribute_hash

        _log.debug(
            "compare_summaries: %s→%s added=%d removed=%d retained=%d",
            s1.label(),
            s2.label(),
            len(added),
            len(removed),
            len(retained),
        )
        return {
            "added_attributes": sorted(added),
            "removed_attributes": sorted(removed),
            "retained_attributes": sorted(retained),
            "type_changed": type_changed,
            "epoch_delta": epoch_diff,
            "hash_changed": hash_changed,
        }

    def track_object_evolution(self, obj_id: str) -> list[ObjectSummary]:
        """Return the full ordered history of summaries for an object.

        Args:
            obj_id: The object identifier to look up.

        Returns:
            Ordered list of ``ObjectSummary`` records from first
            observation to latest.  Returns an empty list if the object
            is not tracked.

        Example::

            history = analyzer.track_object_evolution("cfg:1")
            assert len(history) >= 1
        """
        history = self._summaries.get(obj_id, [])
        _log.debug(
            "track_object_evolution: %r has %d summaries", obj_id, len(history)
        )
        return list(history)

    def detect_attribute_churn(self, obj_id: str) -> float:
        """Compute average attribute change count between consecutive summaries.

        Iterates over consecutive (previous, current) pairs of
        summaries for ``obj_id`` and computes the mean of
        ``|added| + |removed|`` over all adjacent pairs.

        Args:
            obj_id: The object identifier to analyse.

        Returns:
            Average churn as a non-negative float.  Returns ``0.0`` if
            the object has fewer than 2 summaries.

        Example::

            churn = analyzer.detect_attribute_churn("cfg:1")
            assert churn >= 0.0
        """
        history = self._summaries.get(obj_id, [])
        if len(history) < 2:
            _log.debug(
                "detect_attribute_churn: not enough summaries for %r", obj_id
            )
            return 0.0

        total_changes = 0
        pair_count = len(history) - 1
        for prev, curr in zip(history[:-1], history[1:]):
            added = len(curr.attribute_keys - prev.attribute_keys)
            removed = len(prev.attribute_keys - curr.attribute_keys)
            total_changes += added + removed

        churn = total_changes / pair_count
        _log.debug(
            "detect_attribute_churn: %r churn=%.4f over %d pairs",
            obj_id,
            churn,
            pair_count,
        )
        return churn

    def objects_changed_in_epoch(self, epoch_number: int) -> list[str]:
        """Return the IDs of objects that have a summary recorded at ``epoch_number``.

        Args:
            epoch_number: The epoch to query.

        Returns:
            Sorted list of ``object_id`` strings that were observed
            during ``epoch_number``.

        Example::

            changed = analyzer.objects_changed_in_epoch(2)
            assert isinstance(changed, list)
        """
        result: list[str] = []
        for obj_id, summaries in self._summaries.items():
            for s in summaries:
                if s.epoch_number == epoch_number:
                    result.append(obj_id)
                    break
        result.sort()
        _log.debug(
            "objects_changed_in_epoch(%d): %d objects", epoch_number, len(result)
        )
        return result

    def export_summaries(self) -> list[dict[str, Any]]:
        """Flatten all object summaries into a list of dictionaries.

        Iterates over every object and every summary in chronological
        order, calling ``to_dict()`` on each.

        Returns:
            A flat list of summary dictionaries sorted by
            ``(object_id, epoch_number)``.

        Example::

            exported = analyzer.export_summaries()
            assert all("summary_id" in d for d in exported)
        """
        flat: list[dict[str, Any]] = []
        for summaries in self._summaries.values():
            for s in summaries:
                flat.append(s.to_dict())
        flat.sort(key=lambda d: (d["object_id"], d["epoch_number"]))
        _log.debug("export_summaries: exported %d records", len(flat))
        return flat

    def summary_stats(self) -> dict[str, Any]:
        """Compute aggregate statistics about tracked object summaries.

        Returns:
            A dictionary with keys:

            * ``total_summaries`` — total number of ``ObjectSummary`` records.
            * ``unique_objects`` — number of distinct objects tracked.
            * ``avg_attributes`` — mean attribute count per summary.
            * ``avg_churn`` — mean churn rate across all objects.
            * ``max_churn_object`` — object_id with highest churn (or None).
            * ``epochs_represented`` — sorted list of distinct epoch numbers.

        Example::

            stats = analyzer.summary_stats()
            assert stats["unique_objects"] >= 0
        """
        all_summaries = [s for sl in self._summaries.values() for s in sl]
        total = len(all_summaries)
        unique = len(self._summaries)

        avg_attrs = (
            sum(s.attribute_count() for s in all_summaries) / total
            if total > 0
            else 0.0
        )

        churns = {obj_id: self.detect_attribute_churn(obj_id) for obj_id in self._summaries}
        avg_churn = sum(churns.values()) / len(churns) if churns else 0.0
        max_churn_object = max(churns, key=churns.__getitem__) if churns else None

        epochs_seen: set[int] = set()
        for s in all_summaries:
            epochs_seen.add(s.epoch_number)

        return {
            "total_summaries": total,
            "unique_objects": unique,
            "avg_attributes": round(avg_attrs, 4),
            "avg_churn": round(avg_churn, 4),
            "max_churn_object": max_churn_object,
            "epochs_represented": sorted(epochs_seen),
        }

    def most_stable_objects(self, top_n: int = 5) -> list[str]:
        """Return the ``top_n`` object IDs with the lowest attribute churn.

        Objects with only one summary (churn = 0.0) are included and
        will naturally rank among the most stable.

        Args:
            top_n: Maximum number of objects to return.  Defaults to 5.

        Returns:
            List of object_id strings sorted by churn ascending,
            truncated to ``top_n``.

        Example::

            stable = analyzer.most_stable_objects(3)
            assert len(stable) <= 3
        """
        churns = {
            obj_id: self.detect_attribute_churn(obj_id)
            for obj_id in self._summaries
        }
        sorted_ids = sorted(churns, key=churns.__getitem__)
        result = sorted_ids[:top_n]
        _log.debug("most_stable_objects(top_n=%d): %s", top_n, result)
        return result

    def most_volatile_objects(self, top_n: int = 5) -> list[str]:
        """Return the ``top_n`` object IDs with the highest attribute churn.

        Args:
            top_n: Maximum number of objects to return.  Defaults to 5.

        Returns:
            List of object_id strings sorted by churn descending,
            truncated to ``top_n``.

        Example::

            volatile = analyzer.most_volatile_objects(3)
            assert len(volatile) <= 3
        """
        churns = {
            obj_id: self.detect_attribute_churn(obj_id)
            for obj_id in self._summaries
        }
        sorted_ids = sorted(churns, key=churns.__getitem__, reverse=True)
        result = sorted_ids[:top_n]
        _log.debug("most_volatile_objects(top_n=%d): %s", top_n, result)
        return result

    def attribute_overlap_matrix(self) -> dict[str, dict[str, float]]:
        """Compute pairwise Jaccard attribute similarity for all tracked objects.

        For each pair of distinct objects, takes their *latest* summary
        and computes the Jaccard index:

            J(A, B) = |A ∩ B| / |A ∪ B|

        Returns a nested dict ``{obj_a: {obj_b: jaccard_score}}``.
        Diagonal entries (same object) are omitted.  The matrix is
        symmetric: ``result[a][b] == result[b][a]``.

        Returns:
            Nested dict of Jaccard similarity scores.

        Example::

            matrix = analyzer.attribute_overlap_matrix()
            # All values are in [0.0, 1.0]
            for row in matrix.values():
                assert all(0.0 <= v <= 1.0 for v in row.values())
        """
        obj_ids = sorted(self._summaries.keys())
        latest: dict[str, frozenset[str]] = {}
        for obj_id in obj_ids:
            summaries = self._summaries[obj_id]
            if summaries:
                latest[obj_id] = summaries[-1].attribute_keys

        matrix: dict[str, dict[str, float]] = {oid: {} for oid in obj_ids}
        for i, a in enumerate(obj_ids):
            for b in obj_ids[i + 1 :]:
                keys_a = latest.get(a, frozenset())
                keys_b = latest.get(b, frozenset())
                union = keys_a | keys_b
                intersection = keys_a & keys_b
                jaccard = len(intersection) / len(union) if union else 0.0
                matrix[a][b] = round(jaccard, 6)
                matrix[b][a] = round(jaccard, 6)

        _log.debug(
            "attribute_overlap_matrix: computed %d×%d matrix",
            len(obj_ids),
            len(obj_ids),
        )
        return matrix


# ---------------------------------------------------------------------------
# EpochIndexedModuleObjectCoordinator — high-level facade
# ---------------------------------------------------------------------------

@dataclass
class EpochIndexedModuleObjectCoordinator:
    """High-level coordinator that integrates epoch tracking and object summaries.

    Combines an ``EpochStore`` and an ``ObjectSummaryAnalyzer`` under a
    single facade.  Exposes the most common operations (module
    registration, update, object snapshotting, reporting) as simple
    methods while keeping the underlying stores accessible for
    advanced use.

    Attributes:
        store: The ``EpochStore`` instance managing module epochs.
        analyzer: The ``ObjectSummaryAnalyzer`` instance tracking
            live object attribute evolution.
        _session_id: A random 12-character hex string identifying this
            coordinator session.
        _created_at: POSIX timestamp when this coordinator was created.
    """

    store: EpochStore = field(default_factory=EpochStore)
    analyzer: ObjectSummaryAnalyzer = field(default_factory=ObjectSummaryAnalyzer)
    _session_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    _created_at: float = field(default_factory=time.time)

    def register_module(
        self, module_name: str, binding_keys: frozenset[str]
    ) -> EpochRecord:
        """Register a module for the first time, creating its epoch 0 record.

        Delegates to ``store.record_epoch`` with ``EpochKind.INITIAL``
        and no parent epoch.

        Args:
            module_name: Fully-qualified Python module name.
            binding_keys: Initial set of top-level binding names.

        Returns:
            The ``EpochRecord`` created for epoch 0 of this module.

        Raises:
            ValueError: If ``module_name`` is empty.

        Example::

            coord = EpochIndexedModuleObjectCoordinator()
            rec = coord.register_module("myapp.cfg", frozenset({"HOST", "PORT"}))
            assert rec.epoch_number == 0
            assert rec.is_initial()
        """
        _log.info(
            "register_module: %r with %d initial keys", module_name, len(binding_keys)
        )
        return self.store.record_epoch(module_name, binding_keys, EpochKind.INITIAL)

    def update_module(
        self, module_name: str, new_keys: frozenset[str]
    ) -> EpochRecord:
        """Increment the epoch for a module after a hot reload.

        Delegates to ``store.increment_epoch``.

        Args:
            module_name: The module that was reloaded.
            new_keys: The complete set of binding keys after the reload.

        Returns:
            The newly created ``EpochRecord`` with ``kind=INCREMENT``.

        Example::

            rec = coord.update_module("myapp.cfg", frozenset({"HOST", "PORT", "DEBUG"}))
            assert rec.kind == EpochKind.INCREMENT
        """
        _log.info(
            "update_module: %r → new_keys=%d", module_name, len(new_keys)
        )
        return self.store.increment_epoch(module_name, new_keys)

    def snapshot_object(
        self, obj_id: str, type_name: str, attributes: frozenset[str]
    ) -> ObjectSummary:
        """Record an attribute-level snapshot of a live object.

        Determines the current epoch number by querying the epoch
        counter for the session (using the total number of epochs
        across all modules as a proxy), then calls
        ``analyzer.summarize``.

        Args:
            obj_id: Application-level identifier of the object.
            type_name: The ``__class__.__name__`` of the object.
            attributes: Frozenset of attribute names to record.

        Returns:
            The newly created ``ObjectSummary``.

        Example::

            summary = coord.snapshot_object(
                "req:99", "Request", frozenset({"method", "path", "headers"})
            )
            assert summary.type_name == "Request"
        """
        store_stats = self.store.stats()
        current_epoch = store_stats.get("total_epochs", 0)
        _log.debug(
            "snapshot_object: %r type=%r attrs=%d epoch=%d",
            obj_id,
            type_name,
            len(attributes),
            current_epoch,
        )
        return self.analyzer.summarize(obj_id, type_name, attributes, current_epoch)

    def diff_module_epochs(
        self, module_name: str, from_num: int, to_num: int
    ) -> dict[str, Any]:
        """Compute the binding-key diff between two named epochs of a module.

        Finds the ``EpochRecord`` objects for ``from_num`` and
        ``to_num`` in the module's history and delegates to
        ``store.epoch_delta``.

        Args:
            module_name: The module to diff.
            from_num: Source epoch number.
            to_num: Destination epoch number.

        Returns:
            A delta dictionary as returned by ``EpochDelta.to_dict()``,
            or a dict with an ``"error"`` key if either epoch number is
            not found.

        Example::

            result = coord.diff_module_epochs("myapp.cfg", 0, 1)
            assert "added_keys" in result or "error" in result
        """
        history = self.store.epoch_history(module_name)
        rec_from: EpochRecord | None = None
        rec_to: EpochRecord | None = None

        for rec in history:
            if rec.epoch_number == from_num:
                rec_from = rec
            if rec.epoch_number == to_num:
                rec_to = rec

        if rec_from is None:
            _log.warning(
                "diff_module_epochs: epoch %d not found for %r", from_num, module_name
            )
            return {"error": f"epoch {from_num} not found for module {module_name!r}"}
        if rec_to is None:
            _log.warning(
                "diff_module_epochs: epoch %d not found for %r", to_num, module_name
            )
            return {"error": f"epoch {to_num} not found for module {module_name!r}"}

        return self.store.epoch_delta(rec_from.epoch_id, rec_to.epoch_id)

    def full_report(self) -> dict[str, Any]:
        """Generate a comprehensive report of the coordinator's state.

        Combines statistics from the epoch store, the object summary
        analyzer, and session metadata into a single dictionary.

        Returns:
            A dictionary containing:

            * ``session_id`` — this coordinator's session identifier.
            * ``session_age_s`` — seconds since this coordinator was
              created.
            * ``epoch_store`` — dict from ``store.stats()``.
            * ``object_summaries`` — dict from
              ``analyzer.summary_stats()``.
            * ``generated_at`` — POSIX timestamp of report generation.

        Example::

            report = coord.full_report()
            assert "session_id" in report
            assert "epoch_store" in report
        """
        now = time.time()
        store_stats = self.store.stats()
        summary_stats = self.analyzer.summary_stats()

        report = {
            "session_id": self._session_id,
            "session_age_s": round(now - self._created_at, 4),
            "epoch_store": store_stats,
            "object_summaries": summary_stats,
            "generated_at": now,
        }
        _log.debug(
            "full_report: session=%r age=%.2fs epochs=%d objects=%d",
            self._session_id,
            report["session_age_s"],
            store_stats.get("total_epochs", 0),
            summary_stats.get("unique_objects", 0),
        )
        return report

    def reset(self) -> None:
        """Reinitialise the epoch store and object summary analyzer.

        Replaces both ``store`` and ``analyzer`` with fresh instances,
        discarding all recorded state.  The session ID and creation
        timestamp are preserved.

        Example::

            coord.register_module("app.x", frozenset({"A"}))
            coord.reset()
            assert coord.store.stats()["total_epochs"] == 0
        """
        _log.warning(
            "reset: discarding all state for session %r", self._session_id
        )
        self.store = EpochStore()
        self.analyzer = ObjectSummaryAnalyzer()
        _log.info("reset: store and analyzer reinitialised")

    def module_evolution_summary(self, module_name: str) -> dict[str, Any]:
        """Produce a detailed evolution report for a single module.

        Returns the ordered epoch history along with the delta between
        each consecutive pair of epochs.

        Args:
            module_name: The module to summarise.

        Returns:
            A dictionary with:

            * ``module_name`` — echoes the input.
            * ``epoch_count`` — total epochs recorded.
            * ``epochs`` — list of ``EpochRecord.to_dict()`` dicts.
            * ``deltas`` — list of consecutive-epoch delta dicts.
            * ``latest_snapshot_hash`` — hash of the most recent epoch,
              or ``None``.

        Example::

            evo = coord.module_evolution_summary("myapp.cfg")
            assert evo["module_name"] == "myapp.cfg"
        """
        history = self.store.epoch_history(module_name)
        epoch_dicts = [rec.to_dict() for rec in history]

        deltas: list[dict[str, Any]] = []
        for prev, curr in zip(history[:-1], history[1:]):
            delta = self.store.epoch_delta(prev.epoch_id, curr.epoch_id)
            deltas.append(delta)

        latest_hash: str | None = None
        if history:
            latest_hash = history[-1].snapshot_hash

        _log.debug(
            "module_evolution_summary: %r epochs=%d deltas=%d",
            module_name,
            len(history),
            len(deltas),
        )
        return {
            "module_name": module_name,
            "epoch_count": len(history),
            "epochs": epoch_dicts,
            "deltas": deltas,
            "latest_snapshot_hash": latest_hash,
        }

    def cross_module_epoch_alignment(self) -> dict[str, Any]:
        """Compare epoch counts and depths across all registered modules.

        Identifies modules that are ahead of or behind the median epoch
        depth, and reports which modules have the most rollbacks or
        invalidations.

        Returns:
            A dictionary with:

            * ``modules`` — list of ``{module, epoch_count}`` dicts
              sorted by epoch_count descending.
            * ``median_epoch_count`` — median depth across modules.
            * ``above_median`` — module names above median depth.
            * ``below_median`` — module names at or below median depth.
            * ``rollback_counts`` — mapping of module_name → rollback count.
            * ``invalidation_counts`` — mapping of module_name → invalidation count.

        Example::

            alignment = coord.cross_module_epoch_alignment()
            assert "median_epoch_count" in alignment
        """
        all_mods = self.store.all_modules()
        module_depths: list[dict[str, Any]] = []
        rollback_counts: dict[str, int] = {}
        invalidation_counts: dict[str, int] = {}

        for mod in all_mods:
            history = self.store.epoch_history(mod)
            depth = len(history)
            rb_count = sum(1 for r in history if r.kind == EpochKind.ROLLBACK)
            inv_count = sum(1 for r in history if r.kind == EpochKind.INVALIDATION)
            module_depths.append({"module": mod, "epoch_count": depth})
            rollback_counts[mod] = rb_count
            invalidation_counts[mod] = inv_count

        module_depths.sort(key=lambda d: d["epoch_count"], reverse=True)
        depths_only = [d["epoch_count"] for d in module_depths]

        if depths_only:
            sorted_depths = sorted(depths_only)
            mid = len(sorted_depths) // 2
            if len(sorted_depths) % 2 == 0:
                median_depth = (sorted_depths[mid - 1] + sorted_depths[mid]) / 2.0
            else:
                median_depth = float(sorted_depths[mid])
        else:
            median_depth = 0.0

        above = [d["module"] for d in module_depths if d["epoch_count"] > median_depth]
        below = [d["module"] for d in module_depths if d["epoch_count"] <= median_depth]

        _log.debug(
            "cross_module_epoch_alignment: %d modules, median=%.1f",
            len(all_mods),
            median_depth,
        )
        return {
            "modules": module_depths,
            "median_epoch_count": median_depth,
            "above_median": above,
            "below_median": below,
            "rollback_counts": rollback_counts,
            "invalidation_counts": invalidation_counts,
        }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    "EpochKind",
    "EpochRecord",
    "ObjectSummary",
    "EpochDelta",
    "EpochStore",
    "ObjectSummaryAnalyzer",
    "EpochIndexedModuleObjectCoordinator",
]

# copilot: s02 — Epoch-Indexed Module and Object Summaries (Ch23 §2)
