from __future__ import annotations

# ── Semantic Apertures in the Python World ────────────────────────────────────
# Ch23 §1  — live-mutation sub-system
# Tracks, classifies, witnesses and seals semantic apertures (dynamic-binding
# points) observed inside a running Python namespace.  An "aperture" is any
# site where the interpreter's name-resolution machinery can be influenced at
# runtime: exec/eval injection points, monkey-patched attributes, lazily-bound
# closures, dynamically imported modules, etc.
# ─────────────────────────────────────────────────────────────────────────────

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
    from jugeo.sheaf import SheafNode  # type: ignore
except ImportError:
    class SheafNode:  # type: ignore
        """Inline stub for SheafNode.

        Used when the full jugeo.sheaf package is not installed.  Provides just
        enough surface area for type-checking and runtime attribute access.
        """

        def __init__(self, node_id: str = "") -> None:
            self.node_id = node_id

        def __repr__(self) -> str:  # pragma: no cover
            return f"SheafNode(node_id={self.node_id!r})"

try:
    from jugeo.diagnostics import DiagnosticsCollector  # type: ignore
except ImportError:
    class DiagnosticsCollector:  # type: ignore
        """Inline stub for DiagnosticsCollector.

        Provides a no-op ``collect`` implementation so callers do not have to
        guard every call site against an absent diagnostics subsystem.
        """

        def collect(self, data: dict[str, Any]) -> None:
            """Accept diagnostic payload and silently discard it."""
            ...

_log = logging.getLogger(__name__)

# ── module-level constants ────────────────────────────────────────────────────

_LATE_BINDING_PATTERN: re.Pattern[str] = re.compile(
    r"(?:lazy|deferred|late|pending|unresolved|proxy|stub|placeholder)",
    re.IGNORECASE,
)
_EXEC_EVAL_PATTERN: re.Pattern[str] = re.compile(
    r"(?:exec|eval|compile|code_obj|codeobj|bytecode)",
    re.IGNORECASE,
)
_DYNAMIC_IMPORT_PATTERN: re.Pattern[str] = re.compile(
    r"(?:import|loader|finder|module|plugin|extension|addon)",
    re.IGNORECASE,
)
_DUNDER_PATTERN: re.Pattern[str] = re.compile(r"^__\w+__$")

_ENTROPY_BASE: float = 2.0  # bits
_MAX_TIMELINE_LEN: int = 4096
_SEAL_SENTINEL: str = "SEALED_BY_ANALYZER"

# ── helpers ───────────────────────────────────────────────────────────────────


def _new_aperture_id() -> str:
    """Generate a short, unique aperture identifier.

    Returns:
        A 12-character hex string derived from a random UUID4.

    Example:
        >>> aid = _new_aperture_id()
        >>> len(aid)
        12
    """
    return uuid.uuid4().hex[:12]


def _new_witness_id() -> str:
    """Generate a short, unique witness/observation identifier.

    Returns:
        A 12-character hex string derived from a random UUID4.

    Example:
        >>> wid = _new_witness_id()
        >>> len(wid)
        12
    """
    return uuid.uuid4().hex[:12]


def _hash_str(s: str) -> str:
    """Return the first 16 hex characters of SHA-256(s).

    Args:
        s: The input string to hash.

    Returns:
        A 16-character lowercase hex digest suitable for use as a short
        content fingerprint.

    Example:
        >>> _hash_str("hello")
        'aaf4c61ddcc5e8a2'  # illustrative; real value differs
    """
    return hashlib.sha256(s.encode()).hexdigest()[:16]


def _safe_json_dumps(obj: Any) -> str:
    """Serialize *obj* to a JSON string, falling back to ``repr`` on error.

    Args:
        obj: Any Python object.

    Returns:
        A JSON string, or a repr-wrapped string if serialisation fails.
    """
    try:
        return json.dumps(obj, default=str, sort_keys=True)
    except (TypeError, ValueError) as exc:
        _log.debug("_safe_json_dumps fallback for %r: %s", type(obj), exc)
        return json.dumps(repr(obj))


def _entropy_of_distribution(counts: list[int]) -> float:
    """Compute Shannon entropy (in bits) of a probability distribution.

    Args:
        counts: Non-negative integer counts for each category.  Zero-count
            categories are ignored (they contribute 0 to entropy).

    Returns:
        Shannon entropy H = -Σ p_i · log2(p_i) in bits.  Returns 0.0 if
        the total count is zero or only one non-zero category exists.

    Example:
        >>> _entropy_of_distribution([1, 1, 1, 1])
        2.0
        >>> _entropy_of_distribution([4])
        0.0
    """
    total = sum(counts)
    if total == 0:
        return 0.0
    entropy = 0.0
    for c in counts:
        if c > 0:
            p = c / total
            entropy -= p * math.log(p, _ENTROPY_BASE)
    return entropy


def _truncate_type_name(obj: object) -> str:
    """Return a concise type name for *obj*, truncated to 64 characters.

    Args:
        obj: Any Python object.

    Returns:
        The qualified type name as a string, at most 64 characters long.
    """
    raw = type(obj).__qualname__
    return raw[:64] if len(raw) > 64 else raw


# ── enumerations ──────────────────────────────────────────────────────────────


class ApertureKind(str, Enum):
    """Classification of a semantic aperture's injection mechanism.

    Each value names the primary Python runtime feature that makes this
    binding site dynamic.

    Attributes:
        EXEC_INJECTION: The aperture is an ``exec``/``eval`` call site or a
            compiled code-object binding.
        EVAL_QUERY: The aperture is specifically an ``eval``-style read-only
            query into the namespace.
        MONKEY_PATCH: The aperture is an attribute or descriptor assignment on
            an existing class or instance (structural mutation).
        DYNAMIC_IMPORT: The aperture is produced by ``importlib`` machinery,
            plugin loaders, or ``__import__`` overrides.
        LATE_BINDING: The aperture is a lazily-resolved closure or proxy whose
            final value is not yet determined at analysis time.
    """

    EXEC_INJECTION = "EXEC_INJECTION"
    EVAL_QUERY = "EVAL_QUERY"
    MONKEY_PATCH = "MONKEY_PATCH"
    DYNAMIC_IMPORT = "DYNAMIC_IMPORT"
    LATE_BINDING = "LATE_BINDING"


