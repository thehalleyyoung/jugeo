from __future__ import annotations

"""s05 — Hot Reload and Development Mode Semantics (Ch23 §5).

Hot reload = epoch increment in JuGeo's sheaf model.  Every time a module
is reloaded in development mode, its binding snapshot advances to the next
epoch.  This module implements HotReloadEngine, DevModeWitness, and
HotReloadDevelopmentModeCoordinator for tracking live reloads and their
semantic consequences.

The epoch-based model ensures that every hot reload is uniquely stamped in
time, making it possible to replay, rollback, or diff any two snapshots of
a module's binding set.  The DevModeWitness records a chronological
observation log, while HotReloadEngine maintains the authoritative per-module
reload history.  Together they form the backbone of JuGeo's live development
infrastructure.
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
    from jugeo.sheaf import EpochSheaf  # type: ignore
except ImportError:
    class EpochSheaf:  # type: ignore
        """Inline stub for EpochSheaf when jugeo.sheaf is unavailable.

        In a full JuGeo runtime this class wraps the sheaf-theoretic
        binding model and provides epoch-stamped snapshots of module
        state.  This stub allows the module to be imported in isolation
        or in test environments where the full runtime is not installed.
        """

        def __init__(self) -> None:
            pass

try:
    from jugeo.devtools import HotReloadContext  # type: ignore
except ImportError:
    class HotReloadContext:  # type: ignore
        """Inline stub for HotReloadContext when jugeo.devtools is unavailable.

        HotReloadContext in the full runtime carries the metadata needed
        to drive a hot-reload cycle: file watcher callbacks, invalidation
        queues, and the dev-server WebSocket connection.  This stub
        satisfies type-checker requirements without any live functionality.
        """

        def __init__(self) -> None:
            pass


_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def _new_event_id() -> str:
    """Generate a unique reload-event identifier.

    Returns:
        A short string of the form ``"rl_"`` followed by 10 random hex
        characters, e.g. ``"rl_3a9f20b1c7"``.

    Example:
        >>> eid = _new_event_id()
        >>> eid.startswith("rl_")
        True
        >>> len(eid)
        13
    """
    return "rl_" + uuid.uuid4().hex[:10]


def _new_state_id() -> str:
    """Generate a unique dev-mode state identifier.

    Returns:
        A short string of the form ``"ds_"`` followed by 10 random hex
        characters, e.g. ``"ds_7b1d04ef2a"``.

    Example:
        >>> sid = _new_state_id()
        >>> sid.startswith("ds_")
        True
        >>> len(sid)
        13
    """
    return "ds_" + uuid.uuid4().hex[:10]


def _new_obs_id() -> str:
    """Generate a unique observation identifier for DevModeWitness entries.

    Returns:
        A short string of the form ``"do_"`` followed by 10 random hex
        characters, e.g. ``"do_c42e8a901f"``.

    Example:
        >>> oid = _new_obs_id()
        >>> oid.startswith("do_")
        True
        >>> len(oid)
        13
    """
    return "do_" + uuid.uuid4().hex[:10]


def _hash_keys(keys: frozenset[str]) -> str:
    """Compute a deterministic fingerprint for a frozenset of binding keys.

    The keys are sorted, joined with ``"|"`` as a separator, then hashed
    with SHA-256.  Only the first 16 hex characters of the digest are
    returned, giving 64 bits of collision resistance — sufficient for
    development-time diagnostics.

    Args:
        keys: The frozenset of string keys whose combined identity should
            be captured in the fingerprint.

    Returns:
        A 16-character lowercase hex string derived from the SHA-256
        digest of the sorted, pipe-delimited key sequence.

    Example:
        >>> h1 = _hash_keys(frozenset({"a", "b", "c"}))
        >>> h2 = _hash_keys(frozenset({"c", "a", "b"}))
        >>> h1 == h2  # order-independent
        True
        >>> len(h1)
        16
    """
    joined = "|".join(sorted(keys))
    digest = hashlib.sha256(joined.encode("utf-8")).hexdigest()
    return digest[:16]


def _compute_diff(
    old_keys: frozenset[str],
    new_keys: frozenset[str],
) -> dict[str, frozenset[str]]:
    """Compute a structural diff between two sets of binding keys.

    Identifies which keys were added, which were removed, which are
    retained (unchanged), and which are "changed" (always empty in this
    key-only model — value-level diffing is left to the caller).

    Args:
        old_keys: The frozenset of keys present in the previous epoch.
        new_keys: The frozenset of keys present in the incoming epoch.

    Returns:
        A dict with four entries:

        - ``"added"``: keys present in *new_keys* but not in *old_keys*.
        - ``"removed"``: keys present in *old_keys* but not in *new_keys*.
        - ``"changed"``: always an empty frozenset in this implementation
          (value-level change detection is out of scope).
        - ``"retained"``: keys present in both sets.

    Example:
        >>> diff = _compute_diff(frozenset({"x", "y"}), frozenset({"y", "z"}))
        >>> diff["added"] == frozenset({"z"})
        True
        >>> diff["removed"] == frozenset({"x"})
        True
        >>> diff["retained"] == frozenset({"y"})
        True
    """
    added: frozenset[str] = new_keys - old_keys
    removed: frozenset[str] = old_keys - new_keys
    retained: frozenset[str] = old_keys & new_keys
    changed: frozenset[str] = frozenset()
    return {
        "added": added,
        "removed": removed,
        "changed": changed,
        "retained": retained,
    }


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class ReloadKind(str, Enum):
    """Classifies the semantic type of a hot-reload operation.

    Each value describes *why* or *how* the reload was triggered, which
    influences downstream handling — for example, a ROLLBACK reload
    swaps the binding direction, while a FORCED reload bypasses
    incremental guards.

    Attributes:
        FULL_RELOAD: The entire module binding set is replaced from
            scratch without attempting to preserve any existing keys.
        INCREMENTAL: Only the keys that have changed since the last
            epoch are updated; unchanged keys are retained in place.
        FORCED: Like FULL_RELOAD but triggered programmatically (e.g.,
            by a developer command) regardless of whether the source
            has actually changed.
        ROLLBACK: The module's epoch is wound back to a previous snapshot,
            reversing any changes made since that epoch.
        HOT_SWAP: A live in-process replacement where the old binding
            object is atomically swapped with the new one without a
            full module tear-down cycle.
    """

    FULL_RELOAD = "full_reload"
    INCREMENTAL = "incremental"
    FORCED = "forced"
    ROLLBACK = "rollback"
    HOT_SWAP = "hot_swap"


class DevSessionPhase(str, Enum):
    """Represents the lifecycle phase of a development session.

    The phase transitions follow a strict state machine:

        INITIALIZING → ACTIVE → RELOADING → ACTIVE → … → PAUSED → TERMINATED

    Attributes:
        INITIALIZING: The session has been created but the first module
            has not yet been registered with the witness.
        ACTIVE: At least one module is under development and the session
            is ready to accept reload events.
        RELOADING: A reload cycle is currently in progress; new reloads
            are queued rather than executed immediately.
        PAUSED: The session has been temporarily suspended, for example
            when the developer switches focus to another task.
        TERMINATED: The session has been explicitly ended; no further
            events will be accepted.
    """

    INITIALIZING = "initializing"
    ACTIVE = "active"
    RELOADING = "reloading"
    PAUSED = "paused"
    TERMINATED = "terminated"


# ---------------------------------------------------------------------------
# Value-type dataclasses (frozen)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ReloadEvent:
    """An immutable record describing a single hot-reload occurrence.

    Each time a module is reloaded, a ReloadEvent is created and stored
    in the HotReloadEngine.  The event captures the before/after epoch
    numbers, the diff of binding keys, and timing metadata.

    Attributes:
        event_id: Unique identifier for this event, generated by
            :func:`_new_event_id`.
        module_name: The fully-qualified name of the reloaded module,
            e.g. ``"mypackage.core.utils"``.
        old_epoch: The epoch number of the module *before* this reload.
        new_epoch: The epoch number of the module *after* this reload.
        changed_keys: Keys whose values are known to have changed
            (may be empty if only structural changes were detected).
        removed_keys: Keys that existed in ``old_epoch`` but are absent
            from ``new_epoch``.
        added_keys: Keys that are new in ``new_epoch`` and were absent
            from ``old_epoch``.
        reload_at: Unix timestamp (seconds) when the reload was initiated.
        dev_mode: Whether the reload occurred inside an active dev session.
        kind: The :class:`ReloadKind` classifying the reload strategy.
        duration_ms: Wall-clock duration of the reload operation in
            milliseconds.
    """

    event_id: str
    module_name: str
    old_epoch: int
    new_epoch: int
    changed_keys: frozenset[str]
    removed_keys: frozenset[str]
    added_keys: frozenset[str]
    reload_at: float
    dev_mode: bool
    kind: ReloadKind
    duration_ms: float

    def total_delta(self) -> int:
        """Return the total number of binding-key changes in this reload.

        Sums the cardinalities of changed_keys, removed_keys, and
        added_keys to give a single measure of "how much changed" during
        this reload cycle.

        Returns:
            Non-negative integer equal to
            ``len(changed_keys) + len(removed_keys) + len(added_keys)``.

        Example:
            >>> ev = ReloadEvent(
            ...     event_id="rl_abc", module_name="mod", old_epoch=0,
            ...     new_epoch=1, changed_keys=frozenset({"a"}),
            ...     removed_keys=frozenset(), added_keys=frozenset({"b", "c"}),
            ...     reload_at=0.0, dev_mode=True, kind=ReloadKind.INCREMENTAL,
            ...     duration_ms=1.5,
            ... )
            >>> ev.total_delta()
            3
        """
        return len(self.changed_keys) + len(self.removed_keys) + len(self.added_keys)

    def is_incremental(self) -> bool:
        """Check whether this reload is a small incremental update.

        An event is considered incremental when its kind is
        :attr:`ReloadKind.INCREMENTAL` *and* the total number of changed
        keys is fewer than 10.  Large incremental reloads (≥10 changes)
        are treated as effectively full reloads for planning purposes.

        Returns:
            ``True`` if the reload kind is INCREMENTAL and fewer than 10
            binding keys changed in total, ``False`` otherwise.

        Example:
            >>> ev = ReloadEvent(
            ...     event_id="rl_x", module_name="m", old_epoch=1,
            ...     new_epoch=2, changed_keys=frozenset(),
            ...     removed_keys=frozenset(), added_keys=frozenset({"a"}),
            ...     reload_at=0.0, dev_mode=True,
            ...     kind=ReloadKind.INCREMENTAL, duration_ms=0.5,
            ... )
            >>> ev.is_incremental()
            True
        """
        return self.kind == ReloadKind.INCREMENTAL and self.total_delta() < 10

    def label(self) -> str:
        """Return a compact human-readable label for this event.

        The label encodes the event identifier, module name, epoch
        transition, and reload kind in a single string suitable for
        log messages and debug output.

        Returns:
            A string of the form
            ``"reload[<event_id>]:<module>(<old>→<new>,<kind>)"``.

        Example:
            >>> ev = ReloadEvent(
            ...     event_id="rl_abc123", module_name="pkg.mod",
            ...     old_epoch=3, new_epoch=4, changed_keys=frozenset(),
            ...     removed_keys=frozenset(), added_keys=frozenset(),
            ...     reload_at=0.0, dev_mode=True,
            ...     kind=ReloadKind.FULL_RELOAD, duration_ms=12.0,
            ... )
            >>> ev.label()
            'reload[rl_abc123]:pkg.mod(3→4,full_reload)'
        """
        return (
            f"reload[{self.event_id}]:{self.module_name}"
            f"({self.old_epoch}→{self.new_epoch},{self.kind.value})"
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize this event to a JSON-compatible dictionary.

        All fields are included.  ``frozenset`` values are converted to
        sorted lists so the result can be passed to ``json.dumps`` without
        a custom encoder.

        Returns:
            A dictionary with string keys and JSON-serializable values
            representing every field of this dataclass.

        Example:
            >>> ev = ReloadEvent(
            ...     event_id="rl_z", module_name="mod", old_epoch=0,
            ...     new_epoch=1, changed_keys=frozenset(),
            ...     removed_keys=frozenset(), added_keys=frozenset(),
            ...     reload_at=1234567890.0, dev_mode=False,
            ...     kind=ReloadKind.HOT_SWAP, duration_ms=3.0,
            ... )
            >>> d = ev.to_dict()
            >>> d["module_name"]
            'mod'
            >>> isinstance(d["added_keys"], list)
            True
        """
        return {
            "event_id": self.event_id,
            "module_name": self.module_name,
            "old_epoch": self.old_epoch,
            "new_epoch": self.new_epoch,
            "changed_keys": sorted(self.changed_keys),
            "removed_keys": sorted(self.removed_keys),
            "added_keys": sorted(self.added_keys),
            "reload_at": self.reload_at,
            "dev_mode": self.dev_mode,
            "kind": self.kind.value,
            "duration_ms": self.duration_ms,
            "total_delta": self.total_delta(),
            "is_incremental": self.is_incremental(),
            "epoch_advance": self.epoch_advance(),
            "label": self.label(),
        }

    def epoch_advance(self) -> int:
        """Return the number of epoch steps taken by this reload.

        For normal forward reloads this is always ``1``.  For rollbacks
        the value may be negative.

        Returns:
            ``new_epoch - old_epoch`` as a signed integer.

        Example:
            >>> ev = ReloadEvent(
            ...     event_id="rl_r", module_name="mod", old_epoch=5,
            ...     new_epoch=3, changed_keys=frozenset(),
            ...     removed_keys=frozenset(), added_keys=frozenset(),
            ...     reload_at=0.0, dev_mode=True,
            ...     kind=ReloadKind.ROLLBACK, duration_ms=2.0,
            ... )
            >>> ev.epoch_advance()
            -2
        """
        return self.new_epoch - self.old_epoch


