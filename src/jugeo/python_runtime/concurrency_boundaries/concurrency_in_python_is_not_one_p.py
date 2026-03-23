from __future__ import annotations

"""concurrency_in_python_is_not_one_p — Concurrency in Python is Not One Phenomenon.

Theory reference: Ch24 §1

Thread/process/coroutine coverage levels of the same site are distinct phenomena in
the sheaf-theoretic model.  This module provides the Coordinator-Analyzer-Witness
pattern for classifying and reasoning about concurrency layers.
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
    from jugeo.core.site import SiteKey  # type: ignore
except Exception:  # pragma: no cover
    class SiteKey:  # type: ignore
        """Inline stub for jugeo.core.site.SiteKey."""
        def __init__(self, key: str) -> None:
            self.key = key
        def __repr__(self) -> str:
            return f"SiteKey(key={self.key!r})"

try:
    from jugeo.sheaf.restriction import RestrictionMap  # type: ignore
except Exception:  # pragma: no cover
    class RestrictionMap:  # type: ignore
        """Inline stub for jugeo.sheaf.restriction.RestrictionMap."""
        def __init__(self, data: dict | None = None) -> None:
            self._data: dict = data or {}
        def get(self, key: str) -> object:
            return self._data.get(key)

try:
    from jugeo.evidence.certificate import Certificate  # type: ignore
except Exception:  # pragma: no cover
    class Certificate:  # type: ignore
        """Inline stub for jugeo.evidence.certificate.Certificate."""
        def __init__(self, payload: dict) -> None:
            self.payload = payload
        def to_dict(self) -> dict:
            return self.payload

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class ConcurrencyLayer(str, Enum):
    """The distinct concurrency primitives available in CPython.

    Each value names a concurrency abstraction that produces a different
    coverage pattern over a shared site.

    Example::

        layer = ConcurrencyLayer.THREAD
        print(layer)  # "THREAD"
    """

    THREAD = "THREAD"
    COROUTINE = "COROUTINE"
    PROCESS = "PROCESS"
    GREENLET = "GREENLET"
    SUBPROCESS = "SUBPROCESS"


class CoverageLevel(str, Enum):
    """How thoroughly a concurrency layer covers a shared site's bindings.

    Coverage describes the extent of overlap between the keys observable by
    one concurrency unit and those observable by another.

    Example::

        lvl = CoverageLevel.PARTIAL
        assert lvl != CoverageLevel.FULL
    """

    FULL = "FULL"
    PARTIAL = "PARTIAL"
    MINIMAL = "MINIMAL"
    DISJOINT = "DISJOINT"
    OVERLAPPING = "OVERLAPPING"


# ---------------------------------------------------------------------------
# Helper ID factory
# ---------------------------------------------------------------------------

def _new_record_id() -> str:
    """Return a short unique record identifier.

    Returns:
        A 12-character hex string derived from a UUID4.
    """
    return uuid.uuid4().hex[:12]


def _fingerprint(data: object) -> str:
    """Produce a deterministic SHA-256 hex digest of *data* as JSON.

    Args:
        data: Any JSON-serialisable object.

    Returns:
        64-character hex string.
    """
    raw = json.dumps(data, sort_keys=True, default=str).encode()
    return hashlib.sha256(raw).hexdigest()


def _jaccard(set_a: frozenset[str], set_b: frozenset[str]) -> float:
    """Compute the Jaccard similarity between two sets.

    Args:
        set_a: First set of string keys.
        set_b: Second set of string keys.

    Returns:
        Float in [0.0, 1.0]; 1.0 when both sets are identical and non-empty,
        0.0 when both are empty or fully disjoint.
    """
    union = set_a | set_b
    if not union:
        return 0.0
    return len(set_a & set_b) / len(union)


def _coverage_from_jaccard(score: float) -> CoverageLevel:
    """Map a Jaccard similarity score to a CoverageLevel.

    Args:
        score: Value in [0.0, 1.0].

    Returns:
        The corresponding CoverageLevel bucket.
    """
    if score >= 1.0:
        return CoverageLevel.FULL
    if score >= 0.7:
        return CoverageLevel.OVERLAPPING
    if score >= 0.3:
        return CoverageLevel.PARTIAL
    if score > 0.0:
        return CoverageLevel.MINIMAL
    return CoverageLevel.DISJOINT


# ---------------------------------------------------------------------------
# Frozen record dataclass
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class ConcurrencyRecord:
    """An immutable record describing one concurrency unit's coverage of a site.

    Attributes:
        record_id: Unique identifier for this record.
        layer: Which concurrency primitive produced this record.
        coverage_level: How completely this unit covers the site's bindings.
        site_key: The key of the shared site being covered.
        binding_keys: Frozenset of binding keys visible to this unit.
        created_at: Unix timestamp of record creation.
        metadata: Arbitrary string annotations attached at creation time.

    Example::

        rec = ConcurrencyRecord(
            record_id="abc123",
            layer=ConcurrencyLayer.THREAD,
            coverage_level=CoverageLevel.FULL,
            site_key="site:auth",
            binding_keys=frozenset(["token", "user"]),
            created_at=time.time(),
            metadata=("source=test",),
        )
    """

    record_id: str
    layer: ConcurrencyLayer
    coverage_level: CoverageLevel
    site_key: str
    binding_keys: frozenset[str]
    created_at: float
    metadata: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        """Serialise this record to a plain dictionary.

        Returns:
            Dict with all fields serialised to JSON-compatible types.
        """
        return {
            "record_id": self.record_id,
            "layer": self.layer.value,
            "coverage_level": self.coverage_level.value,
            "site_key": self.site_key,
            "binding_keys": sorted(self.binding_keys),
            "created_at": self.created_at,
            "metadata": list(self.metadata),
        }

    def fingerprint(self) -> str:
        """Return a deterministic fingerprint of this record's content.

        Returns:
            64-character hex string.
        """
        return _fingerprint(self.to_dict())


# ---------------------------------------------------------------------------
# Analyzer
# ---------------------------------------------------------------------------

@dataclass
class LayerCoverageAnalyzer:
    """Analyzes coverage of concurrency layers over a shared site.

    Maintains a registry of ConcurrencyRecord objects and provides methods
    for computing coverage levels, overlaps, conflicts, and isolation scores.

    Attributes:
        _records: All registered ConcurrencyRecord objects keyed by record_id.
        _layer_index: Mapping from ConcurrencyLayer to list of record_ids.
        _site_index: Mapping from site_key to list of record_ids.
        _conflict_log: Running log of detected conflict descriptions.

    Example::

        analyzer = LayerCoverageAnalyzer()
        rec = analyzer.register_layer(
            ConcurrencyLayer.THREAD, "site:auth", frozenset(["token"])
        )
        level = analyzer.analyze_coverage(ConcurrencyLayer.THREAD, [rec])
    """

    _records: dict[str, ConcurrencyRecord] = field(default_factory=dict)
    _layer_index: dict[str, list[str]] = field(default_factory=dict)
    _site_index: dict[str, list[str]] = field(default_factory=dict)
    _conflict_log: list[dict[str, object]] = field(default_factory=list)

    def register_layer(
        self,
        layer: ConcurrencyLayer,
        site_key: str,
        binding_keys: frozenset[str],
        metadata: tuple[str, ...] = (),
    ) -> ConcurrencyRecord:
        """Register a new concurrency layer instance for a site.

        Creates a ConcurrencyRecord, stores it in the registry, and updates
        the layer and site indices.

        Args:
            layer: The concurrency primitive for this instance.
            site_key: The shared site this instance observes.
            binding_keys: Keys visible to this concurrency unit.
            metadata: Optional string annotations.

        Returns:
            The newly created ConcurrencyRecord.

        Raises:
            ValueError: If *site_key* is empty.

        Example::

            rec = analyzer.register_layer(
                ConcurrencyLayer.COROUTINE, "site:db", frozenset(["conn"])
            )
        """
        if not site_key:
            raise ValueError("site_key must be a non-empty string")

        # Determine coverage relative to all existing records for this site.
        existing = [
            self._records[rid]
            for rid in self._site_index.get(site_key, [])
        ]
        coverage = self.analyze_coverage(layer, existing) if existing else CoverageLevel.FULL

        record = ConcurrencyRecord(
            record_id=_new_record_id(),
            layer=layer,
            coverage_level=coverage,
            site_key=site_key,
            binding_keys=frozenset(binding_keys),
            created_at=time.monotonic(),
            metadata=tuple(metadata),
        )
        self._records[record.record_id] = record
        self._layer_index.setdefault(layer.value, []).append(record.record_id)
        self._site_index.setdefault(site_key, []).append(record.record_id)
        _log.debug("Registered layer %s for site %s → record %s", layer.value, site_key, record.record_id)
        return record

    def analyze_coverage(
        self,
        layer: ConcurrencyLayer,
        records: list[ConcurrencyRecord],
    ) -> CoverageLevel:
        """Determine the coverage level of *layer* relative to existing records.

        Aggregates the union of binding_keys already registered for the site
        and computes the Jaccard similarity between that union and each record
        for the specified *layer*.

        Args:
            layer: The layer whose coverage is being assessed.
            records: Existing records for the same site.

        Returns:
            A CoverageLevel value.

        Example::

            lvl = analyzer.analyze_coverage(ConcurrencyLayer.THREAD, records)
        """
        if not records:
            return CoverageLevel.FULL

        layer_records = [r for r in records if r.layer == layer]
        if not layer_records:
            # No prior records for this layer; compare against all others.
            layer_records = records

        all_keys: frozenset[str] = frozenset()
        for r in records:
            all_keys = all_keys | r.binding_keys

        best_score = 0.0
        for r in layer_records:
            score = _jaccard(r.binding_keys, all_keys)
            if score > best_score:
                best_score = score

        return _coverage_from_jaccard(best_score)

    def compute_overlap(
        self,
        records_a: list[ConcurrencyRecord],
        records_b: list[ConcurrencyRecord],
    ) -> frozenset[str]:
        """Compute the binding key overlap between two groups of records.

        Args:
            records_a: First group of concurrency records.
            records_b: Second group of concurrency records.

        Returns:
            Frozenset of binding keys present in both groups.

        Example::

            overlap = analyzer.compute_overlap(group_a, group_b)
        """
        keys_a: frozenset[str] = frozenset()
        for r in records_a:
            keys_a = keys_a | r.binding_keys

        keys_b: frozenset[str] = frozenset()
        for r in records_b:
            keys_b = keys_b | r.binding_keys

        return keys_a & keys_b

    def detect_conflicts(self, records: list[ConcurrencyRecord]) -> list[dict[str, object]]:
        """Detect binding-key conflicts among the supplied records.

        Two records conflict when they share at least one binding key but
        belong to different concurrency layers — a potential data race.

        Args:
            records: Records to analyse for conflicts.

        Returns:
            List of conflict descriptor dicts, each with keys:
            ``record_id_a``, ``record_id_b``, ``shared_keys``, ``layers``.

        Example::

            conflicts = analyzer.detect_conflicts(all_records)
        """
        conflicts: list[dict[str, object]] = []
        seen: set[tuple[str, str]] = set()

        for i, ra in enumerate(records):
            for rb in records[i + 1:]:
                pair = tuple(sorted((ra.record_id, rb.record_id)))
                if pair in seen:
                    continue
                seen.add(pair)  # type: ignore[arg-type]

                if ra.layer == rb.layer:
                    continue  # same layer; not a cross-layer conflict

                shared = ra.binding_keys & rb.binding_keys
                if shared:
                    conflict: dict[str, object] = {
                        "record_id_a": ra.record_id,
                        "record_id_b": rb.record_id,
                        "shared_keys": sorted(shared),
                        "layers": (ra.layer.value, rb.layer.value),
                        "site_key": ra.site_key,
                        "severity": len(shared),
                    }
                    conflicts.append(conflict)
                    self._conflict_log.append(conflict)

        _log.debug("detect_conflicts found %d conflicts", len(conflicts))
        return conflicts

    def coverage_matrix(self, records: list[ConcurrencyRecord]) -> dict[str, dict[str, str]]:
        """Build a pairwise coverage-level matrix for the supplied records.

        Args:
            records: Records to include in the matrix.

        Returns:
            Nested dict ``{record_id_a: {record_id_b: coverage_level_value}}``.

        Example::

            matrix = analyzer.coverage_matrix(records)
        """
        result: dict[str, dict[str, str]] = {}
        for ra in records:
            row: dict[str, str] = {}
            for rb in records:
                if ra.record_id == rb.record_id:
                    row[rb.record_id] = CoverageLevel.FULL.value
                else:
                    score = _jaccard(ra.binding_keys, rb.binding_keys)
                    row[rb.record_id] = _coverage_from_jaccard(score).value
            result[ra.record_id] = row
        return result

    def layer_binding_intersection(
        self,
        layer_a: ConcurrencyLayer,
        layer_b: ConcurrencyLayer,
        records: list[ConcurrencyRecord],
    ) -> frozenset[str]:
        """Return the binding keys shared between all records of two layers.

        Args:
            layer_a: First concurrency layer.
            layer_b: Second concurrency layer.
            records: Pool of records to inspect.

        Returns:
            Frozenset of binding keys present in both layer's records.

        Example::

            keys = analyzer.layer_binding_intersection(
                ConcurrencyLayer.THREAD, ConcurrencyLayer.COROUTINE, records
            )
        """
        a_keys: frozenset[str] = frozenset()
        b_keys: frozenset[str] = frozenset()
        for r in records:
            if r.layer == layer_a:
                a_keys = a_keys | r.binding_keys
            elif r.layer == layer_b:
                b_keys = b_keys | r.binding_keys
        return a_keys & b_keys

    def isolation_score(self, records: list[ConcurrencyRecord]) -> float:
        """Compute a scalar isolation score for a set of records.

        A score of 1.0 means every pair of records from different layers is
        fully disjoint in binding keys (perfect isolation).  A score of 0.0
        means complete overlap across all pairs.

        Args:
            records: Records to score.

        Returns:
            Float in [0.0, 1.0].

        Example::

            score = analyzer.isolation_score(records)
            print(f"Isolation: {score:.2%}")
        """
        cross_layer_pairs: list[tuple[ConcurrencyRecord, ConcurrencyRecord]] = []
        for i, ra in enumerate(records):
            for rb in records[i + 1:]:
                if ra.layer != rb.layer:
                    cross_layer_pairs.append((ra, rb))

        if not cross_layer_pairs:
            return 1.0

        total_disjoint = sum(
            1 for ra, rb in cross_layer_pairs if not (ra.binding_keys & rb.binding_keys)
        )
        return total_disjoint / len(cross_layer_pairs)

    def export_records(self) -> list[dict[str, object]]:
        """Export all registered records as a list of plain dicts.

        Returns:
            List of serialised ConcurrencyRecord dicts.
        """
        return [r.to_dict() for r in self._records.values()]

    def stats(self) -> dict[str, object]:
        """Return summary statistics for the analyzer's current state.

        Returns:
            Dict with keys: ``total_records``, ``layers``, ``sites``,
            ``total_conflicts``, ``avg_binding_keys``.
        """
        all_rec = list(self._records.values())
        avg_keys = (
            sum(len(r.binding_keys) for r in all_rec) / len(all_rec)
            if all_rec
            else 0.0
        )
        return {
            "total_records": len(all_rec),
            "layers": {k: len(v) for k, v in self._layer_index.items()},
            "sites": list(self._site_index.keys()),
            "total_conflicts": len(self._conflict_log),
            "avg_binding_keys": round(avg_keys, 3),
        }


# ---------------------------------------------------------------------------
# Witness
# ---------------------------------------------------------------------------

@dataclass
class PhenomenonWitness:
    """Witnesses concurrency phenomena as evidence in the sheaf.

    Collects observations, certifies conflicts, and produces exportable
    evidence bundles for downstream sheaf-morphism verification.

    Attributes:
        _observations: Observation records keyed by observation_id.
        _layer_obs_index: Maps layer value → list of observation_ids.
        _conflict_obs: Conflict observation records keyed by conflict_id.
        _obs_counter: Monotonic counter for observation ordering.

    Example::

        witness = PhenomenonWitness()
        obs_id = witness.observe(record)
    """

    _observations: dict[str, dict[str, object]] = field(default_factory=dict)
    _layer_obs_index: dict[str, list[str]] = field(default_factory=dict)
    _conflict_obs: dict[str, dict[str, object]] = field(default_factory=dict)
    _obs_counter: int = field(default=0)

    def observe(self, record: ConcurrencyRecord) -> str:
        """Record an observation of a ConcurrencyRecord.

        Args:
            record: The record to observe.

        Returns:
            The observation_id for this witness entry.

        Example::

            oid = witness.observe(rec)
        """
        obs_id = f"obs_{_new_record_id()}"
        self._obs_counter += 1
        observation: dict[str, object] = {
            "observation_id": obs_id,
            "record_id": record.record_id,
            "layer": record.layer.value,
            "coverage_level": record.coverage_level.value,
            "site_key": record.site_key,
            "binding_keys": sorted(record.binding_keys),
            "observed_at": time.monotonic(),
            "seq": self._obs_counter,
        }
        self._observations[obs_id] = observation
        self._layer_obs_index.setdefault(record.layer.value, []).append(obs_id)
        _log.debug("Witness observed record %s → obs %s", record.record_id, obs_id)
        return obs_id

    def witness_conflict(
        self,
        record_id_a: str,
        record_id_b: str,
        binding_key: str,
    ) -> str:
        """Create a conflict witness entry for two records sharing a binding key.

        Args:
            record_id_a: First record's identifier.
            record_id_b: Second record's identifier.
            binding_key: The shared binding key causing the conflict.

        Returns:
            The conflict_id for this witness entry.

        Raises:
            ValueError: If *binding_key* is empty.

        Example::

            cid = witness.witness_conflict("abc", "def", "token")
        """
        if not binding_key:
            raise ValueError("binding_key must not be empty")
        conflict_id = f"cfl_{_new_record_id()}"
        entry: dict[str, object] = {
            "conflict_id": conflict_id,
            "record_id_a": record_id_a,
            "record_id_b": record_id_b,
            "binding_key": binding_key,
            "witnessed_at": time.monotonic(),
        }
        self._conflict_obs[conflict_id] = entry
        _log.warning("Witness recorded conflict %s on key '%s'", conflict_id, binding_key)
        return conflict_id

    def get_observations_for_layer(
        self, layer: ConcurrencyLayer
    ) -> list[dict[str, object]]:
        """Return all observations for a given concurrency layer.

        Args:
            layer: The layer to filter by.

        Returns:
            List of observation dicts for *layer*.

        Example::

            obs = witness.get_observations_for_layer(ConcurrencyLayer.PROCESS)
        """
        ids = self._layer_obs_index.get(layer.value, [])
        return [self._observations[i] for i in ids if i in self._observations]

    def conflict_count(self) -> int:
        """Return the total number of witnessed conflicts.

        Returns:
            Integer count of conflict witness entries.
        """
        return len(self._conflict_obs)

    def coverage_summary(self) -> dict[str, int]:
        """Summarise observation counts by CoverageLevel.

        Returns:
            Dict mapping coverage level name to count.

        Example::

            summary = witness.coverage_summary()
        """
        summary: dict[str, int] = {}
        for obs in self._observations.values():
            lvl = str(obs.get("coverage_level", "UNKNOWN"))
            summary[lvl] = summary.get(lvl, 0) + 1
        return summary

    def generate_witness_certificate(self) -> dict[str, object]:
        """Generate a signed-style certificate bundle summarising all evidence.

        Returns:
            Dict with keys: ``cert_id``, ``total_observations``,
            ``total_conflicts``, ``coverage_summary``, ``layer_counts``,
            ``issued_at``, ``fingerprint``.

        Example::

            cert = witness.generate_witness_certificate()
        """
        layer_counts = {k: len(v) for k, v in self._layer_obs_index.items()}
        payload: dict[str, object] = {
            "cert_id": f"cert_{_new_record_id()}",
            "total_observations": len(self._observations),
            "total_conflicts": len(self._conflict_obs),
            "coverage_summary": self.coverage_summary(),
            "layer_counts": layer_counts,
            "issued_at": time.monotonic(),
        }
        payload["fingerprint"] = _fingerprint(payload)
        return payload

    def export_evidence(self) -> list[dict[str, object]]:
        """Export all observation and conflict evidence as a flat list.

        Returns:
            Combined list of observation and conflict dicts, each tagged with
            a ``kind`` field (``"observation"`` or ``"conflict"``).
        """
        evidence: list[dict[str, object]] = []
        for obs in self._observations.values():
            evidence.append({"kind": "observation", **obs})
        for cfl in self._conflict_obs.values():
            evidence.append({"kind": "conflict", **cfl})
        return evidence


# ---------------------------------------------------------------------------
# Coordinator
# ---------------------------------------------------------------------------

@dataclass
class ConcurrencyPythonOnePhenomenonCoordinator:
    """Orchestrates concurrency-layer registration, analysis, and witnessing.

    Combines a LayerCoverageAnalyzer and a PhenomenonWitness to provide a
    single entry point for the full Coordinator-Analyzer-Witness workflow.

    Attributes:
        _analyzer: The underlying LayerCoverageAnalyzer.
        _witness: The underlying PhenomenonWitness.
        _session_id: Unique identifier for this coordinator session.

    Example::

        coordinator = ConcurrencyPythonOnePhenomenonCoordinator()
        result = coordinator.add_layer_instance(
            ConcurrencyLayer.THREAD, "site:auth", frozenset(["token"])
        )
    """

    _analyzer: LayerCoverageAnalyzer = field(default_factory=LayerCoverageAnalyzer)
    _witness: PhenomenonWitness = field(default_factory=PhenomenonWitness)
    _session_id: str = field(default_factory=lambda: _new_record_id())

    def add_layer_instance(
        self,
        layer: ConcurrencyLayer,
        site_key: str,
        binding_keys: frozenset[str],
        metadata: tuple[str, ...] = (),
    ) -> dict[str, object]:
        """Register a new concurrency layer instance and witness it.

        Args:
            layer: The concurrency primitive.
            site_key: The shared site key.
            binding_keys: Binding keys observable by this unit.
            metadata: Optional annotations.

        Returns:
            Dict with keys ``record_id``, ``layer``, ``coverage_level``,
            ``observation_id``.

        Example::

            r = coordinator.add_layer_instance(
                ConcurrencyLayer.COROUTINE, "site:cache", frozenset(["key"])
            )
        """
        record = self._analyzer.register_layer(layer, site_key, binding_keys, metadata)
        obs_id = self._witness.observe(record)
        _log.info("Coordinator: added %s layer for site '%s'", layer.value, site_key)
        return {
            "record_id": record.record_id,
            "layer": record.layer.value,
            "coverage_level": record.coverage_level.value,
            "observation_id": obs_id,
        }

    def analyze_all_layers(self) -> dict[str, object]:
        """Run coverage analysis across all registered records, grouped by layer.

        Returns:
            Dict mapping layer name → list of coverage levels observed for
            that layer's records.

        Example::

            analysis = coordinator.analyze_all_layers()
        """
        all_records = list(self._analyzer._records.values())
        result: dict[str, object] = {}
        for layer in ConcurrencyLayer:
            layer_records = [r for r in all_records if r.layer == layer]
            if not layer_records:
                continue
            others = [r for r in all_records if r.layer != layer]
            coverages = [
                self._analyzer.analyze_coverage(layer, others).value
                for _ in layer_records
            ]
            result[layer.value] = {
                "count": len(layer_records),
                "coverages": coverages,
                "isolation_score": round(self._analyzer.isolation_score(layer_records + others), 4),
            }
        return result

    def detect_all_conflicts(self) -> list[dict[str, object]]:
        """Detect conflicts across all registered records and witness them.

        Returns:
            List of conflict dicts, each enriched with a ``conflict_witness_id``.

        Example::

            conflicts = coordinator.detect_all_conflicts()
        """
        all_records = list(self._analyzer._records.values())
        conflicts = self._analyzer.detect_conflicts(all_records)
        enriched: list[dict[str, object]] = []
        for conflict in conflicts:
            shared = conflict.get("shared_keys", [])
            first_key = shared[0] if shared else ""  # type: ignore[index]
            witness_id = self._witness.witness_conflict(
                str(conflict["record_id_a"]),
                str(conflict["record_id_b"]),
                first_key,
            )
            enriched.append({**conflict, "conflict_witness_id": witness_id})
        return enriched

    def coverage_report(self) -> dict[str, object]:
        """Generate a site-level coverage report.

        Returns:
            Dict mapping site_key → coverage matrix and isolation score.

        Example::

            report = coordinator.coverage_report()
        """
        all_records = list(self._analyzer._records.values())
        sites: dict[str, list[ConcurrencyRecord]] = {}
        for r in all_records:
            sites.setdefault(r.site_key, []).append(r)

        report: dict[str, object] = {}
        for site, records in sites.items():
            matrix = self._analyzer.coverage_matrix(records)
            score = self._analyzer.isolation_score(records)
            report[site] = {
                "record_count": len(records),
                "layers": list({r.layer.value for r in records}),
                "isolation_score": round(score, 4),
                "coverage_matrix": matrix,
            }
        return report

    def full_report(self) -> dict[str, object]:
        """Produce a comprehensive report combining all sub-reports.

        Returns:
            Dict with keys: ``session_id``, ``analyzer_stats``,
            ``layer_analysis``, ``coverage_report``, ``conflicts``,
            ``witness_certificate``.

        Example::

            report = coordinator.full_report()
        """
        return {
            "session_id": self._session_id,
            "analyzer_stats": self._analyzer.stats(),
            "layer_analysis": self.analyze_all_layers(),
            "coverage_report": self.coverage_report(),
            "conflicts": self.detect_all_conflicts(),
            "witness_certificate": self._witness.generate_witness_certificate(),
        }

    def reset(self) -> None:
        """Clear all state in the coordinator, analyzer, and witness.

        Example::

            coordinator.reset()
        """
        self._analyzer = LayerCoverageAnalyzer()
        self._witness = PhenomenonWitness()
        self._session_id = _new_record_id()
        _log.info("Coordinator reset; new session_id=%s", self._session_id)


# ---------------------------------------------------------------------------
# Module-level helpers for quick usage
# ---------------------------------------------------------------------------

def make_coordinator() -> ConcurrencyPythonOnePhenomenonCoordinator:
    """Convenience factory that returns a ready-to-use coordinator instance.

    Returns:
        A freshly constructed ConcurrencyPythonOnePhenomenonCoordinator.

    Example::

        c = make_coordinator()
        c.add_layer_instance(ConcurrencyLayer.THREAD, "site:x", frozenset(["k"]))
    """
    return ConcurrencyPythonOnePhenomenonCoordinator()


def layer_names() -> list[str]:
    """Return the names of all defined ConcurrencyLayer values.

    Returns:
        Sorted list of layer name strings.

    Example::

        print(layer_names())
    """
    return sorted(layer.value for layer in ConcurrencyLayer)


def coverage_level_names() -> list[str]:
    """Return the names of all defined CoverageLevel values.

    Returns:
        Sorted list of coverage level name strings.
    """
    return sorted(lvl.value for lvl in CoverageLevel)


def validate_binding_keys(keys: object) -> frozenset[str]:
    """Validate and normalise a collection of binding keys.

    Args:
        keys: Any iterable of strings.

    Returns:
        Frozenset of non-empty, stripped key strings.

    Raises:
        TypeError: If *keys* is not iterable.
        ValueError: If any element is not a string or is blank after stripping.

    Example::

        fset = validate_binding_keys(["token", "user"])
    """
    if not hasattr(keys, "__iter__"):
        raise TypeError(f"binding_keys must be iterable, got {type(keys)!r}")
    result: set[str] = set()
    for k in keys:  # type: ignore[union-attr]
        if not isinstance(k, str):
            raise ValueError(f"Each binding key must be a str, got {type(k)!r}")
        stripped = k.strip()
        if not stripped:
            raise ValueError("Binding keys must not be blank after stripping")
        result.add(stripped)
    return frozenset(result)


def summarise_records(records: list[ConcurrencyRecord]) -> dict[str, object]:
    """Produce a human-readable summary of a list of ConcurrencyRecords.

    Args:
        records: Records to summarise.

    Returns:
        Dict with ``count``, ``layers``, ``sites``, ``coverage_levels``,
        ``total_binding_keys``.

    Example::

        summary = summarise_records(records)
    """
    layers: set[str] = set()
    sites: set[str] = set()
    coverages: set[str] = set()
    total_keys = 0
    for r in records:
        layers.add(r.layer.value)
        sites.add(r.site_key)
        coverages.add(r.coverage_level.value)
        total_keys += len(r.binding_keys)
    return {
        "count": len(records),
        "layers": sorted(layers),
        "sites": sorted(sites),
        "coverage_levels": sorted(coverages),
        "total_binding_keys": total_keys,
    }


__all__ = [
    "ConcurrencyLayer",
    "CoverageLevel",
    "ConcurrencyRecord",
    "LayerCoverageAnalyzer",
    "PhenomenonWitness",
    "ConcurrencyPythonOnePhenomenonCoordinator",
    "make_coordinator",
    "layer_names",
    "coverage_level_names",
    "validate_binding_keys",
    "summarise_records",
]

# copilot: s01 — concurrency in Python is not one phenomenon; Ch24 §1