class ApertureState(str, Enum):
    """Lifecycle state of a single semantic aperture.

    Attributes:
        OPEN: The aperture is fully active and mutable.
        SEALED: The aperture has been closed and is no longer mutable.
        PARTIALLY_SEALED: The aperture has been partially restricted but some
            mutation pathways remain available.
        INVALIDATED: The aperture was found to be inconsistent or stale and
            should no longer be trusted.
    """

    OPEN = "OPEN"
    SEALED = "SEALED"
    PARTIALLY_SEALED = "PARTIALLY_SEALED"
    INVALIDATED = "INVALIDATED"


# ── value-type dataclasses ────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class SemanticApertureRecord:
    """Immutable record describing one semantic aperture.

    A *semantic aperture* is a specific binding site in a Python namespace
    that exposes the interpreter's dynamic name-resolution machinery.  Each
    record captures a snapshot of that site at the moment of discovery.

    Attributes:
        record_id: Unique identifier for this record (12-hex chars).
        kind: Classification of the aperture's injection mechanism.
        state: Current lifecycle state.
        namespace_key: The name under which this aperture appears in its
            containing namespace dict.
        value_type_name: The ``__qualname__`` of the bound value's type.
        created_at: POSIX timestamp of when this record was created.
        sealed_at: POSIX timestamp of when the aperture was sealed, or
            ``None`` if it has not been sealed.
        metadata_json: JSON-encoded auxiliary metadata string.

    Example:
        >>> r = SemanticApertureRecord(
        ...     record_id="abc123def456",
        ...     kind=ApertureKind.LATE_BINDING,
        ...     state=ApertureState.OPEN,
        ...     namespace_key="lazy_loader",
        ...     value_type_name="function",
        ...     created_at=time.time(),
        ...     sealed_at=None,
        ...     metadata_json="{}",
        ... )
        >>> r.is_active()
        True
    """

    record_id: str
    kind: ApertureKind
    state: ApertureState
    namespace_key: str
    value_type_name: str
    created_at: float
    sealed_at: float | None
    metadata_json: str = field(default="{}")

    # ------------------------------------------------------------------
    # computed properties / helper methods
    # ------------------------------------------------------------------

    def kind_label(self) -> str:
        """Return a composite label combining kind and namespace key.

        Returns:
            A string of the form ``"<KIND>:<namespace_key>"``.

        Example:
            >>> r.kind_label()
            'LATE_BINDING:lazy_loader'
        """
        return f"{self.kind.value}:{self.namespace_key}"

    def age(self) -> float:
        """Return the age of this record in seconds since creation.

        Returns:
            Elapsed seconds as a float; always non-negative.

        Example:
            >>> r.age() >= 0
            True
        """
        return time.time() - self.created_at

    def is_active(self) -> bool:
        """Return ``True`` if the aperture is still operational.

        An aperture is considered active when its state is either
        :attr:`ApertureState.OPEN` or :attr:`ApertureState.PARTIALLY_SEALED`.

        Returns:
            Boolean activity flag.

        Example:
            >>> r.is_active()
            True
        """
        return self.state in (ApertureState.OPEN, ApertureState.PARTIALLY_SEALED)

    def to_dict(self) -> dict[str, Any]:
        """Serialise this record to a plain Python dictionary.

        Returns:
            A JSON-compatible dictionary with all field values, plus a
            ``"kind_label"`` convenience key and a computed ``"age_s"``
            entry for the current age in seconds.

        Example:
            >>> d = r.to_dict()
            >>> "record_id" in d
            True
        """
        return {
            "record_id": self.record_id,
            "kind": self.kind.value,
            "state": self.state.value,
            "namespace_key": self.namespace_key,
            "value_type_name": self.value_type_name,
            "created_at": self.created_at,
            "sealed_at": self.sealed_at,
            "metadata_json": self.metadata_json,
            "kind_label": self.kind_label(),
            "age_s": round(self.age(), 6),
            "is_active": self.is_active(),
        }


@dataclass(frozen=True, slots=True)
class ApertureIndexEntry:
    """Immutable index entry associating a computed integer score with a record.

    Index entries are created by :class:`SemanticApertureAnalyzer` during
    index-building and serve as fast lookup handles.

    Attributes:
        entry_id: Unique identifier for this index entry.
        record_id: The :attr:`SemanticApertureRecord.record_id` this entry
            refers to.
        index_key: The dimension/axis along which the score was computed
            (e.g., ``"kind_rank"``, ``"age_bucket"``).
        index_value: The integer score for this entry in the given dimension.
        computed_at: POSIX timestamp when this index entry was computed.

    Example:
        >>> e = ApertureIndexEntry(
        ...     entry_id="e001",
        ...     record_id="abc123def456",
        ...     index_key="kind_rank",
        ...     index_value=3,
        ...     computed_at=time.time(),
        ... )
        >>> e.label()
        'kind_rank[3] → abc123def456'
    """

    entry_id: str
    record_id: str
    index_key: str
    index_value: int
    computed_at: float

    def label(self) -> str:
        """Return a human-readable label for this index entry.

        Returns:
            A string of the form ``"<index_key>[<index_value>] → <record_id>"``.

        Example:
            >>> e.label()
            'kind_rank[3] → abc123def456'
        """
        return f"{self.index_key}[{self.index_value}] \u2192 {self.record_id}"


