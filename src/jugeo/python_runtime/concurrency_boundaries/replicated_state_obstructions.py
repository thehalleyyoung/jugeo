from __future__ import annotations

"""replicated_state_obstructions — Replicated State Obstructions.

Theory reference: Ch24 §5

When two processes hold local sections of the replicated state that disagree on
overlapping keys, the gluing morphism fails — this is an obstruction in the sheaf
sense.  This module implements the Coordinator-Analyzer-Witness pattern for
detecting, classifying, witnessing, and resolving such obstructions.
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
    from jugeo.core.divergence import DivergenceReport  # type: ignore
except Exception:  # pragma: no cover
    class DivergenceReport:  # type: ignore
        """Inline stub for jugeo.core.divergence.DivergenceReport."""
        def __init__(self, process_id: str, keys: frozenset[str]) -> None:
            self.process_id = process_id
            self.keys = keys
        def describe(self) -> str:
            return f"Divergence in {self.process_id!r}: keys={sorted(self.keys)}"

try:
    from jugeo.sheaf.gluing import GluingCondition  # type: ignore
except Exception:  # pragma: no cover
    class GluingCondition:  # type: ignore
        """Inline stub for jugeo.sheaf.gluing.GluingCondition."""
        def __init__(self, satisfied: bool) -> None:
            self.satisfied = satisfied
        def check(self) -> bool:
            return self.satisfied

try:
    from jugeo.evidence.obstruction import ObstructionEvidence  # type: ignore
except Exception:  # pragma: no cover
    class ObstructionEvidence:  # type: ignore
        """Inline stub for jugeo.evidence.obstruction.ObstructionEvidence."""
        def __init__(self, keys: frozenset[str]) -> None:
            self.keys = keys
        def to_dict(self) -> dict:
            return {"keys": sorted(self.keys)}

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class ObstructionKind(str, Enum):
    """Classification of the type of replicated-state obstruction.

    Each value names a distinct failure mode in the gluing condition between
    two replicated state sections.

    Example::

        kind = ObstructionKind.VALUE_DIVERGENCE
    """

    KEY_CONFLICT = "KEY_CONFLICT"
    EPOCH_MISMATCH = "EPOCH_MISMATCH"
    MISSING_KEY = "MISSING_KEY"
    VALUE_DIVERGENCE = "VALUE_DIVERGENCE"
    CIRCULAR_DEPENDENCY = "CIRCULAR_DEPENDENCY"
    SCHEMA_MISMATCH = "SCHEMA_MISMATCH"


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def _new_obstruction_id() -> str:
    """Return a short unique obstruction identifier.

    Returns:
        12-character hex string from uuid4.
    """
    return uuid.uuid4().hex[:12]


def _new_vector_id() -> str:
    """Return a short unique state-vector identifier.

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


def _base_severity(kind: ObstructionKind) -> int:
    """Map an ObstructionKind to a base severity score.

    Args:
        kind: The kind to score.

    Returns:
        Integer in [1, 10].
    """
    scores: dict[ObstructionKind, int] = {
        ObstructionKind.MISSING_KEY: 3,
        ObstructionKind.EPOCH_MISMATCH: 4,
        ObstructionKind.KEY_CONFLICT: 5,
        ObstructionKind.VALUE_DIVERGENCE: 6,
        ObstructionKind.SCHEMA_MISMATCH: 7,
        ObstructionKind.CIRCULAR_DEPENDENCY: 9,
    }
    return scores.get(kind, 5)


def _vector_key_set(vector: StateVector) -> frozenset[str]:
    """Extract the set of keys from a StateVector's key_values tuple.

    Args:
        vector: The StateVector to extract keys from.

    Returns:
        Frozenset of key strings.
    """
    return frozenset(kv[0] for kv in vector.key_values)


def _vector_value_map(vector: StateVector) -> dict[str, str]:
    """Build a plain dict mapping key → value from a StateVector.

    Args:
        vector: The StateVector to extract.

    Returns:
        Dict of key/value pairs.
    """
    return {kv[0]: kv[1] for kv in vector.key_values}


