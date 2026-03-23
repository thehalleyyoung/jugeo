from __future__ import annotations

"""process_boundaries_and_replicated — Process Boundaries and Replicated State.

Theory reference: Ch24 §4

In the sheaf model, a process boundary acts as a federation boundary: two processes
share an open cover of the base space (the set of observable keys), and the replicated
state on each side constitutes a local section.  The gluing condition asks whether the
two sections agree on the overlap.  This module implements the Coordinator-Analyzer-Witness
pattern for reasoning about process boundaries and their replicated-state consistency.
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

try:
    from jugeo.core.process import ProcessHandle  # type: ignore
except Exception:  # pragma: no cover
    class ProcessHandle:  # type: ignore
        """Inline stub for jugeo.core.process.ProcessHandle."""
        def __init__(self, pid: int) -> None:
            self.pid = pid
        def is_alive(self) -> bool:
            return True

try:
    from jugeo.sheaf.federation import FederationBoundary  # type: ignore
except Exception:  # pragma: no cover
    class FederationBoundary:  # type: ignore
        """Inline stub for jugeo.sheaf.federation.FederationBoundary."""
        def __init__(self, left_id: str, right_id: str) -> None:
            self.left_id = left_id
            self.right_id = right_id

try:
    from jugeo.evidence.sync import SyncRecord  # type: ignore
except Exception:  # pragma: no cover
    class SyncRecord:  # type: ignore
        """Inline stub for jugeo.evidence.sync.SyncRecord."""
        def __init__(self, source: str, target: str, epoch: int) -> None:
            self.source = source
            self.target = target
            self.epoch = epoch

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class BoundaryKind(str, Enum):
    """The mechanism by which two processes communicate across a boundary.

    Example::

        kind = BoundaryKind.PIPE
    """

    FORK = "FORK"
    SPAWN = "SPAWN"
    PIPE = "PIPE"
    SOCKET = "SOCKET"
    SHARED_MEMORY = "SHARED_MEMORY"
    MESSAGE_QUEUE = "MESSAGE_QUEUE"


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def _new_boundary_id() -> str:
    """Return a short unique boundary identifier.

    Returns:
        12-character hex string from uuid4.
    """
    return uuid.uuid4().hex[:12]


def _new_state_id() -> str:
    """Return a short unique state snapshot identifier.

    Returns:
        12-character hex string from uuid4.
    """
    return uuid.uuid4().hex[:12]


def _fingerprint(data: object) -> str:
    """Produce a SHA-256 hex digest of *data* serialised as JSON.

    Args:
        data: Any JSON-serialisable object.

    Returns:
        64-character hex string.
    """
    raw = json.dumps(data, sort_keys=True, default=str).encode()
    return hashlib.sha256(raw).hexdigest()


def _state_hash(state_keys: frozenset[str], epoch: int) -> str:
    """Produce a hash representing the given state keys and epoch.

    Args:
        state_keys: The set of keys in this state snapshot.
        epoch: The state's epoch number.

    Returns:
        16-character hex string.
    """
    content = {"keys": sorted(state_keys), "epoch": epoch}
    return hashlib.md5(json.dumps(content, sort_keys=True).encode()).hexdigest()[:16]


def _jaccard(set_a: frozenset[str], set_b: frozenset[str]) -> float:
    """Jaccard similarity between two frozensets.

    Args:
        set_a: First set of keys.
        set_b: Second set of keys.

    Returns:
        Float in [0.0, 1.0].
    """
    union = set_a | set_b
    if not union:
        return 0.0
    return len(set_a & set_b) / len(union)


def _boundary_kind_latency(kind: BoundaryKind) -> float:
    """Return a nominal latency estimate (in ms) for a boundary kind.

    These are heuristic values used to weight federation topology analysis.

    Args:
        kind: The BoundaryKind to evaluate.

    Returns:
        Estimated latency in milliseconds.
    """
    latencies = {
        BoundaryKind.SHARED_MEMORY: 0.01,
        BoundaryKind.PIPE: 0.1,
        BoundaryKind.FORK: 0.5,
        BoundaryKind.SPAWN: 2.0,
        BoundaryKind.SOCKET: 5.0,
        BoundaryKind.MESSAGE_QUEUE: 10.0,
    }
    return latencies.get(kind, 1.0)


# ---------------------------------------------------------------------------
# Frozen record dataclasses
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class ProcessBoundaryRecord:
    """An immutable record describing a boundary between two processes.

    Attributes:
        boundary_id: Unique identifier for this boundary record.
        process_id: The local (left-side) process identifier.
        peer_process_id: The remote (right-side) process identifier, or None
            for a one-sided boundary.
        shared_keys: Frozenset of binding keys both processes can observe.
        replicated_keys: Frozenset of binding keys replicated across the boundary.
        boundary_kind: The communication mechanism for this boundary.
        created_at: Monotonic timestamp of record creation.

    Example::

        pbr = ProcessBoundaryRecord(
            boundary_id="b001",
            process_id="proc-A",
            peer_process_id="proc-B",
            shared_keys=frozenset(["config", "schema"]),
            replicated_keys=frozenset(["user_data"]),
            boundary_kind=BoundaryKind.SOCKET,
            created_at=time.monotonic(),
        )
    """

    boundary_id: str
    process_id: str
    peer_process_id: str | None
    shared_keys: frozenset[str]
    replicated_keys: frozenset[str]
    boundary_kind: str
    created_at: float

    def to_dict(self) -> dict[str, object]:
        """Serialise to a plain dictionary.

        Returns:
            Dict with all fields as JSON-compatible types.
        """
        return {
            "boundary_id": self.boundary_id,
            "process_id": self.process_id,
            "peer_process_id": self.peer_process_id,
            "shared_keys": sorted(self.shared_keys),
            "replicated_keys": sorted(self.replicated_keys),
            "boundary_kind": self.boundary_kind,
            "created_at": self.created_at,
        }

    def all_keys(self) -> frozenset[str]:
        """Return the union of shared and replicated keys.

        Returns:
            Frozenset of all relevant keys for this boundary.
        """
        return self.shared_keys | self.replicated_keys

    def fingerprint(self) -> str:
        """Return a deterministic content fingerprint.

        Returns:
            64-character hex string.
        """
        return _fingerprint(self.to_dict())


@dataclass(frozen=True, slots=True)
class ReplicatedStateRecord:
    """An immutable snapshot of the replicated state in one process.

    Attributes:
        state_id: Unique identifier for this snapshot.
        process_id: The process owning this state snapshot.
        state_keys: Frozenset of keys present in the state.
        state_hash: Hash of the state keys and epoch for quick comparison.
        epoch: Monotonically increasing epoch counter for this process.
        is_consistent: True when this snapshot is consistent with its peers.
        last_sync_at: Monotonic timestamp of the last synchronisation event.

    Example::

        rsr = ReplicatedStateRecord(
            state_id="s001",
            process_id="proc-A",
            state_keys=frozenset(["user_data"]),
            state_hash="abc123",
            epoch=1,
            is_consistent=True,
            last_sync_at=time.monotonic(),
        )
    """

    state_id: str
    process_id: str
    state_keys: frozenset[str]
    state_hash: str
    epoch: int
    is_consistent: bool
    last_sync_at: float

    def to_dict(self) -> dict[str, object]:
        """Serialise to a plain dictionary.

        Returns:
            Dict with all fields as JSON-compatible types.
        """
        return {
            "state_id": self.state_id,
            "process_id": self.process_id,
            "state_keys": sorted(self.state_keys),
            "state_hash": self.state_hash,
            "epoch": self.epoch,
            "is_consistent": self.is_consistent,
            "last_sync_at": self.last_sync_at,
        }

    def key_count(self) -> int:
        """Return the number of state keys in this snapshot.

        Returns:
            Non-negative integer.
        """
        return len(self.state_keys)

    def fingerprint(self) -> str:
        """Return a deterministic content fingerprint.

        Returns:
            64-character hex string.
        """
        return _fingerprint(self.to_dict())


# ---------------------------------------------------------------------------
# Analyzers
# ---------------------------------------------------------------------------

@dataclass
class FederationBoundaryAnalyzer:
    """Models process boundaries as federation boundaries in the sheaf.

    Maintains a registry of ProcessBoundaryRecord objects and provides
    methods for federation overlap analysis, isolation violation detection,
    boundary compatibility, and topology computation.

    Attributes:
        _boundaries: All registered boundaries keyed by boundary_id.
        _process_index: Maps process_id → list of boundary_ids.

    Example::

        fba = FederationBoundaryAnalyzer()
        b = fba.register_boundary("proc-A", "proc-B", frozenset(["k"]),
                                   frozenset(), BoundaryKind.SOCKET)
    """

    _boundaries: dict[str, ProcessBoundaryRecord] = field(default_factory=dict)
    _process_index: dict[str, list[str]] = field(default_factory=dict)

    def register_boundary(
        self,
        process_id: str,
        peer_id: str | None,
        shared_keys: frozenset[str],
        replicated_keys: frozenset[str],
        kind: BoundaryKind,
    ) -> ProcessBoundaryRecord:
        """Register a new process boundary record.

        Args:
            process_id: The local process identifier.
            peer_id: The peer process identifier, or None.
            shared_keys: Keys both processes can read.
            replicated_keys: Keys replicated to the peer.
            kind: The boundary communication mechanism.

        Returns:
            The newly created ProcessBoundaryRecord.

        Raises:
            ValueError: If *process_id* is empty.

        Example::

            b = fba.register_boundary("A", "B", frozenset(["x"]),
                                       frozenset(["y"]), BoundaryKind.PIPE)
        """
        if not process_id:
            raise ValueError("process_id must not be empty")

        record = ProcessBoundaryRecord(
            boundary_id=_new_boundary_id(),
            process_id=process_id,
            peer_process_id=peer_id,
            shared_keys=frozenset(shared_keys),
            replicated_keys=frozenset(replicated_keys),
            boundary_kind=kind.value,
            created_at=time.monotonic(),
        )
        self._boundaries[record.boundary_id] = record
        self._process_index.setdefault(process_id, []).append(record.boundary_id)
        if peer_id:
            self._process_index.setdefault(peer_id, []).append(record.boundary_id)
        _log.debug("Registered boundary %s (%s↔%s)", record.boundary_id, process_id, peer_id)
        return record

    def compute_federation_overlap(
        self,
        b1: ProcessBoundaryRecord,
        b2: ProcessBoundaryRecord,
    ) -> frozenset[str]:
        """Compute the key overlap between two boundary records.

        The overlap is the intersection of both boundaries' complete key sets
        (shared ∪ replicated on each side).

        Args:
            b1: First boundary record.
            b2: Second boundary record.

        Returns:
            Frozenset of overlapping binding keys.

        Example::

            overlap = fba.compute_federation_overlap(b1, b2)
        """
        return b1.all_keys() & b2.all_keys()

    def detect_isolation_violations(
        self, records: list[ProcessBoundaryRecord]
    ) -> list[dict[str, object]]:
        """Detect when replicated keys cross boundaries without being shared.

        A violation occurs when boundary B1 replicates a key that appears only
        in the replicated_keys of boundary B2 but not in B2's shared_keys —
        meaning the key leaks into a context where it was not intended to be
        visible.

        Args:
            records: Boundary records to inspect for violations.

        Returns:
            List of violation dicts with ``boundary_id_a``, ``boundary_id_b``,
            ``leaking_keys``.

        Example::

            violations = fba.detect_isolation_violations(records)
        """
        violations: list[dict[str, object]] = []
        for i, b1 in enumerate(records):
            for b2 in records[i + 1:]:
                # Keys replicated by b1 that appear only as replicated (not shared) in b2
                leaking = b1.replicated_keys & (b2.replicated_keys - b2.shared_keys)
                if leaking:
                    violations.append({
                        "boundary_id_a": b1.boundary_id,
                        "boundary_id_b": b2.boundary_id,
                        "leaking_keys": sorted(leaking),
                        "boundary_kind_a": b1.boundary_kind,
                        "boundary_kind_b": b2.boundary_kind,
                    })
        return violations

    def boundary_compatibility(self, b1_id: str, b2_id: str) -> bool:
        """Determine whether two boundaries are mutually compatible.

        Two boundaries are compatible when their shared key sets do not
        contradict each other — specifically, when neither side has a key
        in the other side's replicated set that is absent from both shared sets.

        Args:
            b1_id: First boundary_id.
            b2_id: Second boundary_id.

        Returns:
            True if compatible; False otherwise.

        Raises:
            KeyError: If either boundary_id is not found.

        Example::

            compat = fba.boundary_compatibility("b001", "b002")
        """
        b1 = self._boundaries[b1_id]
        b2 = self._boundaries[b2_id]
        shared_union = b1.shared_keys | b2.shared_keys
        # Check that no replicated key of b1 is an unrevealed secret in b2.
        b1_leaks = b1.replicated_keys - shared_union
        b2_leaks = b2.replicated_keys - shared_union
        return not (b1_leaks or b2_leaks)

    def reachable_processes(self, start_id: str) -> set[str]:
        """Return the set of process_ids reachable from *start_id* via boundaries.

        Args:
            start_id: The starting process identifier.

        Returns:
            Set of reachable process_id strings (including *start_id*).

        Example::

            reachable = fba.reachable_processes("proc-A")
        """
        reachable: set[str] = set()
        queue: deque[str] = deque([start_id])
        while queue:
            current = queue.popleft()
            if current in reachable:
                continue
            reachable.add(current)
            for bid in self._process_index.get(current, []):
                boundary = self._boundaries.get(bid)
                if boundary is None:
                    continue
                for pid in (boundary.process_id, boundary.peer_process_id):
                    if pid and pid not in reachable:
                        queue.append(pid)
        return reachable

    def federation_topology(
        self, records: list[ProcessBoundaryRecord]
    ) -> dict[str, object]:
        """Produce a topology description for a set of boundaries.

        Returns a graph representation with nodes (processes) and edges
        (boundaries), plus aggregate statistics.

        Args:
            records: The boundaries to include in the topology.

        Returns:
            Dict with ``nodes``, ``edges``, ``avg_shared_keys``,
            ``avg_latency_ms``, ``isolation_violations``.

        Example::

            topo = fba.federation_topology(records)
        """
        nodes: set[str] = set()
        edges: list[dict[str, object]] = []
        for b in records:
            nodes.add(b.process_id)
            if b.peer_process_id:
                nodes.add(b.peer_process_id)
            edges.append({
                "boundary_id": b.boundary_id,
                "from": b.process_id,
                "to": b.peer_process_id,
                "kind": b.boundary_kind,
                "shared_keys": len(b.shared_keys),
                "replicated_keys": len(b.replicated_keys),
                "latency_ms": _boundary_kind_latency(BoundaryKind(b.boundary_kind)),
            })

        avg_shared = (
            sum(len(b.shared_keys) for b in records) / len(records) if records else 0.0
        )
        avg_latency = (
            sum(_boundary_kind_latency(BoundaryKind(b.boundary_kind)) for b in records)
            / len(records)
            if records
            else 0.0
        )
        violations = self.detect_isolation_violations(records)
        return {
            "nodes": sorted(nodes),
            "edges": edges,
            "avg_shared_keys": round(avg_shared, 3),
            "avg_latency_ms": round(avg_latency, 3),
            "isolation_violations": violations,
        }

    def export_records(self) -> list[dict[str, object]]:
        """Export all registered boundary records as plain dicts.

        Returns:
            List of serialised ProcessBoundaryRecord dicts.
        """
        return [b.to_dict() for b in self._boundaries.values()]

    def stats(self) -> dict[str, object]:
        """Return summary statistics for the federation boundary analyzer.

        Returns:
            Dict with: ``total_boundaries``, ``processes``,
            ``boundary_kinds``, ``avg_shared_keys``, ``avg_replicated_keys``.
        """
        records = list(self._boundaries.values())
        if not records:
            return {
                "total_boundaries": 0,
                "processes": [],
                "boundary_kinds": {},
                "avg_shared_keys": 0.0,
                "avg_replicated_keys": 0.0,
            }
        kind_counts: dict[str, int] = {}
        for b in records:
            kind_counts[b.boundary_kind] = kind_counts.get(b.boundary_kind, 0) + 1
        return {
            "total_boundaries": len(records),
            "processes": list(self._process_index.keys()),
            "boundary_kinds": kind_counts,
            "avg_shared_keys": round(
                sum(len(b.shared_keys) for b in records) / len(records), 3
            ),
            "avg_replicated_keys": round(
                sum(len(b.replicated_keys) for b in records) / len(records), 3
            ),
        }


@dataclass
class ReplicatedStateAnalyzer:
    """Analyzes replicated state snapshots across process boundaries.

    Provides consistency checking, divergence detection, delta computation,
    and consistency scoring across groups of processes.

    Attributes:
        _snapshots: All registered state snapshots keyed by state_id.
        _process_snapshots: Maps process_id → list of state_ids (newest last).

    Example::

        rsa = ReplicatedStateAnalyzer()
        snap = rsa.snapshot_state("proc-A", frozenset(["user_data"]), epoch=1)
    """

    _snapshots: dict[str, ReplicatedStateRecord] = field(default_factory=dict)
    _process_snapshots: dict[str, list[str]] = field(default_factory=dict)

    def snapshot_state(
        self,
        process_id: str,
        state_keys: frozenset[str],
        epoch: int,
    ) -> ReplicatedStateRecord:
        """Take a new state snapshot for a process.

        Args:
            process_id: The process to snapshot.
            state_keys: The set of keys in the current state.
            epoch: The epoch counter for this snapshot.

        Returns:
            The newly created ReplicatedStateRecord.

        Raises:
            ValueError: If *process_id* is empty or *epoch* is negative.

        Example::

            snap = rsa.snapshot_state("proc-A", frozenset(["k1"]), epoch=2)
        """
        if not process_id:
            raise ValueError("process_id must not be empty")
        if epoch < 0:
            raise ValueError("epoch must be non-negative")

        sh = _state_hash(state_keys, epoch)
        # Determine consistency by checking against the latest snapshot.
        latest = self.latest_snapshot(process_id)
        is_consistent = latest is None or latest.state_hash == sh or epoch > latest.epoch

        record = ReplicatedStateRecord(
            state_id=_new_state_id(),
            process_id=process_id,
            state_keys=frozenset(state_keys),
            state_hash=sh,
            epoch=epoch,
            is_consistent=is_consistent,
            last_sync_at=time.monotonic(),
        )
        self._snapshots[record.state_id] = record
        self._process_snapshots.setdefault(process_id, []).append(record.state_id)
        _log.debug("Snapshot %s for process %s epoch=%d", record.state_id, process_id, epoch)
        return record

    def check_consistency(
        self,
        r1: ReplicatedStateRecord,
        r2: ReplicatedStateRecord,
    ) -> bool:
        """Check whether two state snapshots are mutually consistent.

        Two snapshots are consistent when they share the same state_hash or
        one's epoch is strictly greater (implying the other is a prefix).

        Args:
            r1: First snapshot.
            r2: Second snapshot.

        Returns:
            True if consistent; False otherwise.

        Example::

            ok = rsa.check_consistency(snap_a, snap_b)
        """
        if r1.state_hash == r2.state_hash:
            return True
        # Allow epoch ordering to determine consistency.
        if abs(r1.epoch - r2.epoch) == 1:
            # Adjacent epochs are considered compatible (one is a direct successor).
            return True
        # Check key-set containment: one must be a subset of the other.
        return r1.state_keys <= r2.state_keys or r2.state_keys <= r1.state_keys

    def detect_divergence(
        self,
        process_id: str,
        records: list[ReplicatedStateRecord],
    ) -> list[dict[str, object]]:
        """Detect state divergence events for a given process.

        A divergence is recorded whenever two consecutive snapshots for the
        process have incompatible state hashes and non-adjacent epochs.

        Args:
            process_id: The process to inspect.
            records: All snapshots to scan (not just those for process_id).

        Returns:
            List of divergence dicts with ``state_id_a``, ``state_id_b``,
            ``epoch_delta``, ``key_diff``.

        Example::

            divs = rsa.detect_divergence("proc-A", all_snapshots)
        """
        proc_records = sorted(
            [r for r in records if r.process_id == process_id],
            key=lambda r: r.epoch,
        )
        divergences: list[dict[str, object]] = []
        for i in range(len(proc_records) - 1):
            ra = proc_records[i]
            rb = proc_records[i + 1]
            if not self.check_consistency(ra, rb):
                diff_keys = list((ra.state_keys ^ rb.state_keys))
                divergences.append({
                    "state_id_a": ra.state_id,
                    "state_id_b": rb.state_id,
                    "epoch_delta": rb.epoch - ra.epoch,
                    "key_diff": sorted(diff_keys),
                    "process_id": process_id,
                })
        return divergences

    def sync_delta(
        self,
        s1: ReplicatedStateRecord,
        s2: ReplicatedStateRecord,
    ) -> dict[str, object]:
        """Compute the minimal set of changes needed to bring s1 in sync with s2.

        Args:
            s1: The source snapshot (older / to-be-updated).
            s2: The target snapshot (newer / authoritative).

        Returns:
            Dict with ``keys_to_add``, ``keys_to_remove``, ``epoch_advance``,
            ``already_in_sync``.

        Example::

            delta = rsa.sync_delta(snap_old, snap_new)
        """
        to_add = sorted(s2.state_keys - s1.state_keys)
        to_remove = sorted(s1.state_keys - s2.state_keys)
        epoch_advance = max(0, s2.epoch - s1.epoch)
        return {
            "keys_to_add": to_add,
            "keys_to_remove": to_remove,
            "epoch_advance": epoch_advance,
            "already_in_sync": s1.state_hash == s2.state_hash,
        }

    def consistency_score(self, process_ids: list[str]) -> float:
        """Compute an aggregate consistency score for a set of processes.

        Compares the latest snapshot of each process against each other.
        Score is the fraction of cross-process pairs that are mutually consistent.

        Args:
            process_ids: Process identifiers to compare.

        Returns:
            Float in [0.0, 1.0]; 1.0 means all pairs are consistent.

        Example::

            score = rsa.consistency_score(["proc-A", "proc-B"])
        """
        snapshots = [self.latest_snapshot(pid) for pid in process_ids]
        snapshots = [s for s in snapshots if s is not None]
        if len(snapshots) < 2:
            return 1.0

        total_pairs = 0
        consistent_pairs = 0
        for i, s1 in enumerate(snapshots):
            for s2 in snapshots[i + 1:]:
                total_pairs += 1
                if self.check_consistency(s1, s2):
                    consistent_pairs += 1

        return consistent_pairs / total_pairs if total_pairs else 1.0

    def latest_snapshot(self, process_id: str) -> ReplicatedStateRecord | None:
        """Return the most recent snapshot for a process, or None.

        Args:
            process_id: The process to look up.

        Returns:
            The most recently created ReplicatedStateRecord, or None.

        Example::

            snap = rsa.latest_snapshot("proc-A")
        """
        ids = self._process_snapshots.get(process_id, [])
        if not ids:
            return None
        return self._snapshots.get(ids[-1])

    def export_snapshots(self) -> list[dict[str, object]]:
        """Export all registered state snapshots as plain dicts.

        Returns:
            List of serialised ReplicatedStateRecord dicts.
        """
        return [s.to_dict() for s in self._snapshots.values()]

    def stats(self) -> dict[str, object]:
        """Return summary statistics for the replicated-state analyzer.

        Returns:
            Dict with: ``total_snapshots``, ``processes``,
            ``consistent_snapshots``, ``avg_epoch``.
        """
        snaps = list(self._snapshots.values())
        consistent = sum(1 for s in snaps if s.is_consistent)
        avg_epoch = (
            sum(s.epoch for s in snaps) / len(snaps) if snaps else 0.0
        )
        return {
            "total_snapshots": len(snaps),
            "processes": list(self._process_snapshots.keys()),
            "consistent_snapshots": consistent,
            "avg_epoch": round(avg_epoch, 2),
        }


# ---------------------------------------------------------------------------
# Coordinator
# ---------------------------------------------------------------------------

@dataclass
class ProcessBoundariesReplicatedStateCoordinator:
    """Orchestrates process-boundary and replicated-state analysis.

    Combines FederationBoundaryAnalyzer and ReplicatedStateAnalyzer to provide
    a single entry point for the full Coordinator-Analyzer-Witness workflow
    around process boundaries and replicated state.

    Attributes:
        _fb_analyzer: The underlying FederationBoundaryAnalyzer.
        _rs_analyzer: The underlying ReplicatedStateAnalyzer.
        _session_id: Unique identifier for this coordinator session.
        _sync_log: Ordered log of synchronisation events.

    Example::

        coord = ProcessBoundariesReplicatedStateCoordinator()
        coord.connect_processes("proc-A", "proc-B", frozenset(["k"]),
                                 frozenset(), BoundaryKind.SOCKET)
    """

    _fb_analyzer: FederationBoundaryAnalyzer = field(default_factory=FederationBoundaryAnalyzer)
    _rs_analyzer: ReplicatedStateAnalyzer = field(default_factory=ReplicatedStateAnalyzer)
    _session_id: str = field(default_factory=lambda: _new_boundary_id())
    _sync_log: list[dict[str, object]] = field(default_factory=list)

    def connect_processes(
        self,
        process_id: str,
        peer_id: str | None,
        shared_keys: frozenset[str],
        replicated_keys: frozenset[str],
        kind: BoundaryKind,
    ) -> dict[str, object]:
        """Register a boundary between two processes.

        Args:
            process_id: The local process identifier.
            peer_id: The peer process identifier, or None.
            shared_keys: Keys both processes can observe.
            replicated_keys: Keys replicated across the boundary.
            kind: The communication mechanism.

        Returns:
            Dict with ``boundary_id``, ``process_id``, ``peer_id``,
            ``boundary_kind``, ``all_keys_count``.

        Example::

            r = coord.connect_processes("A", "B", frozenset(["x"]),
                                         frozenset(["y"]), BoundaryKind.PIPE)
        """
        record = self._fb_analyzer.register_boundary(
            process_id, peer_id, shared_keys, replicated_keys, kind
        )
        _log.info("Connected %s ↔ %s via %s", process_id, peer_id, kind.value)
        return {
            "boundary_id": record.boundary_id,
            "process_id": record.process_id,
            "peer_id": record.peer_process_id,
            "boundary_kind": record.boundary_kind,
            "all_keys_count": len(record.all_keys()),
        }

    def sync_state(self, source_id: str, target_id: str) -> dict[str, object]:
        """Synchronise the latest state from *source_id* to *target_id*.

        Takes the latest snapshot of source_id, computes the delta relative
        to target_id's latest snapshot, and records a new snapshot for
        target_id with the merged key set.

        Args:
            source_id: The authoritative process.
            target_id: The process to update.

        Returns:
            Dict with ``delta``, ``new_snapshot_id``, ``consistency_score``.

        Example::

            result = coord.sync_state("proc-A", "proc-B")
        """
        source_snap = self._rs_analyzer.latest_snapshot(source_id)
        target_snap = self._rs_analyzer.latest_snapshot(target_id)

        if source_snap is None:
            return {"error": f"No snapshot for source {source_id!r}"}

        if target_snap is None:
            # First sync — take source keys verbatim.
            new_snap = self._rs_analyzer.snapshot_state(
                target_id, source_snap.state_keys, source_snap.epoch
            )
            delta: dict[str, object] = {
                "keys_to_add": sorted(source_snap.state_keys),
                "keys_to_remove": [],
                "epoch_advance": source_snap.epoch,
                "already_in_sync": False,
            }
        else:
            delta = self._rs_analyzer.sync_delta(target_snap, source_snap)
            merged_keys = source_snap.state_keys | target_snap.state_keys
            new_snap = self._rs_analyzer.snapshot_state(
                target_id, merged_keys, source_snap.epoch
            )

        consistency = self._rs_analyzer.consistency_score([source_id, target_id])
        self._sync_log.append({
            "source_id": source_id,
            "target_id": target_id,
            "new_snapshot_id": new_snap.state_id,
            "synced_at": time.monotonic(),
        })
        return {
            "delta": delta,
            "new_snapshot_id": new_snap.state_id,
            "consistency_score": round(consistency, 4),
        }

    def assess_federation(self, process_ids: list[str]) -> dict[str, object]:
        """Assess the federation health for a set of processes.

        Args:
            process_ids: Processes to include in the assessment.

        Returns:
            Dict with ``consistency_score``, ``reachable_from_first``,
            ``topology``, ``divergence_events``.

        Example::

            assessment = coord.assess_federation(["proc-A", "proc-B"])
        """
        consistency = self._rs_analyzer.consistency_score(process_ids)
        reachable = self._fb_analyzer.reachable_processes(process_ids[0]) if process_ids else set()
        all_boundaries = list(self._fb_analyzer._boundaries.values())
        topo = self._fb_analyzer.federation_topology(all_boundaries)

        all_snaps = list(self._rs_analyzer._snapshots.values())
        divergences: list[dict[str, object]] = []
        for pid in process_ids:
            divergences.extend(self._rs_analyzer.detect_divergence(pid, all_snaps))

        return {
            "consistency_score": round(consistency, 4),
            "reachable_from_first": sorted(reachable),
            "topology": topo,
            "divergence_events": divergences,
        }

    def isolation_report(self) -> dict[str, object]:
        """Report on isolation violations across all registered boundaries.

        Returns:
            Dict with ``boundary_count``, ``violations``, ``violation_count``.

        Example::

            report = coord.isolation_report()
        """
        all_b = list(self._fb_analyzer._boundaries.values())
        violations = self._fb_analyzer.detect_isolation_violations(all_b)
        return {
            "boundary_count": len(all_b),
            "violations": violations,
            "violation_count": len(violations),
        }

    def full_report(self) -> dict[str, object]:
        """Produce a comprehensive report for the entire coordinator session.

        Returns:
            Dict with ``session_id``, ``federation_stats``, ``state_stats``,
            ``sync_log``, ``all_boundaries``, ``all_snapshots``.

        Example::

            report = coord.full_report()
        """
        return {
            "session_id": self._session_id,
            "federation_stats": self._fb_analyzer.stats(),
            "state_stats": self._rs_analyzer.stats(),
            "sync_log": list(self._sync_log),
            "all_boundaries": self._fb_analyzer.export_records(),
            "all_snapshots": self._rs_analyzer.export_snapshots(),
        }

    def reset(self) -> None:
        """Clear all state in the coordinator.

        Example::

            coord.reset()
        """
        self._fb_analyzer = FederationBoundaryAnalyzer()
        self._rs_analyzer = ReplicatedStateAnalyzer()
        self._session_id = _new_boundary_id()
        self._sync_log.clear()
        _log.info("ProcessBoundariesReplicatedStateCoordinator reset; session=%s", self._session_id)


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

def make_coordinator() -> ProcessBoundariesReplicatedStateCoordinator:
    """Convenience factory that returns a ready-to-use coordinator.

    Returns:
        A freshly constructed ProcessBoundariesReplicatedStateCoordinator.
    """
    return ProcessBoundariesReplicatedStateCoordinator()


def boundary_kind_names() -> list[str]:
    """Return all BoundaryKind value names.

    Returns:
        Sorted list of boundary kind name strings.
    """
    return sorted(k.value for k in BoundaryKind)


def compare_snapshots(
    s1: ReplicatedStateRecord,
    s2: ReplicatedStateRecord,
) -> dict[str, object]:
    """Compare two ReplicatedStateRecords and return a human-readable summary.

    Args:
        s1: First snapshot.
        s2: Second snapshot.

    Returns:
        Dict with ``process_ids``, ``epochs``, ``hash_match``,
        ``key_intersection``, ``key_union``, ``similarity``.

    Example::

        cmp = compare_snapshots(snap_a, snap_b)
    """
    intersection = sorted(s1.state_keys & s2.state_keys)
    union = s1.state_keys | s2.state_keys
    similarity = len(s1.state_keys & s2.state_keys) / len(union) if union else 1.0
    return {
        "process_ids": (s1.process_id, s2.process_id),
        "epochs": (s1.epoch, s2.epoch),
        "hash_match": s1.state_hash == s2.state_hash,
        "key_intersection": intersection,
        "key_union": sorted(union),
        "similarity": round(similarity, 4),
    }


def describe_boundary(b: ProcessBoundaryRecord) -> str:
    """Return a one-line description of a ProcessBoundaryRecord.

    Args:
        b: The boundary to describe.

    Returns:
        Formatted string with boundary_id, processes, kind, and key counts.
    """
    peer = b.peer_process_id or "<none>"
    return (
        f"ProcessBoundary({b.boundary_id!r}) "
        f"{b.process_id!r}↔{peer!r} "
        f"kind={b.boundary_kind} "
        f"shared={len(b.shared_keys)} replicated={len(b.replicated_keys)}"
    )


__all__ = [
    "BoundaryKind",
    "ProcessBoundaryRecord",
    "ReplicatedStateRecord",
    "FederationBoundaryAnalyzer",
    "ReplicatedStateAnalyzer",
    "ProcessBoundariesReplicatedStateCoordinator",
    "make_coordinator",
    "boundary_kind_names",
    "compare_snapshots",
    "describe_boundary",
]

# copilot: s04 — process boundaries and replicated state; Ch24 §4