@dataclass(frozen=True, slots=True)
class ApertureObservation:
    """Immutable record of a single witness observation of an aperture.

    Created by :class:`ApertureWitness` each time an aperture is presented
    for witnessing.

    Attributes:
        obs_id: Unique identifier for this observation (12-hex chars).
        record_id: The aperture record that was observed.
        observed_at: POSIX timestamp of the observation.
        observer: A short human- or machine-readable identifier for the
            entity that made the observation (e.g., a function name or
            session tag).
        note: Free-text annotation attached at observation time.

    Example:
        >>> obs = ApertureObservation(
        ...     obs_id=_new_witness_id(),
        ...     record_id="abc123def456",
        ...     observed_at=time.time(),
        ...     observer="test_scan",
        ...     note="first observation",
        ... )
        >>> obs.age() >= 0
        True
    """

    obs_id: str
    record_id: str
    observed_at: float
    observer: str
    note: str

    def age(self) -> float:
        """Return elapsed seconds since this observation was made.

        Returns:
            A non-negative float representing the observation's age.

        Example:
            >>> obs.age() >= 0
            True
        """
        return time.time() - self.observed_at

    def to_dict(self) -> dict[str, Any]:
        """Serialise this observation to a plain dictionary.

        Returns:
            A JSON-compatible dictionary containing all fields plus the
            computed ``"age_s"`` key.

        Example:
            >>> d = obs.to_dict()
            >>> "obs_id" in d and "record_id" in d
            True
        """
        return {
            "obs_id": self.obs_id,
            "record_id": self.record_id,
            "observed_at": self.observed_at,
            "observer": self.observer,
            "note": self.note,
            "age_s": round(self.age(), 6),
        }


# ── mutable manager classes ────────────────────────────────────────────────────