@dataclass(frozen=True, slots=True)
class DevModeState:
    """Immutable snapshot of the development session's runtime state.

    A new DevModeState is created each time the coordinator transitions
    to a new phase or the epoch counter advances.  Old states are never
    mutated; instead a replacement instance is constructed and stored.

    Attributes:
        state_id: Unique identifier generated by :func:`_new_state_id`.
        active_modules: Frozenset of module names currently registered
            in this dev session.
        epoch_counter: The highest epoch number seen across all modules
            in this session.
        reload_count: Total number of reload events processed since the
            session began.
        created_at: Unix timestamp when this state was first created.
        updated_at: Unix timestamp of the most recent state transition.
        phase: The current :class:`DevSessionPhase` of the session.
    """

    state_id: str
    active_modules: frozenset[str]
    epoch_counter: int
    reload_count: int
    created_at: float
    updated_at: float
    phase: DevSessionPhase

    def module_count(self) -> int:
        """Return the number of modules currently active in this session.

        Returns:
            The cardinality of :attr:`active_modules`.

        Example:
            >>> import time
            >>> s = DevModeState(
            ...     state_id="ds_x", active_modules=frozenset({"a", "b"}),
            ...     epoch_counter=2, reload_count=5,
            ...     created_at=time.time(), updated_at=time.time(),
            ...     phase=DevSessionPhase.ACTIVE,
            ... )
            >>> s.module_count()
            2
        """
        return len(self.active_modules)

    def uptime(self) -> float:
        """Return the wall-clock age of this state in seconds.

        Measures the time elapsed since the state was created, which
        approximates how long the dev session has been running.

        Returns:
            ``time.time() - created_at`` as a float.

        Example:
            >>> import time
            >>> s = DevModeState(
            ...     state_id="ds_y", active_modules=frozenset(),
            ...     epoch_counter=0, reload_count=0,
            ...     created_at=time.time() - 10.0, updated_at=time.time(),
            ...     phase=DevSessionPhase.INITIALIZING,
            ... )
            >>> s.uptime() >= 10.0
            True
        """
        return time.time() - self.created_at

    def label(self) -> str:
        """Return a compact human-readable label for this state snapshot.

        Returns:
            A string encoding the state_id, module count, epoch counter,
            and session phase.

        Example:
            >>> import time
            >>> s = DevModeState(
            ...     state_id="ds_abc", active_modules=frozenset({"m"}),
            ...     epoch_counter=7, reload_count=3,
            ...     created_at=time.time(), updated_at=time.time(),
            ...     phase=DevSessionPhase.ACTIVE,
            ... )
            >>> s.label()
            'devstate[ds_abc]:1mods@ep7(active)'
        """
        return (
            f"devstate[{self.state_id}]:{len(self.active_modules)}mods"
            f"@ep{self.epoch_counter}({self.phase.value})"
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize this state to a JSON-compatible dictionary.

        Returns:
            A dictionary with all fields represented as JSON-safe types.
            ``frozenset`` values become sorted lists.

        Example:
            >>> import time
            >>> s = DevModeState(
            ...     state_id="ds_z", active_modules=frozenset({"x"}),
            ...     epoch_counter=1, reload_count=2,
            ...     created_at=0.0, updated_at=1.0,
            ...     phase=DevSessionPhase.TERMINATED,
            ... )
            >>> d = s.to_dict()
            >>> d["phase"]
            'terminated'
        """
        return {
            "state_id": self.state_id,
            "active_modules": sorted(self.active_modules),
            "epoch_counter": self.epoch_counter,
            "reload_count": self.reload_count,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "phase": self.phase.value,
            "module_count": self.module_count(),
            "uptime": self.uptime(),
            "is_active": self.is_active(),
            "label": self.label(),
        }

    def is_active(self) -> bool:
        """Return True when the session phase is ACTIVE.

        Returns:
            ``True`` iff :attr:`phase` is :attr:`DevSessionPhase.ACTIVE`.

        Example:
            >>> import time
            >>> s = DevModeState(
            ...     state_id="ds_a", active_modules=frozenset(),
            ...     epoch_counter=0, reload_count=0,
            ...     created_at=time.time(), updated_at=time.time(),
            ...     phase=DevSessionPhase.ACTIVE,
            ... )
            >>> s.is_active()
            True
        """
        return self.phase == DevSessionPhase.ACTIVE


@dataclass(frozen=True, slots=True)
class ReloadDiff:
    """Immutable summary of the structural diff produced by a single reload.

    Unlike :class:`ReloadEvent`, which records the full frozensets of
    added/removed keys, ReloadDiff stores only the counts.  This makes
    it cheap to pass around as a summary token.

    Attributes:
        diff_id: Unique identifier for this diff record.
        module_name: The module whose bindings were compared.
        from_epoch: The source epoch for the comparison.
        to_epoch: The target epoch for the comparison.
        added_count: Number of keys added between the two epochs.
        removed_count: Number of keys removed between the two epochs.
        changed_count: Number of keys whose values changed (may be 0
            in key-only diff mode).
        computed_at: Unix timestamp when the diff was computed.
    """

    diff_id: str
    module_name: str
    from_epoch: int
    to_epoch: int
    added_count: int
    removed_count: int
    changed_count: int
    computed_at: float

    def total_changes(self) -> int:
        """Return the aggregate number of key-level changes.

        Returns:
            ``added_count + removed_count + changed_count``.

        Example:
            >>> import time
            >>> d = ReloadDiff(
            ...     diff_id="dd_1", module_name="mod",
            ...     from_epoch=0, to_epoch=1,
            ...     added_count=3, removed_count=1, changed_count=2,
            ...     computed_at=time.time(),
            ... )
            >>> d.total_changes()
            6
        """
        return self.added_count + self.removed_count + self.changed_count

    def label(self) -> str:
        """Return a short human-readable label for this diff.

        Returns:
            A string encoding the diff_id, module, epoch transition, and
            total change count.

        Example:
            >>> import time
            >>> d = ReloadDiff(
            ...     diff_id="dd_2", module_name="pkg.mod",
            ...     from_epoch=2, to_epoch=3,
            ...     added_count=1, removed_count=0, changed_count=0,
            ...     computed_at=time.time(),
            ... )
            >>> "pkg.mod" in d.label()
            True
        """
        return (
            f"diff[{self.diff_id}]:{self.module_name}"
            f"({self.from_epoch}→{self.to_epoch},"
            f"+{self.added_count}/-{self.removed_count}/~{self.changed_count})"
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize this diff to a JSON-compatible dictionary.

        Returns:
            A flat dictionary with all fields and derived values (total
            changes, label) included as JSON-safe types.

        Example:
            >>> import time
            >>> d = ReloadDiff(
            ...     diff_id="dd_3", module_name="m", from_epoch=0,
            ...     to_epoch=1, added_count=2, removed_count=0,
            ...     changed_count=0, computed_at=0.0,
            ... )
            >>> d.to_dict()["total_changes"]
            2
        """
        return {
            "diff_id": self.diff_id,
            "module_name": self.module_name,
            "from_epoch": self.from_epoch,
            "to_epoch": self.to_epoch,
            "added_count": self.added_count,
            "removed_count": self.removed_count,
            "changed_count": self.changed_count,
            "computed_at": self.computed_at,
            "total_changes": self.total_changes(),
            "label": self.label(),
        }


@dataclass(frozen=True, slots=True)
class DevModeObservation:
    """An immutable chronological observation recorded by DevModeWitness.

    Every significant dev-mode event (enter, exit, reload) is captured
    as a DevModeObservation and appended to the witness's timeline.

    Attributes:
        obs_id: Unique identifier generated by :func:`_new_obs_id`.
        module_name: The module involved in this observation.
        observed_at: Unix timestamp when the observation was made.
        event: A short string describing what was observed, e.g.
            ``"enter_dev_mode"``, ``"exit_dev_mode"``, or ``"reload"``.
        epoch_at_observation: The module's current epoch when the
            observation was recorded.
        dev_mode_active: Whether the module was in an active dev session
            at the time of the observation.
    """

    obs_id: str
    module_name: str
    observed_at: float
    event: str
    epoch_at_observation: int
    dev_mode_active: bool

    def to_dict(self) -> dict[str, Any]:
        """Serialize this observation to a JSON-compatible dictionary.

        Returns:
            A flat dictionary with all fields plus ``age`` represented as
            JSON-safe types.

        Example:
            >>> import time
            >>> o = DevModeObservation(
            ...     obs_id="do_abc", module_name="mod",
            ...     observed_at=time.time(), event="enter_dev_mode",
            ...     epoch_at_observation=0, dev_mode_active=True,
            ... )
            >>> o.to_dict()["event"]
            'enter_dev_mode'
        """
        return {
            "obs_id": self.obs_id,
            "module_name": self.module_name,
            "observed_at": self.observed_at,
            "event": self.event,
            "epoch_at_observation": self.epoch_at_observation,
            "dev_mode_active": self.dev_mode_active,
            "age": self.age(),
        }

    def age(self) -> float:
        """Return the age of this observation in seconds.

        Returns:
            ``time.time() - observed_at`` as a non-negative float.

        Example:
            >>> import time
            >>> o = DevModeObservation(
            ...     obs_id="do_x", module_name="m",
            ...     observed_at=time.time() - 5.0,
            ...     event="reload", epoch_at_observation=3,
            ...     dev_mode_active=True,
            ... )
            >>> o.age() >= 5.0
            True
        """
        return time.time() - self.observed_at


# ---------------------------------------------------------------------------
# HotReloadEngine — mutable manager
# ---------------------------------------------------------------------------


@dataclass
class HotReloadEngine:
    """Stateful engine that executes and archives hot-reload cycles.

    HotReloadEngine is the authoritative source of truth for per-module
    reload histories.  It maintains epoch counters, stores every
    :class:`ReloadEvent` ever produced, and exposes query helpers for
    analytics and debugging.

    Attributes:
        _events: Mapping from module name to the ordered list of
            ReloadEvents that have been applied to that module.
        _epoch_counters: Mapping from module name to its current epoch
            number.  Starts at 0 and increments with each forward reload.
        _all_events: Flat ordered list of every ReloadEvent produced by
            this engine, regardless of module.
    """

    _events: dict[str, list[ReloadEvent]] = field(default_factory=dict)
    _epoch_counters: dict[str, int] = field(default_factory=dict)
    _all_events: list[ReloadEvent] = field(default_factory=list)

    def trigger_reload(
        self,
        module_name: str,
        new_keys: frozenset[str],
        old_keys: frozenset[str],
        kind: ReloadKind = ReloadKind.INCREMENTAL,
    ) -> ReloadEvent:
        """Execute a hot-reload cycle and return the resulting event.

        Computes the diff between *old_keys* and *new_keys*, advances the
        epoch counter for *module_name*, estimates a duration, creates a
        :class:`ReloadEvent`, and stores it in both the per-module and
        global event lists.

        Args:
            module_name: Fully-qualified name of the module being reloaded.
            new_keys: Frozenset of binding keys present after the reload.
            old_keys: Frozenset of binding keys present before the reload.
            kind: The :class:`ReloadKind` classifying this reload.  Defaults
                to :attr:`ReloadKind.INCREMENTAL`.

        Returns:
            The newly created :class:`ReloadEvent` describing this reload.

        Raises:
            ValueError: If *module_name* is an empty string.

        Example:
            >>> engine = HotReloadEngine()
            >>> ev = engine.trigger_reload(
            ...     "mymod",
            ...     frozenset({"a", "b", "c"}),
            ...     frozenset({"a", "b"}),
            ... )
            >>> ev.new_epoch == 1
            True
            >>> "c" in ev.added_keys
            True
        """
        if not module_name:
            raise ValueError("module_name must not be empty")

        diff = _compute_diff(old_keys, new_keys)
        old_epoch = self._epoch_counters.get(module_name, 0)
        new_epoch = old_epoch + 1
        self._epoch_counters[module_name] = new_epoch

        total_changes = (
            len(diff["added"]) + len(diff["removed"]) + len(diff["changed"])
        )
        duration_ms = max(0.5, 0.8 + total_changes * 0.35 + math.sqrt(total_changes + 1) * 0.2)

        event = ReloadEvent(
            event_id=_new_event_id(),
            module_name=module_name,
            old_epoch=old_epoch,
            new_epoch=new_epoch,
            changed_keys=diff["changed"],
            removed_keys=diff["removed"],
            added_keys=diff["added"],
            reload_at=time.time(),
            dev_mode=True,
            kind=kind,
            duration_ms=duration_ms,
        )

        if module_name not in self._events:
            self._events[module_name] = []
        self._events[module_name].append(event)
        self._all_events.append(event)

        _log.debug(
            "HotReloadEngine: triggered %s delta=%d epoch=%d→%d dur=%.2fms",
            event.label(),
            total_changes,
            old_epoch,
            new_epoch,
            duration_ms,
        )
        return event

    def compute_diff(
        self,
        old_keys: frozenset[str],
        new_keys: frozenset[str],
    ) -> dict[str, Any]:
        """Compute and annotate the diff between two key sets.

        Delegates structural diffing to :func:`_compute_diff` and then
        enriches the result with a ``retention_ratio`` measuring what
        fraction of old keys survived into the new snapshot.

        Args:
            old_keys: Binding keys from the previous epoch.
            new_keys: Binding keys from the new epoch.

        Returns:
            A dict containing ``"added"``, ``"removed"``, ``"changed"``,
            ``"retained"`` (all frozensets), plus ``"retention_ratio"``
            (a float in ``[0.0, 1.0]``) and ``"old_fingerprint"`` /
            ``"new_fingerprint"`` SHA-256 snippets.

        Example:
            >>> engine = HotReloadEngine()
            >>> d = engine.compute_diff(frozenset({"a"}), frozenset({"a", "b"}))
            >>> d["retention_ratio"]
            1.0
            >>> "b" in d["added"]
            True
        """
        raw = _compute_diff(old_keys, new_keys)
        retained = raw["retained"]
        retention_ratio = len(retained) / len(old_keys) if old_keys else 1.0

        result: dict[str, Any] = dict(raw)
        result["retention_ratio"] = retention_ratio
        result["old_fingerprint"] = _hash_keys(old_keys)
        result["new_fingerprint"] = _hash_keys(new_keys)
        result["added_count"] = len(raw["added"])
        result["removed_count"] = len(raw["removed"])
        result["changed_count"] = len(raw["changed"])
        result["retained_count"] = len(retained)

        _log.debug(
            "HotReloadEngine.compute_diff: +%d -%d retention=%.2f",
            result["added_count"],
            result["removed_count"],
            retention_ratio,
        )
        return result

    def validate_reload(self, event: ReloadEvent) -> bool:
        """Validate that a ReloadEvent is internally consistent.

        Performs a series of lightweight checks to detect obviously
        malformed events before they are acted upon.

        Args:
            event: The :class:`ReloadEvent` to validate.

        Returns:
            ``True`` if the event passes all checks; ``False`` otherwise.
            Failures are logged at WARNING level.

        Example:
            >>> engine = HotReloadEngine()
            >>> ev = engine.trigger_reload("mod", frozenset(), frozenset())
            >>> engine.validate_reload(ev)
            True
        """
        if not event.event_id:
            _log.warning("validate_reload: event_id is empty")
            return False
        if not event.module_name:
            _log.warning("validate_reload: module_name is empty")
            return False
        if event.new_epoch <= event.old_epoch and event.kind != ReloadKind.ROLLBACK:
            _log.warning(
                "validate_reload: new_epoch %d <= old_epoch %d for non-rollback event %s",
                event.new_epoch,
                event.old_epoch,
                event.event_id,
            )
            return False
        _log.debug("validate_reload: event %s is valid", event.event_id)
        return True

    def get_reload_history(self, module_name: str) -> list[ReloadEvent]:
        """Return the ordered reload history for a given module.

        Args:
            module_name: The module whose history is requested.

        Returns:
            A list of :class:`ReloadEvent` objects in the order they were
            applied, oldest first.  Returns an empty list if the module
            has never been reloaded.

        Example:
            >>> engine = HotReloadEngine()
            >>> engine.get_reload_history("unknown")
            []
        """
        history = self._events.get(module_name, [])
        _log.debug(
            "HotReloadEngine.get_reload_history: %s has %d events",
            module_name,
            len(history),
        )
        return list(history)

    def rollback_reload(
        self, module_name: str, target_epoch: int
    ) -> ReloadEvent | None:
        """Roll back a module to a previous epoch snapshot.

        Searches the reload history of *module_name* for an event that
        arrived at *target_epoch*.  If found, synthesizes a new ROLLBACK
        :class:`ReloadEvent` that swaps the added/removed key sets and
        decrements the epoch counter back to *target_epoch*.

        Args:
            module_name: The module to roll back.
            target_epoch: The epoch to rewind to.  Must be the
                ``new_epoch`` of a previously recorded event.

        Returns:
            A new :class:`ReloadEvent` with kind=ROLLBACK if the target
            epoch was found, or ``None`` if no matching event exists.

        Example:
            >>> engine = HotReloadEngine()
            >>> engine.trigger_reload("m", frozenset({"a"}), frozenset())
            ReloadEvent(...)
            >>> ev = engine.rollback_reload("m", target_epoch=1)
            >>> ev is not None
            True
            >>> ev.kind == ReloadKind.ROLLBACK
            True
        """
        history = self._events.get(module_name, [])
        target_event: ReloadEvent | None = None
        for ev in history:
            if ev.new_epoch == target_epoch:
                target_event = ev
                break

        if target_event is None:
            _log.warning(
                "HotReloadEngine.rollback_reload: no event at epoch %d for %s",
                target_epoch,
                module_name,
            )
            return None

        current_epoch = self._epoch_counters.get(module_name, 0)
        self._epoch_counters[module_name] = target_epoch

        rollback_event = ReloadEvent(
            event_id=_new_event_id(),
            module_name=module_name,
            old_epoch=current_epoch,
            new_epoch=target_epoch,
            changed_keys=target_event.changed_keys,
            removed_keys=target_event.added_keys,
            added_keys=target_event.removed_keys,
            reload_at=time.time(),
            dev_mode=True,
            kind=ReloadKind.ROLLBACK,
            duration_ms=0.5,
        )

        self._events[module_name].append(rollback_event)
        self._all_events.append(rollback_event)

        _log.debug(
            "HotReloadEngine.rollback_reload: %s rolled back epoch %d→%d",
            module_name,
            current_epoch,
            target_epoch,
        )
        return rollback_event

    def reload_count(self, module_name: str) -> int:
        """Return the number of reloads recorded for a specific module.

        Args:
            module_name: The module whose reload count is requested.

        Returns:
            Integer count of reload events; 0 if the module is unknown.

        Example:
            >>> engine = HotReloadEngine()
            >>> engine.reload_count("unknown")
            0
        """
        count = len(self._events.get(module_name, []))
        _log.debug("HotReloadEngine.reload_count: %s → %d", module_name, count)
        return count

    def export_events(self) -> list[dict[str, Any]]:
        """Serialize the complete event log to a list of dictionaries.

        Returns:
            A list of dicts produced by calling :meth:`ReloadEvent.to_dict`
            on every event in :attr:`_all_events`, in chronological order.

        Example:
            >>> engine = HotReloadEngine()
            >>> engine.trigger_reload("m", frozenset(), frozenset())
            ReloadEvent(...)
            >>> exported = engine.export_events()
            >>> len(exported) >= 1
            True
            >>> isinstance(exported[0], dict)
            True
        """
        serialized = [ev.to_dict() for ev in self._all_events]
        _log.debug("HotReloadEngine.export_events: %d events exported", len(serialized))
        return serialized

    def stats(self) -> dict[str, Any]:
        """Compute aggregate statistics over all recorded reload events.

        Returns a dictionary with the following keys:

        - ``"total_events"``: total number of reload events across all modules.
        - ``"modules_with_reloads"``: count of modules that have at least one event.
        - ``"avg_delta_per_reload"``: arithmetic mean of :meth:`ReloadEvent.total_delta`
          across all events; ``0.0`` if no events exist.
        - ``"max_epoch_per_module"``: dict mapping each module to its highest epoch.
        - ``"forced_reload_count"``: count of events with kind=FORCED.
        - ``"rollback_count"``: count of events with kind=ROLLBACK.
        - ``"hot_swap_count"``: count of events with kind=HOT_SWAP.

        Returns:
            A JSON-serializable dict of aggregate statistics.

        Example:
            >>> engine = HotReloadEngine()
            >>> engine.trigger_reload("m", frozenset({"x"}), frozenset())
            ReloadEvent(...)
            >>> s = engine.stats()
            >>> s["total_events"] >= 1
            True
        """
        total = len(self._all_events)
        modules_count = len([k for k, v in self._events.items() if v])
        avg_delta = (
            sum(ev.total_delta() for ev in self._all_events) / total
            if total > 0
            else 0.0
        )
        max_epochs = {mod: self._epoch_counters.get(mod, 0) for mod in self._events}
        forced_count = sum(1 for ev in self._all_events if ev.kind == ReloadKind.FORCED)
        rollback_count = sum(1 for ev in self._all_events if ev.kind == ReloadKind.ROLLBACK)
        hot_swap_count = sum(1 for ev in self._all_events if ev.kind == ReloadKind.HOT_SWAP)

        result = {
            "total_events": total,
            "modules_with_reloads": modules_count,
            "avg_delta_per_reload": round(avg_delta, 4),
            "max_epoch_per_module": max_epochs,
            "forced_reload_count": forced_count,
            "rollback_count": rollback_count,
            "hot_swap_count": hot_swap_count,
        }
        _log.debug("HotReloadEngine.stats: %s", json.dumps(result, default=str))
        return result

    def modules_with_reloads(self) -> list[str]:
        """Return a sorted list of modules that have at least one reload event.

        Returns:
            Alphabetically sorted list of module name strings.

        Example:
            >>> engine = HotReloadEngine()
            >>> engine.trigger_reload("b", frozenset(), frozenset())
            ReloadEvent(...)
            >>> engine.trigger_reload("a", frozenset(), frozenset())
            ReloadEvent(...)
            >>> engine.modules_with_reloads()
            ['a', 'b']
        """
        result = sorted(k for k, v in self._events.items() if v)
        _log.debug("HotReloadEngine.modules_with_reloads: %s", result)
        return result

    def most_reloaded_module(self) -> str | None:
        """Return the name of the module with the most reload events.

        Returns:
            The module name string, or ``None`` if no reloads have been
            recorded yet.

        Example:
            >>> engine = HotReloadEngine()
            >>> engine.most_reloaded_module() is None
            True
        """
        if not self._events:
            return None
        winner = max(self._events, key=lambda k: len(self._events[k]))
        _log.debug("HotReloadEngine.most_reloaded_module: %s", winner)
        return winner

    def total_reload_count(self) -> int:
        """Return the total number of reload events across all modules.

        Returns:
            ``len(_all_events)`` as an integer.

        Example:
            >>> engine = HotReloadEngine()
            >>> engine.total_reload_count()
            0
        """
        return len(self._all_events)

    def latest_event(self, module_name: str) -> ReloadEvent | None:
        """Return the most recent reload event for a module.

        Args:
            module_name: The module whose latest event is requested.

        Returns:
            The last :class:`ReloadEvent` in the module's history, or
            ``None`` if the module has no events.

        Example:
            >>> engine = HotReloadEngine()
            >>> engine.latest_event("nope") is None
            True
        """
        events = self._events.get(module_name, [])
        if not events:
            return None
        latest = events[-1]
        _log.debug("HotReloadEngine.latest_event: %s → %s", module_name, latest.event_id)
        return latest


# ---------------------------------------------------------------------------
# DevModeWitness — mutable observer
# ---------------------------------------------------------------------------


@dataclass
class DevModeWitness:
    """Observational recorder for development-mode lifecycle events.

    DevModeWitness tracks which modules are currently in dev mode,
    records observations when modules enter or exit dev mode and when
    reloads occur, and exposes reporting helpers for session summaries.

    Attributes:
        _dev_sessions: Mapping from module name to the Unix timestamp
            when the module entered dev mode.
        _observations: Ordered list of all :class:`DevModeObservation`
            records produced during this witness's lifetime.
        _reload_obs: List of obs_ids corresponding specifically to
            reload observations (subset of _observations).
        _timeline: Deque storing obs_ids in strict chronological order
            for fast recent-event access.
        _exited: Mapping from module name to the Unix timestamp when
            the module exited dev mode.
    """

    _dev_sessions: dict[str, float] = field(default_factory=dict)
    _observations: list[DevModeObservation] = field(default_factory=list)
    _reload_obs: list[str] = field(default_factory=list)
    _timeline: deque = field(default_factory=deque)
    _exited: dict[str, float] = field(default_factory=dict)

    def enter_dev_mode(self, module_name: str) -> str:
        """Register a module as entering development mode.

        Records the entry timestamp, creates an observation, appends it
        to the timeline, and returns the observation identifier.

        Args:
            module_name: The module transitioning into dev mode.

        Returns:
            The ``obs_id`` string of the newly created observation.

        Raises:
            ValueError: If *module_name* is empty.

        Example:
            >>> witness = DevModeWitness()
            >>> oid = witness.enter_dev_mode("mypackage.core")
            >>> oid.startswith("do_")
            True
            >>> "mypackage.core" in witness.active_dev_modules()
            True
        """
        if not module_name:
            raise ValueError("module_name must not be empty")

        now = time.time()
        self._dev_sessions[module_name] = now

        obs = DevModeObservation(
            obs_id=_new_obs_id(),
            module_name=module_name,
            observed_at=now,
            event="enter_dev_mode",
            epoch_at_observation=0,
            dev_mode_active=True,
        )
        self._observations.append(obs)
        self._timeline.append(obs.obs_id)

        _log.debug(
            "DevModeWitness.enter_dev_mode: module=%s obs=%s",
            module_name,
            obs.obs_id,
        )
        return obs.obs_id

    def exit_dev_mode(self, module_name: str) -> bool:
        """Register a module as exiting development mode.

        Records the exit timestamp, removes the module from active sessions,
        and creates an exit observation.

        Args:
            module_name: The module transitioning out of dev mode.

        Returns:
            ``True`` if the module was in an active dev session and was
            successfully removed, ``False`` if the module was not active.

        Example:
            >>> witness = DevModeWitness()
            >>> witness.enter_dev_mode("mod")
            'do_...'
            >>> witness.exit_dev_mode("mod")
            True
            >>> witness.exit_dev_mode("mod")
            False
        """
        if module_name not in self._dev_sessions:
            _log.warning(
                "DevModeWitness.exit_dev_mode: %s was not in active dev sessions",
                module_name,
            )
            return False

        now = time.time()
        self._exited[module_name] = now
        del self._dev_sessions[module_name]

        obs = DevModeObservation(
            obs_id=_new_obs_id(),
            module_name=module_name,
            observed_at=now,
            event="exit_dev_mode",
            epoch_at_observation=0,
            dev_mode_active=False,
        )
        self._observations.append(obs)
        self._timeline.append(obs.obs_id)

        _log.debug(
            "DevModeWitness.exit_dev_mode: module=%s obs=%s",
            module_name,
            obs.obs_id,
        )
        return True

    def observe_reload(self, event: ReloadEvent) -> str:
        """Record an observation for a reload event.

        Creates a :class:`DevModeObservation` linked to the given
        :class:`ReloadEvent` and appends it to both the general
        observation list and the reload-specific observation list.

        Args:
            event: The :class:`ReloadEvent` to observe.

        Returns:
            The ``obs_id`` string of the newly created observation.

        Example:
            >>> import time
            >>> witness = DevModeWitness()
            >>> engine = HotReloadEngine()
            >>> ev = engine.trigger_reload("mod", frozenset(), frozenset())
            >>> oid = witness.observe_reload(ev)
            >>> oid.startswith("do_")
            True
            >>> witness.reload_observation_count() >= 1
            True
        """
        now = time.time()
        obs = DevModeObservation(
            obs_id=_new_obs_id(),
            module_name=event.module_name,
            observed_at=now,
            event=f"reload:{event.kind.value}",
            epoch_at_observation=event.new_epoch,
            dev_mode_active=event.dev_mode,
        )
        self._observations.append(obs)
        self._reload_obs.append(obs.obs_id)
        self._timeline.append(obs.obs_id)

        _log.debug(
            "DevModeWitness.observe_reload: module=%s epoch=%d obs=%s",
            event.module_name,
            event.new_epoch,
            obs.obs_id,
        )
        return obs.obs_id

    def active_dev_modules(self) -> list[str]:
        """Return a sorted list of modules currently in dev mode.

        Returns:
            Alphabetically sorted list of module names that have entered
            but not yet exited dev mode.

        Example:
            >>> witness = DevModeWitness()
            >>> witness.enter_dev_mode("b")
            'do_...'
            >>> witness.enter_dev_mode("a")
            'do_...'
            >>> witness.active_dev_modules()
            ['a', 'b']
        """
        active = sorted(self._dev_sessions.keys())
        _log.debug("DevModeWitness.active_dev_modules: %s", active)
        return active

    def dev_mode_duration(self, module_name: str) -> float:
        """Return how long a module has been (or was) in dev mode.

        If the module is still active, the duration is measured from the
        entry time to now.  If the module has already exited, the duration
        is the difference between exit and entry times.  If the module
        was never registered, returns ``0.0``.

        Args:
            module_name: The module whose dev-mode duration is queried.

        Returns:
            Duration in seconds as a non-negative float.

        Example:
            >>> import time
            >>> witness = DevModeWitness()
            >>> witness.enter_dev_mode("mod")
            'do_...'
            >>> d = witness.dev_mode_duration("mod")
            >>> d >= 0.0
            True
        """
        now = time.time()
        if module_name in self._dev_sessions:
            return now - self._dev_sessions[module_name]
        if module_name in self._exited:
            start = self._dev_sessions.get(module_name)
            if start is None:
                for obs in self._observations:
                    if obs.module_name == module_name and obs.event == "enter_dev_mode":
                        start = obs.observed_at
                        break
            exit_time = self._exited[module_name]
            if start is not None:
                return exit_time - start
        return 0.0

    def generate_dev_report(self) -> dict[str, Any]:
        """Generate a comprehensive summary of dev-mode activity.

        Returns:
            A dict with keys:

            - ``"active_modules"``: list of currently active module names.
            - ``"total_observations"``: total count of all observations.
            - ``"reload_observation_count"``: count of reload-specific
              observations.
            - ``"total_dev_duration_per_module"``: dict mapping module names
              to their cumulative dev-mode duration in seconds.
            - ``"timeline_length"``: number of entries in the timeline deque.
            - ``"exited_modules"``: list of modules that have exited.

        Example:
            >>> witness = DevModeWitness()
            >>> witness.enter_dev_mode("mod")
            'do_...'
            >>> report = witness.generate_dev_report()
            >>> "active_modules" in report
            True
        """
        active = self.active_dev_modules()
        all_modules = set(self._dev_sessions) | set(self._exited)
        durations = {mod: self.dev_mode_duration(mod) for mod in all_modules}

        report = {
            "active_modules": active,
            "total_observations": len(self._observations),
            "reload_observation_count": len(self._reload_obs),
            "total_dev_duration_per_module": durations,
            "timeline_length": len(self._timeline),
            "exited_modules": sorted(self._exited.keys()),
        }
        _log.debug(
            "DevModeWitness.generate_dev_report: %d active %d obs",
            len(active),
            len(self._observations),
        )
        return report

    def export_observations(self) -> list[dict[str, Any]]:
        """Serialize all observations to a list of dictionaries.

        Returns:
            A list of dicts from :meth:`DevModeObservation.to_dict`, one
            per recorded observation, in chronological order.

        Example:
            >>> witness = DevModeWitness()
            >>> witness.enter_dev_mode("mod")
            'do_...'
            >>> obs_list = witness.export_observations()
            >>> len(obs_list) >= 1
            True
        """
        serialized = [obs.to_dict() for obs in self._observations]
        _log.debug(
            "DevModeWitness.export_observations: %d records", len(serialized)
        )
        return serialized

    def total_dev_time(self) -> float:
        """Return the sum of dev-mode durations across all known modules.

        Includes both currently active and already-exited modules.

        Returns:
            Sum of :meth:`dev_mode_duration` for every module ever
            registered with this witness.

        Example:
            >>> witness = DevModeWitness()
            >>> witness.total_dev_time()
            0.0
        """
        all_modules = set(self._dev_sessions) | set(self._exited)
        total = sum(self.dev_mode_duration(mod) for mod in all_modules)
        _log.debug("DevModeWitness.total_dev_time: %.4fs", total)
        return total

    def reload_observation_count(self) -> int:
        """Return the number of reload-specific observations.

        Returns:
            ``len(_reload_obs)`` as an integer.

        Example:
            >>> witness = DevModeWitness()
            >>> witness.reload_observation_count()
            0
        """
        return len(self._reload_obs)

    def latest_reload_observation(self) -> DevModeObservation | None:
        """Return the most recent reload observation, if any.

        Scans :attr:`_observations` in reverse order and returns the first
        entry whose event string starts with ``"reload:"``.

        Returns:
            The most recent reload :class:`DevModeObservation`, or ``None``
            if no reload observations have been recorded.

        Example:
            >>> witness = DevModeWitness()
            >>> witness.latest_reload_observation() is None
            True
        """
        for obs in reversed(self._observations):
            if obs.event.startswith("reload:"):
                _log.debug(
                    "DevModeWitness.latest_reload_observation: %s", obs.obs_id
                )
                return obs
        return None


# ---------------------------------------------------------------------------
# HotReloadDevelopmentModeCoordinator — top-level orchestrator
# ---------------------------------------------------------------------------


@dataclass
class HotReloadDevelopmentModeCoordinator:
    """Top-level orchestrator for JuGeo's hot-reload development workflow.

    Combines a :class:`HotReloadEngine` and a :class:`DevModeWitness` into
    a single entry point that manages full development sessions: starting a
    session, executing hot reloads, ending the session, and reporting.

    Attributes:
        engine: The :class:`HotReloadEngine` instance managing reload cycles.
        witness: The :class:`DevModeWitness` instance recording observations.
        _state: The current :class:`DevModeState` snapshot, or ``None``
            before the first session is started.
        _session_id: A short hex string uniquely identifying this
            coordinator's lifetime session.
        _created_at: Unix timestamp when this coordinator was instantiated.
    """

    engine: HotReloadEngine = field(default_factory=HotReloadEngine)
    witness: DevModeWitness = field(default_factory=DevModeWitness)
    _state: DevModeState | None = field(default=None)
    _session_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    _created_at: float = field(default_factory=time.time)

    def start_dev_session(self, module_name: str) -> dict[str, Any]:
        """Begin a development session for a module.

        Registers the module with the witness, initializes its epoch
        counter in the engine, and creates the initial
        :class:`DevModeState` for this session.

        Args:
            module_name: The fully-qualified name of the module entering
                development mode.

        Returns:
            A dictionary summarizing the new session state, including
            ``"session_id"``, ``"obs_id"``, ``"state"`` (serialized
            :class:`DevModeState`), and ``"module_name"``.

        Raises:
            ValueError: If *module_name* is empty.

        Example:
            >>> coord = HotReloadDevelopmentModeCoordinator()
            >>> summary = coord.start_dev_session("mypackage.util")
            >>> summary["module_name"]
            'mypackage.util'
        """
        if not module_name:
            raise ValueError("module_name must not be empty")

        obs_id = self.witness.enter_dev_mode(module_name)

        if module_name not in self.engine._epoch_counters:
            self.engine._epoch_counters[module_name] = 0

        now = time.time()
        state = DevModeState(
            state_id=_new_state_id(),
            active_modules=frozenset(self.witness.active_dev_modules()),
            epoch_counter=self.engine._epoch_counters.get(module_name, 0),
            reload_count=self.engine.reload_count(module_name),
            created_at=now,
            updated_at=now,
            phase=DevSessionPhase.ACTIVE,
        )
        self._state = state

        _log.debug(
            "HotReloadDevelopmentModeCoordinator.start_dev_session: "
            "session=%s module=%s obs=%s state=%s",
            self._session_id,
            module_name,
            obs_id,
            state.state_id,
        )

        return {
            "session_id": self._session_id,
            "obs_id": obs_id,
            "module_name": module_name,
            "state": state.to_dict(),
        }

    def hot_reload(
        self,
        module_name: str,
        new_keys: frozenset[str],
        old_keys: frozenset[str],
    ) -> dict[str, Any]:
        """Execute an incremental hot reload for a module.

        Triggers the reload in the engine, records an observation in the
        witness, validates the resulting event, and returns a summary.

        Args:
            module_name: The module to reload.
            new_keys: Binding keys present after the reload.
            old_keys: Binding keys present before the reload.

        Returns:
            A dictionary with keys ``"event"`` (serialized
            :class:`ReloadEvent`), ``"obs_id"``, ``"valid"``, and
            ``"diff"`` (computed diff dict with retention ratio).

        Example:
            >>> coord = HotReloadDevelopmentModeCoordinator()
            >>> coord.start_dev_session("mod")
            {...}
            >>> result = coord.hot_reload("mod", frozenset({"a"}), frozenset())
            >>> result["valid"]
            True
        """
        event = self.engine.trigger_reload(
            module_name, new_keys, old_keys, kind=ReloadKind.INCREMENTAL
        )
        obs_id = self.witness.observe_reload(event)
        valid = self.engine.validate_reload(event)
        diff = self.engine.compute_diff(old_keys, new_keys)

        now = time.time()
        if self._state is not None:
            self._state = DevModeState(
                state_id=self._state.state_id,
                active_modules=frozenset(self.witness.active_dev_modules()),
                epoch_counter=event.new_epoch,
                reload_count=self.engine.reload_count(module_name),
                created_at=self._state.created_at,
                updated_at=now,
                phase=DevSessionPhase.ACTIVE,
            )

        _log.debug(
            "HotReloadDevelopmentModeCoordinator.hot_reload: "
            "module=%s event=%s valid=%s",
            module_name,
            event.event_id,
            valid,
        )

        diff_serializable = {
            k: sorted(v) if isinstance(v, frozenset) else v
            for k, v in diff.items()
        }

        return {
            "event": event.to_dict(),
            "obs_id": obs_id,
            "valid": valid,
            "diff": diff_serializable,
        }

    def end_dev_session(self, module_name: str) -> dict[str, Any]:
        """Terminate the development session for a module.

        Unregisters the module from the witness and transitions the session
        state to TERMINATED.

        Args:
            module_name: The module exiting development mode.

        Returns:
            A dictionary with ``"module_name"``, ``"was_active"``,
            ``"final_state"`` (serialized), ``"reload_count"``, and
            ``"dev_duration"`` (in seconds).

        Example:
            >>> coord = HotReloadDevelopmentModeCoordinator()
            >>> coord.start_dev_session("mod")
            {...}
            >>> result = coord.end_dev_session("mod")
            >>> result["was_active"]
            True
        """
        was_active = self.witness.exit_dev_mode(module_name)
        dev_duration = self.witness.dev_mode_duration(module_name)
        reload_count = self.engine.reload_count(module_name)

        now = time.time()
        if self._state is not None:
            self._state = DevModeState(
                state_id=self._state.state_id,
                active_modules=frozenset(self.witness.active_dev_modules()),
                epoch_counter=self._state.epoch_counter,
                reload_count=self._state.reload_count,
                created_at=self._state.created_at,
                updated_at=now,
                phase=DevSessionPhase.TERMINATED,
            )

        _log.debug(
            "HotReloadDevelopmentModeCoordinator.end_dev_session: "
            "module=%s was_active=%s reloads=%d",
            module_name,
            was_active,
            reload_count,
        )

        return {
            "module_name": module_name,
            "was_active": was_active,
            "final_state": self._state.to_dict() if self._state else None,
            "reload_count": reload_count,
            "dev_duration": dev_duration,
        }

    def session_summary(self) -> dict[str, Any]:
        """Return a concise summary of the current session state.

        Returns:
            A dictionary containing ``"session_id"``, ``"session_duration"``,
            ``"current_phase"``, ``"state"`` (serialized :class:`DevModeState`
            or ``None``), ``"engine_stats"``, and ``"witness_report"``.

        Example:
            >>> coord = HotReloadDevelopmentModeCoordinator()
            >>> summary = coord.session_summary()
            >>> "session_id" in summary
            True
        """
        session_duration = time.time() - self._created_at
        phase = self.current_phase()

        summary = {
            "session_id": self._session_id,
            "session_duration": round(session_duration, 4),
            "current_phase": phase.value,
            "state": self._state.to_dict() if self._state else None,
            "engine_stats": self.engine.stats(),
            "witness_report": self.witness.generate_dev_report(),
        }
        _log.debug(
            "HotReloadDevelopmentModeCoordinator.session_summary: "
            "session=%s phase=%s duration=%.2fs",
            self._session_id,
            phase.value,
            session_duration,
        )
        return summary

    def full_report(self) -> dict[str, Any]:
        """Generate a comprehensive report of all session activity.

        Includes engine statistics, witness observations, session summary,
        and the complete serialized event log.

        Returns:
            A dictionary with keys ``"engine_stats"``,
            ``"witness_report"``, ``"session_summary"``, and
            ``"all_events"`` (list of serialized :class:`ReloadEvent`
            dicts).

        Example:
            >>> coord = HotReloadDevelopmentModeCoordinator()
            >>> report = coord.full_report()
            >>> "all_events" in report
            True
        """
        report = {
            "engine_stats": self.engine.stats(),
            "witness_report": self.witness.generate_dev_report(),
            "session_summary": self.session_summary(),
            "all_events": self.engine.export_events(),
            "all_observations": self.witness.export_observations(),
        }
        _log.debug(
            "HotReloadDevelopmentModeCoordinator.full_report: "
            "%d events %d observations",
            len(report["all_events"]),
            len(report["all_observations"]),
        )
        return report

    def reset(self) -> None:
        """Reset the coordinator to a clean initial state.

        Replaces the engine and witness with fresh instances and clears
        the stored session state.  The session_id and created_at are
        preserved to maintain the coordinator's identity.

        Example:
            >>> coord = HotReloadDevelopmentModeCoordinator()
            >>> coord.start_dev_session("mod")
            {...}
            >>> coord.reset()
            >>> coord._state is None
            True
        """
        self.engine = HotReloadEngine()
        self.witness = DevModeWitness()
        self._state = None
        _log.debug(
            "HotReloadDevelopmentModeCoordinator.reset: session=%s",
            self._session_id,
        )

    def current_phase(self) -> DevSessionPhase:
        """Return the current phase of the dev session.

        Returns:
            The phase from :attr:`_state` if a state exists, otherwise
            :attr:`DevSessionPhase.INITIALIZING`.

        Example:
            >>> coord = HotReloadDevelopmentModeCoordinator()
            >>> coord.current_phase() == DevSessionPhase.INITIALIZING
            True
        """
        if self._state is None:
            return DevSessionPhase.INITIALIZING
        return self._state.phase

    def force_reload(
        self,
        module_name: str,
        new_keys: frozenset[str],
        old_keys: frozenset[str],
    ) -> dict[str, Any]:
        """Execute a forced (non-incremental) reload for a module.

        Triggers a :attr:`ReloadKind.FORCED` reload in the engine,
        records an observation, validates the event, and returns a summary.

        Args:
            module_name: The module to forcibly reload.
            new_keys: Binding keys present after the reload.
            old_keys: Binding keys present before the reload.

        Returns:
            A dictionary with ``"event"`` (serialized
            :class:`ReloadEvent`), ``"obs_id"``, ``"valid"``, and
            ``"diff"`` (annotated diff dict).

        Example:
            >>> coord = HotReloadDevelopmentModeCoordinator()
            >>> coord.start_dev_session("mod")
            {...}
            >>> result = coord.force_reload("mod", frozenset({"x"}), frozenset())
            >>> result["event"]["kind"]
            'forced'
        """
        event = self.engine.trigger_reload(
            module_name, new_keys, old_keys, kind=ReloadKind.FORCED
        )
        obs_id = self.witness.observe_reload(event)
        valid = self.engine.validate_reload(event)
        diff = self.engine.compute_diff(old_keys, new_keys)

        now = time.time()
        if self._state is not None:
            self._state = DevModeState(
                state_id=self._state.state_id,
                active_modules=frozenset(self.witness.active_dev_modules()),
                epoch_counter=event.new_epoch,
                reload_count=self.engine.reload_count(module_name),
                created_at=self._state.created_at,
                updated_at=now,
                phase=DevSessionPhase.ACTIVE,
            )

        _log.debug(
            "HotReloadDevelopmentModeCoordinator.force_reload: "
            "module=%s event=%s valid=%s",
            module_name,
            event.event_id,
            valid,
        )

        diff_serializable = {
            k: sorted(v) if isinstance(v, frozenset) else v
            for k, v in diff.items()
        }

        return {
            "event": event.to_dict(),
            "obs_id": obs_id,
            "valid": valid,
            "diff": diff_serializable,
        }


__all__ = [
    "ReloadKind",
    "DevSessionPhase",
    "ReloadEvent",
    "DevModeState",
    "ReloadDiff",
    "HotReloadEngine",
    "DevModeObservation",
    "DevModeWitness",
    "HotReloadDevelopmentModeCoordinator",
]

# copilot: s05 — Hot Reload and Development Mode Semantics (Ch23 §5)