def _classify_by_comparison(
    v1_keys: frozenset[str],
    v2_keys: frozenset[str],
    v1_vals: dict[str, str],
    v2_vals: dict[str, str],
    epoch_delta: int,
) -> ObstructionKind:
    """Determine the primary obstruction kind from key/value comparisons.

    Args:
        v1_keys: Key set of the first vector.
        v2_keys: Key set of the second vector.
        v1_vals: Value map of the first vector.
        v2_vals: Value map of the second vector.
        epoch_delta: abs(v1.epoch - v2.epoch).

    Returns:
        The most specific ObstructionKind that applies.
    """
    if epoch_delta > 1:
        return ObstructionKind.EPOCH_MISMATCH

    only_in_v1 = v1_keys - v2_keys
    only_in_v2 = v2_keys - v1_keys
    if only_in_v1 or only_in_v2:
        return ObstructionKind.MISSING_KEY

    # Both vectors have the same keys; check value differences.
    shared = v1_keys & v2_keys
    value_diffs = {k for k in shared if v1_vals.get(k) != v2_vals.get(k)}
    if value_diffs:
        # Heuristic: if all differing values look like type names (contain '.'),
        # classify as SCHEMA_MISMATCH; otherwise VALUE_DIVERGENCE.
        schema_like = all("." in v1_vals.get(k, "") or "." in v2_vals.get(k, "") for k in value_diffs)
        return ObstructionKind.SCHEMA_MISMATCH if schema_like else ObstructionKind.VALUE_DIVERGENCE

    return ObstructionKind.KEY_CONFLICT


# ---------------------------------------------------------------------------
# Frozen record dataclasses
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class ObstructionRecord:
    """An immutable record describing a replicated-state obstruction.

    An obstruction arises when two process-local state sections cannot be
    glued — the restriction maps disagree on the overlap.

    Attributes:
        obstruction_id: Unique identifier for this obstruction.
        process_ids: Tuple of process identifiers involved in the obstruction.
        conflicting_keys: Frozenset of binding keys at the centre of the conflict.
        obstruction_kind: Classification of the obstruction type.
        severity: Integer severity in [1, 10].
        detected_at: Monotonic timestamp of detection.
        resolved_at: Monotonic timestamp of resolution, or None.
        resolution_strategy: Name of the resolution strategy applied, or None.

    Example::

        rec = ObstructionRecord(
            obstruction_id="o001",
            process_ids=("proc-A", "proc-B"),
            conflicting_keys=frozenset(["user_data"]),
            obstruction_kind=ObstructionKind.VALUE_DIVERGENCE,
            severity=6,
            detected_at=time.monotonic(),
            resolved_at=None,
            resolution_strategy=None,
        )
    """

    obstruction_id: str
    process_ids: tuple[str, ...]
    conflicting_keys: frozenset[str]
    obstruction_kind: str
    severity: int
    detected_at: float
    resolved_at: float | None
    resolution_strategy: str | None

    def to_dict(self) -> dict[str, object]:
        """Serialise to a plain dictionary.

        Returns:
            Dict with all fields as JSON-compatible types.
        """
        return {
            "obstruction_id": self.obstruction_id,
            "process_ids": list(self.process_ids),
            "conflicting_keys": sorted(self.conflicting_keys),
            "obstruction_kind": self.obstruction_kind,
            "severity": self.severity,
            "detected_at": self.detected_at,
            "resolved_at": self.resolved_at,
            "resolution_strategy": self.resolution_strategy,
        }

    def is_resolved(self) -> bool:
        """Return True if this obstruction has been resolved.

        Returns:
            Boolean.
        """
        return self.resolved_at is not None

    def fingerprint(self) -> str:
        """Return a deterministic content fingerprint.

        Returns:
            64-character hex string.
        """
        return _fingerprint(self.to_dict())


@dataclass(frozen=True, slots=True)
class StateVector:
    """An immutable representation of a process's state as a key-value vector.

    State vectors are the inputs to obstruction detection; two vectors from
    different processes are compared to find conflicting or missing keys.

    Attributes:
        vector_id: Unique identifier for this vector.
        process_id: The process that owns this state vector.
        key_values: Tuple of (key, value) string pairs representing the state.
        epoch: Monotonically increasing epoch counter for this vector.
        created_at: Monotonic timestamp of vector creation.

    Example::

        sv = StateVector(
            vector_id="v001",
            process_id="proc-A",
            key_values=(("user", "alice"), ("role", "admin")),
            epoch=3,
            created_at=time.monotonic(),
        )
    """

    vector_id: str
    process_id: str
    key_values: tuple[tuple[str, str], ...]
    epoch: int
    created_at: float

    def to_dict(self) -> dict[str, object]:
        """Serialise to a plain dictionary.

        Returns:
            Dict with all fields as JSON-compatible types.
        """
        return {
            "vector_id": self.vector_id,
            "process_id": self.process_id,
            "key_values": [list(kv) for kv in self.key_values],
            "epoch": self.epoch,
            "created_at": self.created_at,
        }

    def keys(self) -> frozenset[str]:
        """Return the frozenset of keys in this vector.

        Returns:
            Frozenset of key strings.
        """
        return _vector_key_set(self)

    def values(self) -> dict[str, str]:
        """Return a dict mapping key → value for this vector.

        Returns:
            Dict of key/value string pairs.
        """
        return _vector_value_map(self)

    def fingerprint(self) -> str:
        """Return a deterministic content fingerprint.

        Returns:
            64-character hex string.
        """
        return _fingerprint(self.to_dict())

    def key_count(self) -> int:
        """Return the number of key/value pairs in this vector.

        Returns:
            Non-negative integer.
        """
        return len(self.key_values)