@dataclass
class SemanticApertureAnalyzer:
    """Scans Python namespaces for semantic apertures and manages their state.

    The analyzer is the primary engine of the *Semantic Apertures* subsystem.
    It accepts raw namespace dictionaries, classifies each name into an
    :class:`ApertureKind`, builds :class:`SemanticApertureRecord` objects,
    maintains an index, and supports sealing apertures.

    Attributes:
        _records: Ordered list of all discovered records.
        _index: Mapping from entry_id to :class:`ApertureIndexEntry`.
        _sealed_ids: Set of record_ids that have been sealed.

    Example:
        >>> analyzer = SemanticApertureAnalyzer()
        >>> records = analyzer.scan_namespace({"lazy_fn": lambda: None})
        >>> len(records) >= 1
        True
    """

    _records: list[SemanticApertureRecord] = field(default_factory=list)
    _index: dict[str, ApertureIndexEntry] = field(default_factory=dict)
    _sealed_ids: set[str] = field(default_factory=set)

    # ------------------------------------------------------------------
    # scanning
    # ------------------------------------------------------------------

    def scan_namespace(
        self, namespace: dict[str, Any]
    ) -> list[SemanticApertureRecord]:
        """Scan a namespace dictionary and create records for each aperture.

        Iterates over every key/value pair in *namespace*, skips dunder
        names, classifies each value via :meth:`classify_aperture`, and
        constructs a :class:`SemanticApertureRecord` for each site.  All
        new records are appended to :attr:`_records`.

        Args:
            namespace: A Python namespace dictionary, such as the result of
                ``vars(module)`` or ``locals()``.

        Returns:
            A list of newly created :class:`SemanticApertureRecord` objects
            (only those created in this call, not all historical records).

        Raises:
            TypeError: If *namespace* is not a dict.

        Example:
            >>> analyzer = SemanticApertureAnalyzer()
            >>> ns = {"plugin_loader": lambda m: None, "_private": 42}
            >>> new_recs = analyzer.scan_namespace(ns)
            >>> all(r.state == ApertureState.OPEN for r in new_recs)
            True
        """
        if not isinstance(namespace, dict):
            raise TypeError(
                f"scan_namespace expects dict, got {type(namespace).__name__!r}"
            )
        new_records: list[SemanticApertureRecord] = []
        now = time.time()
        _log.debug("scan_namespace: scanning %d keys", len(namespace))
        for key, value in namespace.items():
            if _DUNDER_PATTERN.match(str(key)):
                _log.debug("scan_namespace: skipping dunder key %r", key)
                continue
            kind = self.classify_aperture(key, value)
            record_id = _new_aperture_id()
            type_name = _truncate_type_name(value)
            metadata = {
                "callable": callable(value),
                "value_repr": repr(value)[:120],
                "key_hash": _hash_str(str(key)),
            }
            record = SemanticApertureRecord(
                record_id=record_id,
                kind=kind,
                state=ApertureState.OPEN,
                namespace_key=str(key),
                value_type_name=type_name,
                created_at=now,
                sealed_at=None,
                metadata_json=_safe_json_dumps(metadata),
            )
            new_records.append(record)
            self._records.append(record)
            _log.debug(
                "scan_namespace: created record %s kind=%s key=%r",
                record_id,
                kind.value,
                key,
            )
        self._build_index()
        _log.info(
            "scan_namespace: discovered %d new apertures (total=%d)",
            len(new_records),
            len(self._records),
        )
        return new_records

    # ------------------------------------------------------------------
    # classification
    # ------------------------------------------------------------------

    def classify_aperture(self, key: str, value: object) -> ApertureKind:
        """Classify a namespace entry into an :class:`ApertureKind`.

        Uses a priority-ordered set of heuristics:

        1. If the key matches ``exec``/``eval``/``compile`` patterns →
           :attr:`ApertureKind.EXEC_INJECTION` (or ``EVAL_QUERY`` when the
           key contains the word "eval" but not "exec").
        2. If the value is itself a ``type`` object (i.e. a class) →
           :attr:`ApertureKind.MONKEY_PATCH`.
        3. If the key matches late-binding vocabulary →
           :attr:`ApertureKind.LATE_BINDING`.
        4. If the value is callable and its ``__module__`` looks like a
           dynamic import path →
           :attr:`ApertureKind.DYNAMIC_IMPORT`.
        5. Default → :attr:`ApertureKind.DYNAMIC_IMPORT`.

        Args:
            key: The namespace key string.
            value: The value bound under that key.

        Returns:
            The inferred :class:`ApertureKind` for this binding site.

        Example:
            >>> analyzer = SemanticApertureAnalyzer()
            >>> analyzer.classify_aperture("exec_hook", lambda: None)
            <ApertureKind.EXEC_INJECTION: 'EXEC_INJECTION'>
        """
        key_lower = key.lower()

        # --- exec/eval injection ---
        if _EXEC_EVAL_PATTERN.search(key_lower):
            if "eval" in key_lower and "exec" not in key_lower:
                _log.debug("classify_aperture: EVAL_QUERY for key=%r", key)
                return ApertureKind.EVAL_QUERY
            _log.debug("classify_aperture: EXEC_INJECTION for key=%r", key)
            return ApertureKind.EXEC_INJECTION

        # --- monkey-patch: assigning a type/class ---
        if isinstance(value, type):
            _log.debug("classify_aperture: MONKEY_PATCH for key=%r (type)", key)
            return ApertureKind.MONKEY_PATCH

        # --- late binding: closure/proxy vocabulary ---
        if _LATE_BINDING_PATTERN.search(key_lower):
            _log.debug("classify_aperture: LATE_BINDING for key=%r", key)
            return ApertureKind.LATE_BINDING

        # --- dynamic import: callable with a module path that looks external ---
        if callable(value):
            module = getattr(value, "__module__", None) or ""
            if _DYNAMIC_IMPORT_PATTERN.search(key_lower) or (
                "." in module and not module.startswith("builtins")
            ):
                _log.debug(
                    "classify_aperture: DYNAMIC_IMPORT for key=%r module=%r",
                    key,
                    module,
                )
                return ApertureKind.DYNAMIC_IMPORT

        # --- fallback ---
        _log.debug("classify_aperture: default DYNAMIC_IMPORT for key=%r", key)
        return ApertureKind.DYNAMIC_IMPORT

    # ------------------------------------------------------------------
    # index & metrics
    # ------------------------------------------------------------------

    def _build_index(self) -> None:
        """Rebuild the internal :attr:`_index` from :attr:`_records`.

        Assigns each record a ``"kind_rank"`` index entry (ordinal position
        among records sharing the same kind) and an ``"age_bucket"`` entry
        (floor of age in seconds).  Existing index entries are replaced.

        This method is called automatically after :meth:`scan_namespace` and
        :meth:`seal_aperture`.

        Returns:
            None
        """
        self._index.clear()
        kind_counters: dict[str, int] = {}
        for record in self._records:
            kind_key = record.kind.value
            rank = kind_counters.get(kind_key, 0)
            kind_counters[kind_key] = rank + 1
            entry_id = _hash_str(f"{record.record_id}:kind_rank")
            self._index[entry_id] = ApertureIndexEntry(
                entry_id=entry_id,
                record_id=record.record_id,
                index_key="kind_rank",
                index_value=rank,
                computed_at=time.time(),
            )
            age_bucket = int(record.age())
            bucket_entry_id = _hash_str(f"{record.record_id}:age_bucket")
            self._index[bucket_entry_id] = ApertureIndexEntry(
                entry_id=bucket_entry_id,
                record_id=record.record_id,
                index_key="age_bucket",
                index_value=age_bucket,
                computed_at=time.time(),
            )
        _log.debug("_build_index: index has %d entries", len(self._index))

    def compute_aperture_index(
        self, records: list[SemanticApertureRecord]
    ) -> dict[str, int]:
        """Count aperture records by kind.

        Args:
            records: A list of :class:`SemanticApertureRecord` objects to
                analyse.  May be a subset of :attr:`_records`.

        Returns:
            A dictionary mapping each :class:`ApertureKind` value string to
            the count of records with that kind in *records*.

        Example:
            >>> analyzer = SemanticApertureAnalyzer()
            >>> idx = analyzer.compute_aperture_index(records)
            >>> "EXEC_INJECTION" in idx
            True
        """
        counts: dict[str, int] = {k.value: 0 for k in ApertureKind}
        for record in records:
            counts[record.kind.value] = counts.get(record.kind.value, 0) + 1
        _log.debug("compute_aperture_index: counts=%s", counts)
        return counts

    def aperture_density(
        self, records: list[SemanticApertureRecord]
    ) -> float:
        """Compute the ratio of open apertures to total apertures.

        Args:
            records: The list of records to measure.

        Returns:
            A float in [0.0, 1.0] representing the fraction of records that
            are currently active (``OPEN`` or ``PARTIALLY_SEALED``).  Returns
            ``0.0`` when *records* is empty.

        Example:
            >>> analyzer.aperture_density([])
            0.0
        """
        if not records:
            return 0.0
        active = sum(1 for r in records if r.is_active())
        density = active / len(records)
        _log.debug(
            "aperture_density: %d active / %d total = %.4f",
            active,
            len(records),
            density,
        )
        return density

    def find_overlapping_apertures(
        self, records: list[SemanticApertureRecord]
    ) -> list[tuple[SemanticApertureRecord, SemanticApertureRecord]]:
        """Find pairs of active apertures that share the same namespace key.

        Two apertures *overlap* when they have identical ``namespace_key``
        values and both are active.  Overlapping apertures indicate a
        potential double-binding that should be investigated.

        Args:
            records: The pool of records to search for overlaps.

        Returns:
            A list of ``(record_a, record_b)`` tuples where both are active
            and share the same namespace key.  Each unordered pair is
            returned exactly once.

        Example:
            >>> pairs = analyzer.find_overlapping_apertures(records)
            >>> all(a.namespace_key == b.namespace_key for a, b in pairs)
            True
        """
        active = [r for r in records if r.is_active()]
        key_map: dict[str, list[SemanticApertureRecord]] = {}
        for record in active:
            key_map.setdefault(record.namespace_key, []).append(record)
        overlapping: list[tuple[SemanticApertureRecord, SemanticApertureRecord]] = []
        for ns_key, group in key_map.items():
            if len(group) >= 2:
                for i in range(len(group)):
                    for j in range(i + 1, len(group)):
                        overlapping.append((group[i], group[j]))
                        _log.warning(
                            "find_overlapping_apertures: overlap detected "
                            "namespace_key=%r record_ids=%s/%s",
                            ns_key,
                            group[i].record_id,
                            group[j].record_id,
                        )
        return overlapping

    def seal_aperture(self, record_id: str) -> bool:
        """Seal the aperture identified by *record_id*.

        Since :class:`SemanticApertureRecord` is frozen, sealing creates a
        new record with ``state=SEALED`` and ``sealed_at`` set, replacing the
        old record in :attr:`_records`.

        Args:
            record_id: The ``record_id`` of the aperture to seal.

        Returns:
            ``True`` if the record was found and sealed; ``False`` if no
            record with the given ID exists.

        Example:
            >>> sealed = analyzer.seal_aperture(records[0].record_id)
            >>> sealed
            True
        """
        for idx, record in enumerate(self._records):
            if record.record_id == record_id:
                if record_id in self._sealed_ids:
                    _log.debug(
                        "seal_aperture: record %s already sealed", record_id
                    )
                    return True
                sealed_record = SemanticApertureRecord(
                    record_id=record.record_id,
                    kind=record.kind,
                    state=ApertureState.SEALED,
                    namespace_key=record.namespace_key,
                    value_type_name=record.value_type_name,
                    created_at=record.created_at,
                    sealed_at=time.time(),
                    metadata_json=record.metadata_json,
                )
                self._records[idx] = sealed_record
                self._sealed_ids.add(record_id)
                self._build_index()
                _log.info(
                    "seal_aperture: sealed record_id=%s namespace_key=%r",
                    record_id,
                    record.namespace_key,
                )
                return True
        _log.warning("seal_aperture: record_id=%s not found", record_id)
        return False

    def export_apertures(self) -> list[dict[str, Any]]:
        """Serialise all records to a list of plain dictionaries.

        Returns:
            A list of dictionaries, one per record, as produced by
            :meth:`SemanticApertureRecord.to_dict`.

        Example:
            >>> data = analyzer.export_apertures()
            >>> isinstance(data, list)
            True
        """
        exported = [r.to_dict() for r in self._records]
        _log.debug("export_apertures: exported %d records", len(exported))
        return exported

    def stats(self) -> dict[str, Any]:
        """Return comprehensive statistics about the current aperture corpus.

        Returns:
            A dictionary containing:
            - ``"total_records"``: total record count
            - ``"sealed_count"``: number of sealed records
            - ``"open_count"``: number of open records
            - ``"density"``: aperture density ratio
            - ``"entropy_bits"``: Shannon entropy over kind distribution
            - ``"kind_counts"``: per-kind record counts
            - ``"index_size"``: number of index entries
            - ``"overlap_count"``: number of overlapping aperture pairs

        Example:
            >>> s = analyzer.stats()
            >>> "total_records" in s
            True
        """
        kind_counts = self.compute_aperture_index(self._records)
        density = self.aperture_density(self._records)
        entropy = self.aperture_entropy()
        overlaps = self.find_overlapping_apertures(self._records)
        sealed_count = len(self._sealed_ids)
        open_count = len(self.open_apertures())
        return {
            "total_records": len(self._records),
            "sealed_count": sealed_count,
            "open_count": open_count,
            "density": round(density, 6),
            "entropy_bits": round(entropy, 6),
            "kind_counts": kind_counts,
            "index_size": len(self._index),
            "overlap_count": len(overlaps),
        }

    def find_by_kind(
        self, kind: ApertureKind
    ) -> list[SemanticApertureRecord]:
        """Return all records with the given kind.

        Args:
            kind: The :class:`ApertureKind` to filter by.

        Returns:
            A list of matching :class:`SemanticApertureRecord` objects.

        Example:
            >>> exec_recs = analyzer.find_by_kind(ApertureKind.EXEC_INJECTION)
            >>> all(r.kind == ApertureKind.EXEC_INJECTION for r in exec_recs)
            True
        """
        result = [r for r in self._records if r.kind == kind]
        _log.debug("find_by_kind: kind=%s → %d records", kind.value, len(result))
        return result

    def open_apertures(self) -> list[SemanticApertureRecord]:
        """Return all records whose state is :attr:`ApertureState.OPEN`.

        Returns:
            A filtered list of open :class:`SemanticApertureRecord` objects.

        Example:
            >>> open_recs = analyzer.open_apertures()
            >>> all(r.state == ApertureState.OPEN for r in open_recs)
            True
        """
        return [r for r in self._records if r.state == ApertureState.OPEN]

    def aperture_entropy(self) -> float:
        """Compute Shannon entropy (in bits) of the kind distribution.

        A high entropy value indicates that aperture kinds are uniformly
        distributed; a low value indicates dominance of one or a few kinds.

        Returns:
            Entropy in bits, rounded to 6 decimal places.

        Example:
            >>> analyzer.aperture_entropy() >= 0.0
            True
        """
        kind_counts = self.compute_aperture_index(self._records)
        counts_list = list(kind_counts.values())
        entropy = _entropy_of_distribution(counts_list)
        _log.debug("aperture_entropy: %.6f bits", entropy)
        return round(entropy, 6)


# ── witness ───────────────────────────────────────────────────────────────────


@dataclass
class ApertureWitness:
    """Records observations and sealing events for semantic apertures.

    The witness acts as an append-only audit log: every call to
    :meth:`observe_aperture` creates an immutable :class:`ApertureObservation`
    and every call to :meth:`witness_sealing` records a sealing event with a
    human-readable "sealed by" attribution.

    Attributes:
        _observations: Ordered list of all observations made.
        _sealing_log: Mapping from record_id to sealing event metadata.
        _invalidated: Set of record_ids that have been marked as invalidated.
        _timeline: Bounded deque of timeline event dictionaries.

    Example:
        >>> witness = ApertureWitness()
        >>> obs_id = witness.observe_aperture(record)
        >>> len(obs_id) > 0
        True
    """

    _observations: list[ApertureObservation] = field(default_factory=list)
    _sealing_log: dict[str, dict[str, Any]] = field(default_factory=dict)
    _invalidated: set[str] = field(default_factory=set)
    _timeline: deque = field(default_factory=deque)  # type: ignore[type-arg]

    # ------------------------------------------------------------------
    # observation
    # ------------------------------------------------------------------

    def observe_aperture(self, record: SemanticApertureRecord) -> str:
        """Create and store a new observation for *record*.

        Args:
            record: The :class:`SemanticApertureRecord` being observed.

        Returns:
            The ``obs_id`` of the newly created :class:`ApertureObservation`.

        Raises:
            TypeError: If *record* is not a :class:`SemanticApertureRecord`.

        Example:
            >>> witness = ApertureWitness()
            >>> obs_id = witness.observe_aperture(record)
            >>> isinstance(obs_id, str)
            True
        """
        if not isinstance(record, SemanticApertureRecord):
            raise TypeError(
                f"observe_aperture expects SemanticApertureRecord, "
                f"got {type(record).__name__!r}"
            )
        obs_id = _new_witness_id()
        obs = ApertureObservation(
            obs_id=obs_id,
            record_id=record.record_id,
            observed_at=time.time(),
            observer="ApertureWitness",
            note=(
                f"Observed {record.kind.value} aperture "
                f"at namespace_key={record.namespace_key!r}"
            ),
        )
        self._observations.append(obs)
        timeline_entry = {
            "event": "observation",
            "obs_id": obs_id,
            "record_id": record.record_id,
            "kind": record.kind.value,
            "timestamp": obs.observed_at,
        }
        self._timeline.append(timeline_entry)
        if len(self._timeline) > _MAX_TIMELINE_LEN:
            self._timeline.popleft()
        _log.debug(
            "observe_aperture: obs_id=%s record_id=%s kind=%s",
            obs_id,
            record.record_id,
            record.kind.value,
        )
        return obs_id

    # ------------------------------------------------------------------
    # sealing witness
    # ------------------------------------------------------------------

    def witness_sealing(self, record_id: str, sealed_by: str) -> bool:
        """Record a sealing event for *record_id*.

        This does NOT actually seal the aperture — call
        :meth:`SemanticApertureAnalyzer.seal_aperture` for that.  This
        method only records the witness-level attribution.

        Args:
            record_id: The ID of the aperture record that was sealed.
            sealed_by: A string identifying the entity that triggered the
                seal (e.g. a function name, user ID, or test tag).

        Returns:
            ``True`` if the sealing was recorded successfully; ``False`` if
            this record_id has already been recorded in the sealing log.

        Example:
            >>> witness.witness_sealing("abc123", "test_runner")
            True
        """
        if record_id in self._sealing_log:
            _log.debug(
                "witness_sealing: record_id=%s already in sealing_log", record_id
            )
            return False
        entry: dict[str, Any] = {
            "record_id": record_id,
            "sealed_by": sealed_by,
            "witnessed_at": time.time(),
            "fingerprint": _hash_str(f"{record_id}:{sealed_by}"),
        }
        self._sealing_log[record_id] = entry
        self._timeline.append(
            {
                "event": "sealing",
                "record_id": record_id,
                "sealed_by": sealed_by,
                "timestamp": entry["witnessed_at"],
            }
        )
        if len(self._timeline) > _MAX_TIMELINE_LEN:
            self._timeline.popleft()
        _log.info(
            "witness_sealing: record_id=%s sealed_by=%r", record_id, sealed_by
        )
        return True

    # ------------------------------------------------------------------
    # queries
    # ------------------------------------------------------------------

    def get_observations(self) -> list[dict[str, Any]]:
        """Return all observations as serialised dictionaries.

        Returns:
            A list of dicts produced by :meth:`ApertureObservation.to_dict`.

        Example:
            >>> witness.get_observations()
            [...]
        """
        return [obs.to_dict() for obs in self._observations]

    def invalidated_observations(self) -> list[dict[str, Any]]:
        """Return observations whose record_ids have been invalidated.

        Returns:
            A list of serialised observation dicts where the underlying
            record has been marked as invalidated via :meth:`mark_invalidated`.

        Example:
            >>> witness.mark_invalidated("abc123")
            >>> len(witness.invalidated_observations()) >= 1
            True
        """
        result = [
            obs.to_dict()
            for obs in self._observations
            if obs.record_id in self._invalidated
        ]
        _log.debug(
            "invalidated_observations: %d invalidated out of %d total",
            len(result),
            len(self._observations),
        )
        return result

    def observation_timeline(self) -> list[dict[str, Any]]:
        """Return all timeline events in chronological order.

        Returns:
            A list of event dictionaries sorted ascending by ``"timestamp"``.

        Example:
            >>> tl = witness.observation_timeline()
            >>> all("timestamp" in e for e in tl)
            True
        """
        events = list(self._timeline)
        events.sort(key=lambda e: e.get("timestamp", 0.0))
        return events

    def coverage_report(self) -> dict[str, Any]:
        """Generate a coverage report summarising observation and sealing stats.

        Returns:
            A dictionary with:
            - ``"total_observations"``: total observations made
            - ``"total_sealed"``: number of sealing events witnessed
            - ``"total_invalidated"``: number of records marked invalid
            - ``"sealing_coverage"``: ratio of sealed to observed records
            - ``"invalidation_ratio"``: ratio of invalidated to observed records
            - ``"unique_record_ids"``: count of distinct record_ids observed

        Example:
            >>> report = witness.coverage_report()
            >>> "sealing_coverage" in report
            True
        """
        total_obs = len(self._observations)
        total_sealed = len(self._sealing_log)
        total_invalidated = len(self._invalidated)
        unique_ids = len({obs.record_id for obs in self._observations})
        sealing_coverage = (
            total_sealed / unique_ids if unique_ids > 0 else 0.0
        )
        invalidation_ratio = (
            total_invalidated / unique_ids if unique_ids > 0 else 0.0
        )
        return {
            "total_observations": total_obs,
            "total_sealed": total_sealed,
            "total_invalidated": total_invalidated,
            "sealing_coverage": round(sealing_coverage, 6),
            "invalidation_ratio": round(invalidation_ratio, 6),
            "unique_record_ids": unique_ids,
        }

    def generate_witness_certificate(self) -> dict[str, Any]:
        """Generate a cryptographic-style certificate summarising all observations.

        The certificate contains a hash over all observation IDs (in sorted
        order) so that any tampering with the observation list will change the
        certificate fingerprint.

        Returns:
            A dictionary containing:
            - ``"certificate_id"``: unique ID for this certificate
            - ``"issued_at"``: POSIX timestamp
            - ``"obs_count"``: number of observations included
            - ``"content_hash"``: SHA-256 hash over sorted observation IDs
            - ``"coverage"``: output of :meth:`coverage_report`
            - ``"timeline_length"``: number of timeline events

        Example:
            >>> cert = witness.generate_witness_certificate()
            >>> "content_hash" in cert
            True
        """
        obs_ids_sorted = sorted(obs.obs_id for obs in self._observations)
        combined = "|".join(obs_ids_sorted)
        content_hash = hashlib.sha256(combined.encode()).hexdigest()
        cert_id = _new_witness_id()
        cert: dict[str, Any] = {
            "certificate_id": cert_id,
            "issued_at": time.time(),
            "obs_count": len(self._observations),
            "content_hash": content_hash,
            "coverage": self.coverage_report(),
            "timeline_length": len(self._timeline),
        }
        _log.info(
            "generate_witness_certificate: cert_id=%s hash=%s",
            cert_id,
            content_hash[:16],
        )
        return cert

    def mark_invalidated(self, record_id: str) -> None:
        """Mark a record_id as invalidated in the witness.

        Args:
            record_id: The ID of the aperture record to mark invalid.

        Returns:
            None

        Example:
            >>> witness.mark_invalidated("abc123")
        """
        self._invalidated.add(record_id)
        self._timeline.append(
            {
                "event": "invalidation",
                "record_id": record_id,
                "timestamp": time.time(),
            }
        )
        if len(self._timeline) > _MAX_TIMELINE_LEN:
            self._timeline.popleft()
        _log.info("mark_invalidated: record_id=%s", record_id)

    def observation_count(self) -> int:
        """Return the total number of observations recorded.

        Returns:
            Integer count of observations.

        Example:
            >>> witness.observation_count() >= 0
            True
        """
        return len(self._observations)

    def most_recent_observation(self) -> ApertureObservation | None:
        """Return the most recently added observation, or ``None``.

        Returns:
            The last :class:`ApertureObservation` in the internal list, or
            ``None`` if no observations have been made yet.

        Example:
            >>> witness.most_recent_observation() is None
            True
        """
        if not self._observations:
            _log.debug("most_recent_observation: no observations available")
            return None
        return self._observations[-1]