# ---------------------------------------------------------------------------
# Analyzer
# ---------------------------------------------------------------------------

@dataclass
class ObstructionDetector:
    """Detects, classifies, and resolves replicated-state obstructions.

    Maintains a registry of ObstructionRecord objects and provides methods
    for detecting obstructions between StateVectors, computing density
    metrics, and tracking resolution.

    Attributes:
        _obstructions: All registered ObstructionRecord objects keyed by obstruction_id.
        _process_obstructions: Maps process_id → list of obstruction_ids.
        _vectors: All registered StateVector objects keyed by vector_id.

    Example::

        detector = ObstructionDetector()
        records = detector.detect(v1, v2)
    """

    _obstructions: dict[str, ObstructionRecord] = field(default_factory=dict)
    _process_obstructions: dict[str, list[str]] = field(default_factory=dict)
    _vectors: dict[str, StateVector] = field(default_factory=dict)

    def detect(
        self,
        v1: StateVector,
        v2: StateVector,
    ) -> list[ObstructionRecord]:
        """Compare two StateVectors and produce obstruction records for each conflict.

        The comparison checks for:
        - Key-set differences (MISSING_KEY)
        - Value differences on shared keys (VALUE_DIVERGENCE / SCHEMA_MISMATCH)
        - Epoch mismatches (EPOCH_MISMATCH)

        Each conflicting key cluster yields one ObstructionRecord.

        Args:
            v1: First StateVector.
            v2: Second StateVector.

        Returns:
            List of newly created ObstructionRecord objects.

        Example::

            records = detector.detect(v1, v2)
        """
        k1 = v1.keys()
        k2 = v2.keys()
        val1 = v1.values()
        val2 = v2.values()
        epoch_delta = abs(v1.epoch - v2.epoch)

        results: list[ObstructionRecord] = []

        # --- Group 1: keys present only in v1 ---
        only_in_v1 = k1 - k2
        if only_in_v1:
            kind = ObstructionKind.MISSING_KEY
            sev = _base_severity(kind) + min(len(only_in_v1), 3)
            rec = ObstructionRecord(
                obstruction_id=_new_obstruction_id(),
                process_ids=(v1.process_id, v2.process_id),
                conflicting_keys=only_in_v1,
                obstruction_kind=kind.value,
                severity=min(sev, 10),
                detected_at=time.monotonic(),
                resolved_at=None,
                resolution_strategy=None,
            )
            self._register(rec, v1.process_id, v2.process_id)
            results.append(rec)

        # --- Group 2: keys present only in v2 ---
        only_in_v2 = k2 - k1
        if only_in_v2:
            kind = ObstructionKind.MISSING_KEY
            sev = _base_severity(kind) + min(len(only_in_v2), 3)
            rec = ObstructionRecord(
                obstruction_id=_new_obstruction_id(),
                process_ids=(v2.process_id, v1.process_id),
                conflicting_keys=only_in_v2,
                obstruction_kind=kind.value,
                severity=min(sev, 10),
                detected_at=time.monotonic(),
                resolved_at=None,
                resolution_strategy=None,
            )
            self._register(rec, v1.process_id, v2.process_id)
            results.append(rec)

        # --- Group 3: shared keys with differing values ---
        shared = k1 & k2
        diverging_keys: frozenset[str] = frozenset(
            k for k in shared if val1.get(k) != val2.get(k)
        )
        if diverging_keys:
            kind = _classify_by_comparison(k1, k2, val1, val2, epoch_delta)
            sev = _base_severity(kind) + min(len(diverging_keys), 4)
            if epoch_delta > 0:
                sev += 1
            rec = ObstructionRecord(
                obstruction_id=_new_obstruction_id(),
                process_ids=(v1.process_id, v2.process_id),
                conflicting_keys=diverging_keys,
                obstruction_kind=kind.value,
                severity=min(sev, 10),
                detected_at=time.monotonic(),
                resolved_at=None,
                resolution_strategy=None,
            )
            self._register(rec, v1.process_id, v2.process_id)
            results.append(rec)

        # --- Group 4: epoch mismatch with no other conflict ---
        if epoch_delta > 1 and not diverging_keys and not only_in_v1 and not only_in_v2:
            kind = ObstructionKind.EPOCH_MISMATCH
            sev = _base_severity(kind)
            rec = ObstructionRecord(
                obstruction_id=_new_obstruction_id(),
                process_ids=(v1.process_id, v2.process_id),
                conflicting_keys=frozenset(),
                obstruction_kind=kind.value,
                severity=sev,
                detected_at=time.monotonic(),
                resolved_at=None,
                resolution_strategy=None,
            )
            self._register(rec, v1.process_id, v2.process_id)
            results.append(rec)

        _log.debug("detect(%s, %s) → %d obstructions", v1.vector_id, v2.vector_id, len(results))
        return results

    def _register(
        self,
        record: ObstructionRecord,
        *process_ids: str,
    ) -> None:
        """Internal helper: add *record* to the registry and update process index.

        Args:
            record: The ObstructionRecord to register.
            *process_ids: Process identifiers to index under.
        """
        self._obstructions[record.obstruction_id] = record
        for pid in process_ids:
            self._process_obstructions.setdefault(pid, []).append(record.obstruction_id)

    def classify_obstruction(
        self,
        conflicting_keys: frozenset[str],
        v1: StateVector,
        v2: StateVector,
    ) -> ObstructionKind:
        """Classify the obstruction kind for a specific set of conflicting keys.

        Args:
            conflicting_keys: The keys causing the obstruction.
            v1: First StateVector.
            v2: Second StateVector.

        Returns:
            The most appropriate ObstructionKind.

        Example::

            kind = detector.classify_obstruction(frozenset(["x"]), v1, v2)
        """
        k1, k2 = v1.keys(), v2.keys()
        val1, val2 = v1.values(), v2.values()
        epoch_delta = abs(v1.epoch - v2.epoch)
        return _classify_by_comparison(k1, k2, val1, val2, epoch_delta)

    def severity_score(self, record: ObstructionRecord) -> int:
        """Return the effective severity score for an obstruction record.

        Applies a +1 bonus for records with many conflicting keys and a −2
        discount for resolved records.

        Args:
            record: The ObstructionRecord to score.

        Returns:
            Integer in [1, 10].

        Example::

            score = detector.severity_score(rec)
        """
        base = record.severity
        key_bonus = min(len(record.conflicting_keys) // 3, 2)
        resolved_discount = -2 if record.is_resolved() else 0
        return max(1, min(base + key_bonus + resolved_discount, 10))

    def resolve_obstruction(self, obstruction_id: str, strategy: str) -> bool:
        """Mark an obstruction as resolved with the given strategy.

        Args:
            obstruction_id: The obstruction to resolve.
            strategy: Name of the resolution strategy applied.

        Returns:
            True if found and updated; False if not found.

        Example::

            ok = detector.resolve_obstruction("o001", "last-write-wins")
        """
        rec = self._obstructions.get(obstruction_id)
        if rec is None:
            _log.warning("resolve_obstruction: unknown id %s", obstruction_id)
            return False
        resolved = ObstructionRecord(
            obstruction_id=rec.obstruction_id,
            process_ids=rec.process_ids,
            conflicting_keys=rec.conflicting_keys,
            obstruction_kind=rec.obstruction_kind,
            severity=rec.severity,
            detected_at=rec.detected_at,
            resolved_at=time.monotonic(),
            resolution_strategy=strategy,
        )
        self._obstructions[obstruction_id] = resolved
        _log.debug("Resolved obstruction %s via strategy %r", obstruction_id, strategy)
        return True

    def find_circular_deps(
        self, vectors: list[StateVector]
    ) -> list[tuple[str, ...]]:
        """Detect circular key dependencies across a set of StateVectors.

        A circular dependency exists when process A's value for key K is the
        same as another key in process B, and vice versa — forming a ring.

        Args:
            vectors: StateVectors to inspect.

        Returns:
            List of tuples, each representing a cycle as a sequence of
            (process_id, key) pairs.

        Example::

            cycles = detector.find_circular_deps(vectors)
        """
        # Build a directed graph: process -> keys that reference other process keys.
        # For simplicity: a key "x" in v1 pointing to value "y" where "y" is a key
        # in v2 (for a different process) forms a directed edge.
        cycles: list[tuple[str, ...]] = []
        by_process: dict[str, dict[str, str]] = {}
        for v in vectors:
            by_process[v.process_id] = v.values()

        all_keys_all_processes: dict[str, set[str]] = {}
        for pid, kv in by_process.items():
            all_keys_all_processes[pid] = set(kv.keys())

        visited_pairs: set[tuple[str, str]] = set()
        for pid, kv in by_process.items():
            for key, value in kv.items():
                # Check if 'value' is a key in any OTHER process.
                for other_pid, other_keys in all_keys_all_processes.items():
                    if other_pid == pid:
                        continue
                    if value in other_keys:
                        # Edge: pid.key → other_pid.value
                        other_val = by_process[other_pid].get(value, "")
                        if other_val == key:
                            # Cycle: pid.key ↔ other_pid.value
                            pair = tuple(sorted([(pid, key), (other_pid, value)]))  # type: ignore[arg-type]
                            if pair not in visited_pairs:
                                visited_pairs.add(pair)  # type: ignore[arg-type]
                                cycles.append((f"{pid}.{key}", f"{other_pid}.{value}"))
        return cycles

    def unresolved_obstructions(self) -> list[ObstructionRecord]:
        """Return all unresolved obstruction records.

        Returns:
            List of ObstructionRecord objects where resolved_at is None.
        """
        return [r for r in self._obstructions.values() if not r.is_resolved()]

    def obstruction_density(self, process_ids: list[str]) -> float:
        """Compute the obstruction density for a set of processes.

        Density is the ratio of unresolved obstructions to the total possible
        pairwise comparisons among the provided processes.

        Args:
            process_ids: Processes to include in the density calculation.

        Returns:
            Float in [0.0, 1.0]; 0.0 if no unresolved obstructions or fewer
            than 2 processes.

        Example::

            density = detector.obstruction_density(["proc-A", "proc-B", "proc-C"])
        """
        n = len(process_ids)
        max_pairs = n * (n - 1) / 2 if n >= 2 else 1
        unresolved_count = sum(
            1 for r in self.unresolved_obstructions()
            if any(pid in r.process_ids for pid in process_ids)
        )
        return min(unresolved_count / max_pairs, 1.0)

    def export_records(self) -> list[dict[str, object]]:
        """Export all obstruction records as plain dicts.

        Returns:
            List of serialised ObstructionRecord dicts with effective severity.
        """
        result: list[dict[str, object]] = []
        for rec in self._obstructions.values():
            d = rec.to_dict()
            d["effective_severity"] = self.severity_score(rec)
            result.append(d)
        return result

    def stats(self) -> dict[str, object]:
        """Return summary statistics for the obstruction detector.

        Returns:
            Dict with: ``total_obstructions``, ``unresolved``, ``resolved``,
            ``by_kind``, ``processes``, ``avg_severity``.
        """
        all_recs = list(self._obstructions.values())
        by_kind: dict[str, int] = {}
        for r in all_recs:
            by_kind[r.obstruction_kind] = by_kind.get(r.obstruction_kind, 0) + 1
        avg_sev = (
            sum(self.severity_score(r) for r in all_recs) / len(all_recs)
            if all_recs
            else 0.0
        )
        unresolved = sum(1 for r in all_recs if not r.is_resolved())
        return {
            "total_obstructions": len(all_recs),
            "unresolved": unresolved,
            "resolved": len(all_recs) - unresolved,
            "by_kind": by_kind,
            "processes": list(self._process_obstructions.keys()),
            "avg_severity": round(avg_sev, 3),
        }


# ---------------------------------------------------------------------------
# Witness
# ---------------------------------------------------------------------------

@dataclass
class ObstructionWitness:
    """Witnesses replicated-state obstructions in the sheaf.

    Collects observations of obstruction records and resolutions, and produces
    exportable evidence bundles for downstream sheaf-morphism verification.

    Attributes:
        _observations: Observation dicts keyed by obs_id.
        _process_obs_index: Maps process_id → list of obs_ids.
        _resolutions: Resolution dicts keyed by res_id.
        _obs_counter: Monotonic sequence counter for observations.

    Example::

        witness = ObstructionWitness()
        obs_id = witness.observe_obstruction(record)
    """

    _observations: dict[str, dict[str, object]] = field(default_factory=dict)
    _process_obs_index: dict[str, list[str]] = field(default_factory=dict)
    _resolutions: dict[str, dict[str, object]] = field(default_factory=dict)
    _obs_counter: int = field(default=0)

    def observe_obstruction(self, record: ObstructionRecord) -> str:
        """Record an observation of an ObstructionRecord.

        Args:
            record: The obstruction to observe.

        Returns:
            The obs_id for this witness entry.

        Example::

            oid = witness.observe_obstruction(rec)
        """
        obs_id = f"obs_{_new_obstruction_id()}"
        self._obs_counter += 1
        obs: dict[str, object] = {
            "obs_id": obs_id,
            "obstruction_id": record.obstruction_id,
            "process_ids": list(record.process_ids),
            "conflicting_keys": sorted(record.conflicting_keys),
            "obstruction_kind": record.obstruction_kind,
            "severity": record.severity,
            "is_resolved": record.is_resolved(),
            "seq": self._obs_counter,
            "observed_at": time.monotonic(),
        }
        self._observations[obs_id] = obs
        for pid in record.process_ids:
            self._process_obs_index.setdefault(pid, []).append(obs_id)
        _log.debug("ObstructionWitness: observed obstruction %s → obs %s",
                   record.obstruction_id, obs_id)
        return obs_id

    def witness_resolution(self, obstruction_id: str, strategy: str) -> bool:
        """Record the resolution of an obstruction as a witness event.

        Args:
            obstruction_id: The obstruction that was resolved.
            strategy: The resolution strategy applied.

        Returns:
            True if the resolution was newly recorded; False if already
            recorded for this obstruction_id.

        Example::

            was_new = witness.witness_resolution("o001", "last-write-wins")
        """
        existing = [
            r for r in self._resolutions.values()
            if r.get("obstruction_id") == obstruction_id
        ]
        if existing:
            _log.debug("Resolution for %s already witnessed", obstruction_id)
            return False
        res_id = f"res_{_new_obstruction_id()}"
        self._resolutions[res_id] = {
            "res_id": res_id,
            "obstruction_id": obstruction_id,
            "strategy": strategy,
            "witnessed_at": time.monotonic(),
        }
        _log.info("Witnessed resolution of obstruction %s via %r", obstruction_id, strategy)
        return True

    def get_observations_for_process(
        self, process_id: str
    ) -> list[dict[str, object]]:
        """Return all witness observations for a given process.

        Args:
            process_id: The process to query.

        Returns:
            List of observation dicts.

        Example::

            obs = witness.get_observations_for_process("proc-A")
        """
        ids = self._process_obs_index.get(process_id, [])
        return [self._observations[oid] for oid in ids if oid in self._observations]

    def unresolved_count(self) -> int:
        """Return the count of observed obstructions that are not yet resolved.

        Returns:
            Integer count.
        """
        resolved_ids = {
            str(r.get("obstruction_id")) for r in self._resolutions.values()
        }
        return sum(
            1 for obs in self._observations.values()
            if str(obs.get("obstruction_id")) not in resolved_ids
        )

    def severity_distribution(self) -> dict[str, int]:
        """Return a distribution of observed obstructions by severity bucket.

        Buckets: ``"low"`` (1-3), ``"medium"`` (4-6), ``"high"`` (7-10).

        Returns:
            Dict with keys ``"low"``, ``"medium"``, ``"high"``.

        Example::

            dist = witness.severity_distribution()
        """
        dist: dict[str, int] = {"low": 0, "medium": 0, "high": 0}
        for obs in self._observations.values():
            sev = int(obs.get("severity", 5))
            if sev <= 3:
                dist["low"] += 1
            elif sev <= 6:
                dist["medium"] += 1
            else:
                dist["high"] += 1
        return dist

    def generate_obstruction_certificate(self) -> dict[str, object]:
        """Generate a certificate bundle summarising all obstruction evidence.

        Returns:
            Dict with ``cert_id``, ``total_observations``, ``total_resolutions``,
            ``unresolved_count``, ``severity_distribution``, ``issued_at``,
            ``fingerprint``.

        Example::

            cert = witness.generate_obstruction_certificate()
        """
        payload: dict[str, object] = {
            "cert_id": f"cert_{_new_obstruction_id()}",
            "total_observations": len(self._observations),
            "total_resolutions": len(self._resolutions),
            "unresolved_count": self.unresolved_count(),
            "severity_distribution": self.severity_distribution(),
            "issued_at": time.monotonic(),
        }
        payload["fingerprint"] = _fingerprint(payload)
        return payload

    def export_evidence(self) -> list[dict[str, object]]:
        """Export all observation and resolution evidence as a flat list.

        Returns:
            Combined list of observation and resolution dicts, each tagged
            with a ``kind`` field.
        """
        evidence: list[dict[str, object]] = []
        for obs in self._observations.values():
            evidence.append({"kind": "observation", **obs})
        for res in self._resolutions.values():
            evidence.append({"kind": "resolution", **res})
        return evidence


# ---------------------------------------------------------------------------
# Coordinator
# ---------------------------------------------------------------------------

@dataclass
class ReplicatedStateObstructionsCoordinator:
    """Orchestrates replicated-state obstruction detection, witnessing, and resolution.

    Combines an ObstructionDetector and an ObstructionWitness to provide a
    single entry point for the full Coordinator-Analyzer-Witness workflow
    for replicated-state obstructions.

    Attributes:
        _detector: The underlying ObstructionDetector.
        _witness: The underlying ObstructionWitness.
        _vectors: All registered StateVector objects keyed by vector_id.
        _session_id: Unique identifier for this coordinator session.

    Example::

        coord = ReplicatedStateObstructionsCoordinator()
        v = coord.register_state_vector("proc-A", (("k", "v"),), 1)
    """

    _detector: ObstructionDetector = field(default_factory=ObstructionDetector)
    _witness: ObstructionWitness = field(default_factory=ObstructionWitness)
    _vectors: dict[str, StateVector] = field(default_factory=dict)
    _session_id: str = field(default_factory=lambda: _new_obstruction_id())

    def register_state_vector(
        self,
        process_id: str,
        key_values: tuple[tuple[str, str], ...],
        epoch: int,
    ) -> StateVector:
        """Create and register a new StateVector.

        Args:
            process_id: The process owning this vector.
            key_values: Tuple of (key, value) string pairs.
            epoch: The epoch counter for this vector.

        Returns:
            The newly created StateVector.

        Raises:
            ValueError: If *process_id* is empty or *epoch* is negative.

        Example::

            v = coord.register_state_vector("proc-A", (("k", "v"),), epoch=1)
        """
        if not process_id:
            raise ValueError("process_id must not be empty")
        if epoch < 0:
            raise ValueError("epoch must be non-negative")

        vector = StateVector(
            vector_id=_new_vector_id(),
            process_id=process_id,
            key_values=tuple(key_values),
            epoch=epoch,
            created_at=time.monotonic(),
        )
        self._vectors[vector.vector_id] = vector
        self._detector._vectors[vector.vector_id] = vector
        _log.debug("Registered state vector %s for process %s", vector.vector_id, process_id)
        return vector

    def compare_state_vectors(
        self,
        v1: StateVector,
        v2: StateVector,
    ) -> list[dict[str, object]]:
        """Compare two StateVectors and return enriched obstruction dicts.

        Args:
            v1: First StateVector.
            v2: Second StateVector.

        Returns:
            List of obstruction dicts, each with an ``obs_id`` field from
            the witness.

        Example::

            obstructions = coord.compare_state_vectors(v1, v2)
        """
        records = self._detector.detect(v1, v2)
        enriched: list[dict[str, object]] = []
        for rec in records:
            obs_id = self._witness.observe_obstruction(rec)
            enriched.append({**rec.to_dict(), "obs_id": obs_id})
        return enriched

    def resolve_obstructions(self, process_id: str, strategy: str) -> int:
        """Resolve all unresolved obstructions for a given process.

        Args:
            process_id: The process whose obstructions to resolve.
            strategy: The resolution strategy to apply.

        Returns:
            The number of obstructions resolved.

        Example::

            count = coord.resolve_obstructions("proc-A", "last-write-wins")
        """
        obs_ids = self._detector._process_obstructions.get(process_id, [])
        resolved_count = 0
        for oid in obs_ids:
            rec = self._detector._obstructions.get(oid)
            if rec is None or rec.is_resolved():
                continue
            if self._detector.resolve_obstruction(oid, strategy):
                self._witness.witness_resolution(oid, strategy)
                resolved_count += 1
        _log.info("Resolved %d obstructions for process %s", resolved_count, process_id)
        return resolved_count

    def obstruction_summary(self, process_ids: list[str]) -> dict[str, object]:
        """Produce an obstruction summary for a set of processes.

        Args:
            process_ids: Processes to include in the summary.

        Returns:
            Dict with ``process_ids``, ``density``, ``unresolved_count``,
            ``severity_distribution``, ``by_kind``.

        Example::

            summary = coord.obstruction_summary(["proc-A", "proc-B"])
        """
        density = self._detector.obstruction_density(process_ids)
        by_kind: dict[str, int] = {}
        for pid in process_ids:
            obs_ids = self._detector._process_obstructions.get(pid, [])
            for oid in obs_ids:
                rec = self._detector._obstructions.get(oid)
                if rec:
                    by_kind[rec.obstruction_kind] = by_kind.get(rec.obstruction_kind, 0) + 1

        return {
            "process_ids": process_ids,
            "density": round(density, 4),
            "unresolved_count": self._witness.unresolved_count(),
            "severity_distribution": self._witness.severity_distribution(),
            "by_kind": by_kind,
        }

    def full_report(self) -> dict[str, object]:
        """Produce a comprehensive report for the entire coordinator session.

        Returns:
            Dict with ``session_id``, ``detector_stats``,
            ``witness_certificate``, ``all_obstructions``,
            ``all_evidence``.

        Example::

            report = coord.full_report()
        """
        return {
            "session_id": self._session_id,
            "detector_stats": self._detector.stats(),
            "witness_certificate": self._witness.generate_obstruction_certificate(),
            "all_obstructions": self._detector.export_records(),
            "all_evidence": self._witness.export_evidence(),
        }

    def reset(self) -> None:
        """Clear all state in the coordinator.

        Example::

            coord.reset()
        """
        self._detector = ObstructionDetector()
        self._witness = ObstructionWitness()
        self._vectors.clear()
        self._session_id = _new_obstruction_id()
        _log.info("ReplicatedStateObstructionsCoordinator reset; session=%s", self._session_id)


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

def make_coordinator() -> ReplicatedStateObstructionsCoordinator:
    """Convenience factory that returns a ready-to-use coordinator.

    Returns:
        A freshly constructed ReplicatedStateObstructionsCoordinator.
    """
    return ReplicatedStateObstructionsCoordinator()


def obstruction_kind_names() -> list[str]:
    """Return all ObstructionKind value names.

    Returns:
        Sorted list of obstruction kind name strings.
    """
    return sorted(k.value for k in ObstructionKind)


def build_state_vector(
    process_id: str,
    key_values: dict[str, str],
    epoch: int,
) -> StateVector:
    """Construct a StateVector from a plain dict.

    Args:
        process_id: The owning process identifier.
        key_values: Dict of key → value string pairs.
        epoch: The epoch counter.

    Returns:
        A new StateVector.

    Example::

        v = build_state_vector("proc-A", {"user": "alice", "role": "admin"}, 1)
    """
    return StateVector(
        vector_id=_new_vector_id(),
        process_id=process_id,
        key_values=tuple(sorted(key_values.items())),
        epoch=epoch,
        created_at=time.monotonic(),
    )


def describe_obstruction(record: ObstructionRecord) -> str:
    """Return a human-readable one-liner for an ObstructionRecord.

    Args:
        record: The ObstructionRecord to describe.

    Returns:
        Formatted string with obstruction_id, kind, severity, and status.
    """
    status = "resolved" if record.is_resolved() else "unresolved"
    keys_str = ", ".join(sorted(record.conflicting_keys)) or "(none)"
    procs_str = "↔".join(record.process_ids)
    return (
        f"Obstruction({record.obstruction_id!r}) {procs_str} "
        f"kind={record.obstruction_kind} sev={record.severity} "
        f"keys=[{keys_str}] {status}"
    )


def merge_vectors(v1: StateVector, v2: StateVector, epoch: int) -> StateVector:
    """Merge two StateVectors into one by taking the union of their key-value pairs.

    When both vectors share a key with different values, v2's value wins.

    Args:
        v1: First StateVector.
        v2: Second StateVector.
        epoch: Epoch for the merged vector.

    Returns:
        A new StateVector with merged key_values and process_id=v2.process_id.

    Example::

        merged = merge_vectors(v1, v2, epoch=5)
    """
    merged: dict[str, str] = {}
    merged.update(v1.values())
    merged.update(v2.values())  # v2 wins on conflict
    return StateVector(
        vector_id=_new_vector_id(),
        process_id=v2.process_id,
        key_values=tuple(sorted(merged.items())),
        epoch=epoch,
        created_at=time.monotonic(),
    )


__all__ = [
    "ObstructionKind",
    "ObstructionRecord",
    "StateVector",
    "ObstructionDetector",
    "ObstructionWitness",
    "ReplicatedStateObstructionsCoordinator",
    "make_coordinator",
    "obstruction_kind_names",
    "build_state_vector",
    "describe_obstruction",
    "merge_vectors",
]

# copilot: s05 — replicated state obstructions; Ch24 §5