# ── coordinator ───────────────────────────────────────────────────────────────


@dataclass
class SemanticAperturesPythonWorldCoordinator:
    """Top-level coordinator combining scanning, witnessing and reporting.

    This class provides the primary public API for the *Semantic Apertures in
    the Python World* subsystem.  It orchestrates a
    :class:`SemanticApertureAnalyzer` and an :class:`ApertureWitness` in
    a single session-aware object.

    Attributes:
        analyzer: The underlying aperture analyzer instance.
        witness: The underlying aperture witness instance.
        _session_id: Unique identifier for this coordinator session.
        _created_at: POSIX timestamp of when this coordinator was created.

    Example:
        >>> coord = SemanticAperturesPythonWorldCoordinator()
        >>> result = coord.scan_and_witness({"lazy_fn": lambda: None})
        >>> result["new_records"] >= 1
        True
    """

    analyzer: SemanticApertureAnalyzer = field(
        default_factory=SemanticApertureAnalyzer
    )
    witness: ApertureWitness = field(default_factory=ApertureWitness)
    _session_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    _created_at: float = field(default_factory=time.time)

    # ------------------------------------------------------------------
    # core operations
    # ------------------------------------------------------------------

    def scan_and_witness(self, namespace: dict[str, Any]) -> dict[str, Any]:
        """Scan *namespace* and witness every discovered aperture.

        Calls :meth:`SemanticApertureAnalyzer.scan_namespace` then
        :meth:`ApertureWitness.observe_aperture` for each new record.

        Args:
            namespace: A Python namespace dict to scan.

        Returns:
            A summary dictionary with:
            - ``"session_id"``: this coordinator's session ID
            - ``"new_records"``: count of newly discovered records
            - ``"obs_ids"``: list of new observation IDs
            - ``"density"``: aperture density of the new records

        Example:
            >>> coord = SemanticAperturesPythonWorldCoordinator()
            >>> summary = coord.scan_and_witness({"exec_hook": lambda: None})
            >>> summary["new_records"] >= 1
            True
        """
        _log.info(
            "scan_and_witness: session=%s namespace_size=%d",
            self._session_id,
            len(namespace),
        )
        new_records = self.analyzer.scan_namespace(namespace)
        obs_ids: list[str] = []
        for record in new_records:
            obs_id = self.witness.observe_aperture(record)
            obs_ids.append(obs_id)
        density = self.analyzer.aperture_density(new_records)
        summary: dict[str, Any] = {
            "session_id": self._session_id,
            "new_records": len(new_records),
            "obs_ids": obs_ids,
            "density": round(density, 6),
        }
        _log.info(
            "scan_and_witness: discovered=%d density=%.4f",
            len(new_records),
            density,
        )
        return summary

    def seal_all_open(self, namespace: dict[str, Any]) -> int:
        """Scan *namespace* (if not already scanned) and seal all open apertures.

        Scans the namespace via :meth:`scan_and_witness`, then iterates over
        all currently open apertures and seals each one.

        Args:
            namespace: A Python namespace dict.  May be empty if apertures
                have already been discovered in a prior call.

        Returns:
            The number of apertures that were successfully sealed.

        Example:
            >>> coord = SemanticAperturesPythonWorldCoordinator()
            >>> coord.scan_and_witness({"lazy_loader": lambda: None})
            >>> count = coord.seal_all_open({})
            >>> count >= 0
            True
        """
        if namespace:
            self.scan_and_witness(namespace)
        open_recs = self.analyzer.open_apertures()
        sealed_count = 0
        for record in open_recs:
            ok = self.analyzer.seal_aperture(record.record_id)
            if ok:
                self.witness.witness_sealing(
                    record.record_id, _SEAL_SENTINEL
                )
                sealed_count += 1
        _log.info(
            "seal_all_open: session=%s sealed=%d", self._session_id, sealed_count
        )
        return sealed_count

    def aperture_report(self) -> dict[str, Any]:
        """Return a comprehensive combined report.

        Combines :meth:`SemanticApertureAnalyzer.stats` and
        :meth:`ApertureWitness.coverage_report` into a single dictionary,
        augmented with session metadata.

        Returns:
            A nested dictionary with keys ``"session_id"``, ``"uptime_s"``,
            ``"analyzer"``, and ``"witness"``.

        Example:
            >>> report = coord.aperture_report()
            >>> "analyzer" in report and "witness" in report
            True
        """
        report: dict[str, Any] = {
            "session_id": self._session_id,
            "uptime_s": round(time.time() - self._created_at, 3),
            "analyzer": self.analyzer.stats(),
            "witness": self.witness.coverage_report(),
        }
        _log.debug("aperture_report: session=%s", self._session_id)
        return report

    def reset(self) -> None:
        """Reinitialise the analyzer and witness, discarding all state.

        Returns:
            None

        Example:
            >>> coord.reset()
            >>> coord.analyzer.stats()["total_records"]
            0
        """
        _log.info("reset: session=%s — discarding all state", self._session_id)
        self.analyzer = SemanticApertureAnalyzer()
        self.witness = ApertureWitness()

    def export_state(self) -> dict[str, Any]:
        """Export the full coordinator state as a plain dictionary.

        Returns:
            A dictionary with:
            - ``"session_id"``: session identifier
            - ``"created_at"``: creation timestamp
            - ``"records"``: all aperture records as dicts
            - ``"observations"``: all observations as dicts
            - ``"certificate"``: current witness certificate
            - ``"global_hash"``: integrity hash of all record IDs

        Example:
            >>> state = coord.export_state()
            >>> "global_hash" in state
            True
        """
        state: dict[str, Any] = {
            "session_id": self._session_id,
            "created_at": self._created_at,
            "records": self.analyzer.export_apertures(),
            "observations": self.witness.get_observations(),
            "certificate": self.witness.generate_witness_certificate(),
            "global_hash": self.compute_global_hash(),
        }
        _log.info(
            "export_state: session=%s records=%d obs=%d",
            self._session_id,
            len(state["records"]),
            len(state["observations"]),
        )
        return state

    def compute_global_hash(self) -> str:
        """Compute a single hash over all record IDs for integrity checking.

        Sorts all record IDs and hashes their concatenation.  Can be used to
        detect whether the aperture corpus has changed between two snapshots.

        Returns:
            A 64-character hexadecimal SHA-256 digest string.

        Example:
            >>> h = coord.compute_global_hash()
            >>> len(h) == 64
            True
        """
        record_ids = sorted(r.record_id for r in self.analyzer._records)
        combined = ":".join(record_ids)
        digest = hashlib.sha256(combined.encode()).hexdigest()
        _log.debug("compute_global_hash: digest=%s", digest[:16])
        return digest

    def summary_line(self) -> str:
        """Return a single human-readable summary line.

        Returns:
            A string such as:
            ``"[session=abc123] 5 apertures | density=0.8000 | entropy=1.5219 bits"``.

        Example:
            >>> line = coord.summary_line()
            >>> "apertures" in line
            True
        """
        stats = self.analyzer.stats()
        line = (
            f"[session={self._session_id}] "
            f"{stats['total_records']} apertures | "
            f"density={stats['density']:.4f} | "
            f"entropy={stats['entropy_bits']:.4f} bits | "
            f"open={stats['open_count']} sealed={stats['sealed_count']}"
        )
        return line


# ── public API ────────────────────────────────────────────────────────────────

__all__ = [
    "ApertureKind",
    "ApertureState",
    "SemanticApertureRecord",
    "ApertureIndexEntry",
    "SemanticApertureAnalyzer",
    "ApertureObservation",
    "ApertureWitness",
    "SemanticAperturesPythonWorldCoordinator",
]

# copilot: s01 — Semantic Apertures in the Python World (Ch23 §1)
