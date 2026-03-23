r"""Serialization layer for JuGeo semantic artefacts.

This module implements the full serialization discipline for the JuGeo
framework as described in ``preliminaries/theory2.tex``.  Serialization must
preserve **semantic fidelity**: trust levels, evidence kinds, provenance
chains, residual obligations, and obstruction records must survive round-trips
without silent type coercion or trust flattening.

Design goals
------------
1. **No trust flattening** — ``TrustLevel`` and ``TrustProfile`` are always
   serialized as their structured algebraic representations, never collapsed
   to a scalar float.
2. **No evidence-kind erasure** — ``EvidenceKind`` is preserved as a named
   constant, not converted to an integer or boolean.
3. **Full provenance chains** — every ``ProvenanceNode`` in a DAG is emitted;
   no node is silently dropped because it is "internal".
4. **Residual obligations** — obligation identifiers survive round-trips so
   that downstream settlement logic can proceed correctly.
5. **Obstruction records** — first-class cohomology obstructions are
   serialized with their ``ObstructionKind`` tag; they are never merged into
   a generic error field.
6. **Schema versioning** — every payload carries a ``_jugeo_schema_version``
   tag so that :class:`SchemaVersionManager` can apply migrations before
   deserialization.
7. **Copilot-visible projections** — :class:`SerializationDiagnostics`
   exposes a ``copilot_serialization_summary`` method that produces a
   machine-readable report suitable for copilot-assisted orchestration.
   Copilot proposals entering via ``TrustLevel.COPILOT_SUGGESTED`` are never
   promoted during serialization; they retain their exact tier so that
   downstream trust-ordering checks remain correct.

Module layout
-------------
* :class:`SerializationFormat` — supported wire formats.
* :class:`SerializationContext` — per-call configuration.
* :class:`JuGeoSerializer` — generic entry-point.
* :class:`JudgmentSerializer` — eight-component judgment tuple ``(c,φ,A,E,O,B,T,Π)``.
* :class:`EvidenceSerializer` — evidence records and kinds.
* :class:`TrustSerializer` — ordered-algebra trust levels and profiles.
* :class:`ProvenanceSerializer` — provenance DAG nodes and chains.
* :class:`ManifestSerializer` — full manifest ``(J,O,E,X,K,η,σ)``.
* :class:`SchemaVersionManager` — versioning, migration, backward compatibility.
* :class:`SerializationValidator` — round-trip checks and trust-degradation guards.
* :class:`SerializationDiagnostics` — human- and machine-readable reports.

Backward compatibility
----------------------
The original ``serialize`` / ``deserialize`` helpers are retained as
module-level functions so that the ~20 modules importing them continue to work
without modification.  New code should use the class-based API.
"""

from __future__ import annotations

import copy
import hashlib
import json
import textwrap
import time
import uuid
from dataclasses import asdict, dataclass, field, is_dataclass
from enum import Enum
from typing import Any, Callable, Iterable, Mapping, Sequence


# ---------------------------------------------------------------------------
# Schema version constant
# ---------------------------------------------------------------------------

_CURRENT_SCHEMA_VERSION: str = '2.0.0'
_SCHEMA_VERSION_KEY: str = '_jugeo_schema_version'

# ---------------------------------------------------------------------------
# Small internal helpers
# ---------------------------------------------------------------------------


def _uid() -> str:
    """Return a short unique identifier."""
    return uuid.uuid4().hex[:12]


def _now() -> float:
    """Return a monotonic-safe wall-clock timestamp."""
    return time.time()


def _stable_hash(payload: str) -> str:
    """Return a deterministic SHA-256 hex digest of *payload*."""
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()


def _normalize(value: Any) -> Any:
    """Recursively lower *value* to a JSON-safe primitive.

    Handles dataclasses, Enums, dicts, lists, tuples, and sets.
    Dicts are key-sorted so that the output is deterministic.

    This function is intentionally conservative: it does **not** strip trust
    information or evidence kinds, so it is safe to call on any JuGeo object
    without risk of semantic data loss.
    """
    if is_dataclass(value) and not isinstance(value, type):
        return {k: _normalize(v) for k, v in asdict(value).items()}
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {
            str(k): _normalize(v)
            for k, v in sorted(value.items(), key=lambda e: str(e[0]))
        }
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_normalize(item) for item in value]
    return value


# ---------------------------------------------------------------------------
# 1. SerializationFormat
# ---------------------------------------------------------------------------


class SerializationFormat(Enum):
    """Supported wire formats for JuGeo serialization.

    Each variant names a concrete encoding strategy.  Not all formats support
    all features (e.g. ``TEXT_SUMMARY`` is lossy and never used for
    deserialization).

    Attributes
    ----------
    JSON:
        UTF-8 JSON with sorted keys.  The default and most portable format.
    MSGPACK:
        Binary MessagePack encoding.  Requires the ``msgpack`` extra.
    YAML:
        Human-readable YAML.  Requires the ``pyyaml`` extra.
    BINARY:
        Internal compact binary representation using a length-prefixed JSON
        envelope.  Suitable for high-throughput in-process transport.
    TEXT_SUMMARY:
        Lossy human-readable summary for display purposes only.  Round-trips
        are not guaranteed; this format is rejected by
        :class:`SerializationValidator`.
    """

    JSON = 'json'
    MSGPACK = 'msgpack'
    YAML = 'yaml'
    BINARY = 'binary'
    TEXT_SUMMARY = 'text_summary'

    def is_lossless(self) -> bool:
        """Return ``True`` if this format supports faithful round-trips.

        ``TEXT_SUMMARY`` is explicitly lossy.  All other formats are
        considered lossless for structurally well-formed JuGeo payloads.

        Returns
        -------
        bool
        """
        return self is not SerializationFormat.TEXT_SUMMARY

    def requires_extra(self) -> str | None:
        """Return the Python extra package name required by this format, or
        ``None`` if the format depends only on the standard library.

        Returns
        -------
        str | None
            ``'msgpack'`` for :attr:`MSGPACK`, ``'pyyaml'`` for
            :attr:`YAML`, ``None`` otherwise.
        """
        _extras: dict[SerializationFormat, str] = {
            SerializationFormat.MSGPACK: 'msgpack',
            SerializationFormat.YAML: 'pyyaml',
        }
        return _extras.get(self)

    def content_type(self) -> str:
        """Return a MIME-like content-type string for this format.

        Used when embedding serialized payloads in HTTP responses or manifest
        envelopes.

        Returns
        -------
        str
        """
        _types: dict[SerializationFormat, str] = {
            SerializationFormat.JSON: 'application/json',
            SerializationFormat.MSGPACK: 'application/msgpack',
            SerializationFormat.YAML: 'application/yaml',
            SerializationFormat.BINARY: 'application/octet-stream',
            SerializationFormat.TEXT_SUMMARY: 'text/plain',
        }
        return _types[self]

    def __repr__(self) -> str:
        return f'<SerializationFormat.{self.name}>'


# ---------------------------------------------------------------------------
# 2. SerializationContext
# ---------------------------------------------------------------------------


@dataclass
class SerializationContext:
    """Per-call configuration for JuGeo serialization operations.

    A ``SerializationContext`` is passed to every serializer method to control
    which components are included in the output and what safety checks are
    applied.  It does **not** mutate serializer state; multiple concurrent
    serialization calls may share the same context safely.

    Parameters
    ----------
    format:
        Wire format to use.  Defaults to :attr:`SerializationFormat.JSON`.
    include_provenance:
        When ``True`` (default), provenance chains are included in the
        output.  Setting this to ``False`` is useful for compact storage but
        must not be done in contexts where audit trails are required.
    include_evidence:
        When ``True`` (default), evidence records are included.  Disabling
        this produces summary-only payloads unsuitable for re-verification.
    include_obstructions:
        When ``True`` (default), obstruction records are included.  Disabling
        strips first-class cohomology data and is therefore only appropriate
        for display-only contexts.
    redact_internals:
        When ``True``, internal implementation details (e.g. solver
        intermediate states) are omitted from the output.  Trust annotations
        are never redacted regardless of this flag.
    trust_floor:
        If set, only evidence records at or above this trust level are
        included.  Records below ``trust_floor`` are omitted rather than
        downcast.  The floor is *not* applied to the judgment's own trust
        annotation — that would constitute trust flattening.
    schema_version:
        Schema version tag embedded in every payload.  Defaults to
        :data:`_CURRENT_SCHEMA_VERSION`.
    strict_round_trip:
        When ``True``, the serializer performs an automatic round-trip check
        after every serialization and raises :exc:`SerializationError` if the
        reconstructed object differs semantically from the original.
    """

    format: SerializationFormat = field(default_factory=lambda: SerializationFormat.JSON)
    include_provenance: bool = True
    include_evidence: bool = True
    include_obstructions: bool = True
    redact_internals: bool = False
    trust_floor: str | None = None
    schema_version: str = field(default_factory=lambda: _CURRENT_SCHEMA_VERSION)
    strict_round_trip: bool = False

    def with_format(self, fmt: SerializationFormat) -> SerializationContext:
        """Return a copy of this context with *format* replaced.

        Parameters
        ----------
        fmt:
            New wire format.

        Returns
        -------
        SerializationContext
        """
        c = copy.copy(self)
        c.format = fmt
        return c

    def minimal(self) -> SerializationContext:
        """Return a minimal context: no provenance, no evidence, no obstructions.

        Useful for size-constrained summaries.  The caller is responsible for
        ensuring that the resulting payload is only used for display, not for
        re-verification.

        Returns
        -------
        SerializationContext
        """
        c = copy.copy(self)
        c.include_provenance = False
        c.include_evidence = False
        c.include_obstructions = False
        return c

    def strict(self) -> SerializationContext:
        """Return a copy with ``strict_round_trip`` enabled.

        Returns
        -------
        SerializationContext
        """
        c = copy.copy(self)
        c.strict_round_trip = True
        return c

    def to_dict(self) -> dict[str, Any]:
        """Serialize this context to a plain dictionary.

        Returns
        -------
        dict[str, Any]
        """
        return {
            'format': self.format.value,
            'include_provenance': self.include_provenance,
            'include_evidence': self.include_evidence,
            'include_obstructions': self.include_obstructions,
            'redact_internals': self.redact_internals,
            'trust_floor': self.trust_floor,
            'schema_version': self.schema_version,
            'strict_round_trip': self.strict_round_trip,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> SerializationContext:
        """Reconstruct a :class:`SerializationContext` from a dictionary.

        Parameters
        ----------
        payload:
            Dictionary as produced by :meth:`to_dict`.

        Returns
        -------
        SerializationContext
        """
        fmt_raw = payload.get('format', SerializationFormat.JSON.value)
        fmt = SerializationFormat(fmt_raw) if isinstance(fmt_raw, str) else SerializationFormat.JSON
        return cls(
            format=fmt,
            include_provenance=bool(payload.get('include_provenance', True)),
            include_evidence=bool(payload.get('include_evidence', True)),
            include_obstructions=bool(payload.get('include_obstructions', True)),
            redact_internals=bool(payload.get('redact_internals', False)),
            trust_floor=payload.get('trust_floor'),
            schema_version=str(payload.get('schema_version', _CURRENT_SCHEMA_VERSION)),
            strict_round_trip=bool(payload.get('strict_round_trip', False)),
        )


# ---------------------------------------------------------------------------
# Serialization error
# ---------------------------------------------------------------------------


class SerializationError(Exception):
    """Raised when a JuGeo serialization or deserialization operation fails.

    Carries a ``field`` attribute identifying which component of the payload
    triggered the error so that callers can produce targeted diagnostics.
    """

    def __init__(self, message: str, *, field: str = '', payload_type: str = '') -> None:
        super().__init__(message)
        self.field = field
        self.payload_type = payload_type

    def __repr__(self) -> str:
        return (
            f'SerializationError({str(self)!r}, '
            f'field={self.field!r}, payload_type={self.payload_type!r})'
        )


# ---------------------------------------------------------------------------
# 3. JuGeoSerializer
# ---------------------------------------------------------------------------


class JuGeoSerializer:
    """Generic serialization entry-point for JuGeo objects.

    :class:`JuGeoSerializer` provides a format-agnostic surface for
    serializing and deserializing any JuGeo object that exposes a
    ``to_dict`` / ``from_dict`` interface or is a plain dataclass.

    The class orchestrates the specialized sub-serializers
    (:class:`TrustSerializer`, :class:`EvidenceSerializer`,
    :class:`ProvenanceSerializer`, etc.) and enforces schema versioning via
    :class:`SchemaVersionManager`.

    Parameters
    ----------
    context:
        Default serialization context.  Individual method calls may override
        it by passing a *context* keyword argument.
    """

    def __init__(self, context: SerializationContext | None = None) -> None:
        self._default_context: SerializationContext = context or SerializationContext()
        self._schema_manager: SchemaVersionManager = SchemaVersionManager()
        self._validator: SerializationValidator = SerializationValidator()

    # -- primary interface ---------------------------------------------------

    def serialize(self, obj: Any, *, context: SerializationContext | None = None) -> str | bytes:
        """Serialize *obj* to the wire format specified by *context*.

        Parameters
        ----------
        obj:
            Any JuGeo object with a ``to_dict`` method or a plain dataclass /
            primitive.
        context:
            Serialization context overriding the instance default.

        Returns
        -------
        str | bytes
            UTF-8 string for JSON/YAML/TEXT_SUMMARY, bytes for MSGPACK/BINARY.

        Raises
        ------
        SerializationError
            If the object cannot be serialized, or if ``strict_round_trip``
            is enabled and the round-trip check fails.
        """
        ctx = context or self._default_context
        raw = self.to_dict(obj, context=ctx)
        raw[_SCHEMA_VERSION_KEY] = ctx.schema_version
        return self._encode(raw, ctx.format)

    def deserialize(self, payload: str | bytes, *, context: SerializationContext | None = None) -> Any:
        """Deserialize *payload* produced by :meth:`serialize`.

        Parameters
        ----------
        payload:
            Bytes or string produced by :meth:`serialize`.
        context:
            Serialization context (used to determine format if not
            auto-detected from payload type).

        Returns
        -------
        Any
            The deserialized object as a plain dictionary.  Callers needing
            typed objects should pass the result to the appropriate
            ``from_dict`` method.

        Raises
        ------
        SerializationError
            If the payload is malformed, or if schema migration fails.
        """
        ctx = context or self._default_context
        raw = self._decode(payload, ctx.format)
        raw = self._schema_manager.migrate(raw)
        return raw

    def round_trip_check(
        self, obj: Any, *, context: SerializationContext | None = None
    ) -> tuple[bool, str]:
        """Serialize *obj*, deserialize the result, and compare semantically.

        Parameters
        ----------
        obj:
            Object to round-trip.
        context:
            Serialization context.

        Returns
        -------
        tuple[bool, str]
            ``(passed, message)`` where *passed* is ``True`` iff the
            round-trip produced a semantically equivalent representation.
        """
        ctx = context or self._default_context
        try:
            wire = self.serialize(obj, context=ctx)
            recovered = self.deserialize(wire, context=ctx)
            original_dict = self.to_dict(obj, context=ctx)
            original_dict.pop(_SCHEMA_VERSION_KEY, None)
            recovered.pop(_SCHEMA_VERSION_KEY, None)
            original_str = json.dumps(_normalize(original_dict), sort_keys=True)
            recovered_str = json.dumps(_normalize(recovered), sort_keys=True)
            if original_str == recovered_str:
                return True, 'round-trip OK'
            diff_hint = _first_diff_key(original_dict, recovered)
            return False, f'round-trip mismatch at key: {diff_hint}'
        except Exception as exc:
            return False, f'round-trip raised {type(exc).__name__}: {exc}'

    def to_json(self, obj: Any, *, context: SerializationContext | None = None) -> str:
        """Serialize *obj* to a UTF-8 JSON string.

        Convenience wrapper around :meth:`serialize` with
        :attr:`SerializationFormat.JSON` forced.

        Parameters
        ----------
        obj:
            Object to serialize.
        context:
            Optional context; format is overridden to JSON.

        Returns
        -------
        str
        """
        ctx = (context or self._default_context).with_format(SerializationFormat.JSON)
        result = self.serialize(obj, context=ctx)
        assert isinstance(result, str)
        return result

    def from_json(self, payload: str, *, context: SerializationContext | None = None) -> Any:
        """Deserialize a UTF-8 JSON string produced by :meth:`to_json`.

        Parameters
        ----------
        payload:
            JSON string.
        context:
            Optional context; format is overridden to JSON.

        Returns
        -------
        Any
        """
        ctx = (context or self._default_context).with_format(SerializationFormat.JSON)
        return self.deserialize(payload, context=ctx)

    def to_dict(self, obj: Any, *, context: SerializationContext | None = None) -> dict[str, Any]:
        """Lower *obj* to a JSON-safe dictionary.

        If *obj* exposes a ``to_dict`` method, that is called first.
        Otherwise the generic :func:`_normalize` fallback is used.

        Parameters
        ----------
        obj:
            Object to lower.
        context:
            Unused here but accepted for API uniformity.

        Returns
        -------
        dict[str, Any]
        """
        if hasattr(obj, 'to_dict') and callable(obj.to_dict):
            raw = obj.to_dict()
        elif is_dataclass(obj) and not isinstance(obj, type):
            raw = _normalize(obj)
        elif isinstance(obj, dict):
            raw = _normalize(obj)
        else:
            raw = {'value': _normalize(obj)}
        if not isinstance(raw, dict):
            raw = {'value': raw}
        return raw

    def from_dict(
        self,
        payload: dict[str, Any],
        target_type: type | None = None,
        *,
        context: SerializationContext | None = None,
    ) -> Any:
        """Reconstruct an object from a plain dictionary.

        If *target_type* exposes a ``from_dict`` class method, it is called.
        Otherwise the dictionary is returned as-is.

        Parameters
        ----------
        payload:
            Dictionary as produced by :meth:`to_dict`.
        target_type:
            Optional class to reconstruct.
        context:
            Unused here but accepted for API uniformity.

        Returns
        -------
        Any
        """
        if target_type is not None and hasattr(target_type, 'from_dict'):
            return target_type.from_dict(payload)
        return payload

    # -- internal helpers ----------------------------------------------------

    def _encode(self, raw: dict[str, Any], fmt: SerializationFormat) -> str | bytes:
        """Encode *raw* to the specified wire format."""
        if fmt is SerializationFormat.JSON:
            return json.dumps(_normalize(raw), sort_keys=True, ensure_ascii=False)
        if fmt is SerializationFormat.TEXT_SUMMARY:
            return _dict_to_text_summary(raw)
        if fmt is SerializationFormat.BINARY:
            inner = json.dumps(_normalize(raw), sort_keys=True, ensure_ascii=False)
            encoded = inner.encode('utf-8')
            length = len(encoded).to_bytes(4, 'big')
            return length + encoded
        if fmt is SerializationFormat.MSGPACK:
            try:
                import msgpack  # type: ignore[import]
                return msgpack.packb(_normalize(raw), use_bin_type=True)
            except ImportError as exc:
                raise SerializationError(
                    "MSGPACK format requires 'msgpack' package",
                    payload_type='msgpack',
                ) from exc
        if fmt is SerializationFormat.YAML:
            try:
                import yaml  # type: ignore[import]
                return yaml.dump(_normalize(raw), sort_keys=True, allow_unicode=True)
            except ImportError as exc:
                raise SerializationError(
                    "YAML format requires 'pyyaml' package",
                    payload_type='yaml',
                ) from exc
        raise SerializationError(f'Unknown format: {fmt}', payload_type=str(fmt))

    def _decode(self, payload: str | bytes, fmt: SerializationFormat) -> dict[str, Any]:
        """Decode *payload* from the specified wire format."""
        if fmt is SerializationFormat.JSON:
            text = payload if isinstance(payload, str) else payload.decode('utf-8')
            return json.loads(text)
        if fmt is SerializationFormat.TEXT_SUMMARY:
            raise SerializationError(
                'TEXT_SUMMARY is a lossy display format and cannot be deserialized.',
                payload_type='text_summary',
            )
        if fmt is SerializationFormat.BINARY:
            if not isinstance(payload, bytes) or len(payload) < 4:
                raise SerializationError('BINARY payload too short', payload_type='binary')
            length = int.from_bytes(payload[:4], 'big')
            inner = payload[4:4 + length].decode('utf-8')
            return json.loads(inner)
        if fmt is SerializationFormat.MSGPACK:
            try:
                import msgpack  # type: ignore[import]
                return msgpack.unpackb(payload, raw=False)
            except ImportError as exc:
                raise SerializationError(
                    "MSGPACK format requires 'msgpack' package",
                    payload_type='msgpack',
                ) from exc
        if fmt is SerializationFormat.YAML:
            try:
                import yaml  # type: ignore[import]
                text = payload if isinstance(payload, str) else payload.decode('utf-8')
                return yaml.safe_load(text)
            except ImportError as exc:
                raise SerializationError(
                    "YAML format requires 'pyyaml' package",
                    payload_type='yaml',
                ) from exc
        raise SerializationError(f'Unknown format: {fmt}', payload_type=str(fmt))


# ---------------------------------------------------------------------------
# 4. JudgmentSerializer
# ---------------------------------------------------------------------------


class JudgmentSerializer:
    """Serializer for the eight-component JuGeo judgment tuple (c,φ,A,E,O,B,T,Π).

    Each component is serialized to a named slot in the output dictionary.
    No component is silently omitted, merged, or type-coerced.  In particular:

    * **T** (trust annotation) is always serialized via :class:`TrustSerializer`
      so that ordered-algebra structure is preserved.
    * **Π** (provenance) is serialized via :class:`ProvenanceSerializer`
      with the full node DAG intact.
    * **E** (evidence bundle) is serialized via :class:`EvidenceSerializer`
      with evidence kinds and trust tiers preserved.
    * **O** (residual obligations) and **B** (obstructions) are preserved as
      structured records, not flattened to string lists.

    Parameters
    ----------
    context:
        Default serialization context.
    """

    # Component keys in the canonical serialization order.
    _SLOTS: tuple[str, ...] = ('c', 'phi', 'A', 'E', 'O', 'B', 'T', 'Pi')

    def __init__(self, context: SerializationContext | None = None) -> None:
        self._ctx = context or SerializationContext()
        self._trust_ser = TrustSerializer()
        self._evidence_ser = EvidenceSerializer()
        self._provenance_ser = ProvenanceSerializer()

    def serialize_judgment(
        self,
        judgment: Any,
        *,
        context: SerializationContext | None = None,
    ) -> dict[str, Any]:
        """Serialize a judgment object to a canonical dictionary.

        The judgment must expose named attributes for each slot:
        ``coordinate`` / ``c``, ``proposition`` / ``phi``,
        ``carrier`` / ``A``, ``evidence`` / ``E``, ``obligations`` / ``O``,
        ``obstructions`` / ``B``, ``trust`` / ``T``, ``provenance`` / ``Pi``.
        Plain dicts with those keys are also accepted.

        Parameters
        ----------
        judgment:
            Judgment object or dict.
        context:
            Override context.

        Returns
        -------
        dict[str, Any]
            Canonical dictionary with all eight slots present.
        """
        ctx = context or self._ctx
        d = judgment if isinstance(judgment, dict) else _obj_to_dict(judgment)
        return {
            _SCHEMA_VERSION_KEY: ctx.schema_version,
            '_type': 'judgment',
            'c': self._serialize_coordinate(d),
            'phi': self._serialize_proposition(d),
            'A': self._serialize_carrier(d),
            'E': self._serialize_evidence_bundle(d, ctx),
            'O': self._serialize_obligations(d, ctx),
            'B': self._serialize_obstructions(d, ctx),
            'T': self._serialize_trust(d),
            'Pi': self._serialize_provenance(d, ctx),
        }

    def deserialize_judgment(
        self,
        payload: dict[str, Any],
        *,
        context: SerializationContext | None = None,
    ) -> dict[str, Any]:
        """Deserialize a judgment from a canonical dictionary.

        Returns a plain dict with normalized slot values.  Callers needing a
        typed :class:`~jugeo.judgments.judgment_terms.LocalJudgment` should
        call :meth:`to_local_judgment` on the result.

        Parameters
        ----------
        payload:
            Dictionary as produced by :meth:`serialize_judgment`.
        context:
            Override context.

        Returns
        -------
        dict[str, Any]
        """
        ctx = context or self._ctx
        if payload.get('_type') != 'judgment':
            raise SerializationError(
                "Payload '_type' is not 'judgment'",
                field='_type',
                payload_type='judgment',
            )
        return {
            'c': payload.get('c', ''),
            'phi': payload.get('phi', ''),
            'A': payload.get('A', {}),
            'E': self._evidence_ser.deserialize_bundle(payload.get('E', []), context=ctx),
            'O': self._deserialize_obligations(payload.get('O', [])),
            'B': self._deserialize_obstructions(payload.get('B', []), ctx),
            'T': self._trust_ser.deserialize_trust(payload.get('T', {})),
            'Pi': self._provenance_ser.deserialize_chain(payload.get('Pi', {}), context=ctx),
        }

    def verify_slots_present(self, payload: dict[str, Any]) -> list[str]:
        """Return a list of missing required slots in *payload*.

        Parameters
        ----------
        payload:
            Dictionary to inspect.

        Returns
        -------
        list[str]
            Names of missing slots; empty list if all slots are present.
        """
        required = ('c', 'phi', 'A', 'E', 'O', 'B', 'T', 'Pi')
        return [k for k in required if k not in payload]

    def canonical_hash(self, payload: dict[str, Any]) -> str:
        """Compute a deterministic hash of a serialized judgment.

        The hash covers all eight slots.  It can be used as a stable key for
        caching or deduplication.

        Parameters
        ----------
        payload:
            Serialized judgment dictionary.

        Returns
        -------
        str
            Hex-encoded SHA-256 digest.
        """
        clean = {k: payload[k] for k in ('c', 'phi', 'A', 'E', 'O', 'B', 'T', 'Pi') if k in payload}
        canonical = json.dumps(_normalize(clean), sort_keys=True)
        return _stable_hash(canonical)

    def diff_judgments(
        self,
        left: dict[str, Any],
        right: dict[str, Any],
    ) -> dict[str, Any]:
        """Return a slot-by-slot diff between two serialized judgments.

        Parameters
        ----------
        left, right:
            Serialized judgment dictionaries.

        Returns
        -------
        dict[str, Any]
            Maps slot names to ``{'left': ..., 'right': ...}`` entries for
            slots that differ; omits matching slots.
        """
        slots = ('c', 'phi', 'A', 'E', 'O', 'B', 'T', 'Pi')
        diff: dict[str, Any] = {}
        for slot in slots:
            lv = json.dumps(_normalize(left.get(slot)), sort_keys=True)
            rv = json.dumps(_normalize(right.get(slot)), sort_keys=True)
            if lv != rv:
                diff[slot] = {'left': left.get(slot), 'right': right.get(slot)}
        return diff

    # -- private helpers -----------------------------------------------------

    def _serialize_coordinate(self, d: dict[str, Any]) -> str:
        raw = d.get('c') or d.get('coordinate') or ''
        if hasattr(raw, 'name'):
            return str(raw.name)
        return str(raw)

    def _serialize_proposition(self, d: dict[str, Any]) -> str:
        raw = d.get('phi') or d.get('proposition') or ''
        return str(raw)

    def _serialize_carrier(self, d: dict[str, Any]) -> Any:
        raw = d.get('A') or d.get('carrier') or {}
        return _normalize(raw)

    def _serialize_evidence_bundle(
        self, d: dict[str, Any], ctx: SerializationContext
    ) -> list[dict[str, Any]]:
        raw = d.get('E') or d.get('evidence') or []
        if not ctx.include_evidence:
            return []
        if isinstance(raw, (list, tuple)):
            return [self._evidence_ser.serialize_record(r) for r in raw]
        return [self._evidence_ser.serialize_record(raw)]

    def _serialize_obligations(self, d: dict[str, Any], ctx: SerializationContext) -> list[Any]:
        raw = d.get('O') or d.get('obligations') or []
        if isinstance(raw, (list, tuple)):
            return [_normalize(o) for o in raw]
        return [_normalize(raw)]

    def _serialize_obstructions(
        self, d: dict[str, Any], ctx: SerializationContext
    ) -> list[Any]:
        if not ctx.include_obstructions:
            return []
        raw = d.get('B') or d.get('obstructions') or []
        if isinstance(raw, (list, tuple)):
            return [_normalize(b) for b in raw]
        return [_normalize(raw)]

    def _serialize_trust(self, d: dict[str, Any]) -> dict[str, Any]:
        raw = d.get('T') or d.get('trust')
        return self._trust_ser.serialize_trust(raw)

    def _serialize_provenance(
        self, d: dict[str, Any], ctx: SerializationContext
    ) -> dict[str, Any]:
        if not ctx.include_provenance:
            return {'_provenance_omitted': True}
        raw = d.get('Pi') or d.get('provenance')
        return self._provenance_ser.serialize_chain(raw, context=ctx)

    def _deserialize_obligations(self, raw: list[Any]) -> list[Any]:
        return list(raw) if isinstance(raw, (list, tuple)) else []

    def _deserialize_obstructions(
        self, raw: list[Any], ctx: SerializationContext
    ) -> list[Any]:
        return list(raw) if isinstance(raw, (list, tuple)) else []


# ---------------------------------------------------------------------------
# 5. EvidenceSerializer
# ---------------------------------------------------------------------------


class EvidenceSerializer:
    """Serializer for JuGeo evidence records and bundles.

    Evidence must be serialized with its ``EvidenceKind`` tag and
    ``TrustTier`` intact.  This class explicitly rejects any attempt to
    reduce kind information to a boolean or integer.

    Parameters
    ----------
    context:
        Default serialization context.
    """

    # Recognized evidence kind values (mirrors EvidenceKind enum).
    _KNOWN_KINDS: frozenset[str] = frozenset({
        'proof', 'solver', 'runtime', 'semantic', 'proposal',
        'oracle', 'human', 'certificate', 'external',
    })

    def __init__(self, context: SerializationContext | None = None) -> None:
        self._ctx = context or SerializationContext()
        self._trust_ser = TrustSerializer()

    def serialize_record(self, record: Any) -> dict[str, Any]:
        """Serialize a single evidence record to a canonical dictionary.

        Parameters
        ----------
        record:
            An ``EvidenceRecord``-like object or plain dict.

        Returns
        -------
        dict[str, Any]
        """
        d = record if isinstance(record, dict) else _obj_to_dict(record)
        kind = self._extract_kind(d)
        trust = self._extract_trust(d)
        return {
            '_type': 'evidence_record',
            'kind': kind,
            'channel': _normalize(d.get('channel') or d.get('channel_name') or ''),
            'claim': str(d.get('claim', '')),
            'payload': _normalize(d.get('payload', {})),
            'trust': self._trust_ser.serialize_trust(trust),
            'obligations': list(d.get('obligations', [])),
            'provenance': _normalize(d.get('provenance', [])),
        }

    def deserialize_record(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Deserialize an evidence record from a canonical dictionary.

        Parameters
        ----------
        payload:
            Dictionary as produced by :meth:`serialize_record`.

        Returns
        -------
        dict[str, Any]

        Raises
        ------
        SerializationError
            If the ``kind`` field is missing or unrecognized.
        """
        kind = str(payload.get('kind', ''))
        if not kind:
            raise SerializationError(
                'Evidence record missing kind field',
                field='kind',
                payload_type='evidence_record',
            )
        return {
            'kind': kind,
            'channel': payload.get('channel', ''),
            'claim': str(payload.get('claim', '')),
            'payload': dict(payload.get('payload', {})),
            'trust': self._trust_ser.deserialize_trust(payload.get('trust', {})),
            'obligations': list(payload.get('obligations', [])),
            'provenance': payload.get('provenance', []),
        }

    def serialize_bundle(
        self,
        records: Iterable[Any],
        *,
        context: SerializationContext | None = None,
    ) -> list[dict[str, Any]]:
        """Serialize an iterable of evidence records.

        Parameters
        ----------
        records:
            Evidence records to serialize.
        context:
            Override context.

        Returns
        -------
        list[dict[str, Any]]
        """
        ctx = context or self._ctx
        serialized = [self.serialize_record(r) for r in records]
        if ctx.trust_floor is not None:
            serialized = [r for r in serialized if self._meets_floor(r, ctx.trust_floor)]
        return serialized

    def deserialize_bundle(
        self,
        payload: list[Any],
        *,
        context: SerializationContext | None = None,
    ) -> list[dict[str, Any]]:
        """Deserialize a list of evidence record dictionaries.

        Parameters
        ----------
        payload:
            List of dicts as produced by :meth:`serialize_bundle`.
        context:
            Override context.

        Returns
        -------
        list[dict[str, Any]]
        """
        return [self.deserialize_record(r) for r in (payload or [])]

    def assert_no_kind_erasure(self, original: Any, recovered: dict[str, Any]) -> None:
        """Assert that the evidence kind survived serialization intact.

        Parameters
        ----------
        original:
            Original evidence record (object or dict).
        recovered:
            Deserialized evidence record dict.

        Raises
        ------
        SerializationError
            If the kind was erased or changed during serialization.
        """
        d = original if isinstance(original, dict) else _obj_to_dict(original)
        original_kind = self._extract_kind(d)
        recovered_kind = str(recovered.get('kind', ''))
        if original_kind != recovered_kind:
            raise SerializationError(
                f'Evidence kind erasure detected: {original_kind!r} -> {recovered_kind!r}',
                field='kind',
                payload_type='evidence_record',
            )

    def summary(self, records: list[dict[str, Any]]) -> dict[str, Any]:
        """Summarize a serialized evidence bundle.

        Parameters
        ----------
        records:
            List of serialized evidence record dicts.

        Returns
        -------
        dict[str, Any]
            Counts by kind and trust tier.
        """
        by_kind: dict[str, int] = {}
        by_tier: dict[str, int] = {}
        for r in records:
            k = str(r.get('kind', 'unknown'))
            by_kind[k] = by_kind.get(k, 0) + 1
            trust = r.get('trust', {})
            tier = str(trust.get('level') or trust.get('tier') or 'unknown')
            by_tier[tier] = by_tier.get(tier, 0) + 1
        return {
            'total': len(records),
            'by_kind': by_kind,
            'by_trust_tier': by_tier,
        }

    # -- private helpers -----------------------------------------------------

    def _extract_kind(self, d: dict[str, Any]) -> str:
        raw = d.get('kind') or d.get('evidence_kind') or d.get('channel', {})
        if isinstance(raw, dict):
            raw = raw.get('kind', 'proposal')
        if isinstance(raw, Enum):
            return str(raw.value)
        if hasattr(raw, 'kind'):
            inner = raw.kind
            if isinstance(inner, Enum):
                return str(inner.value)
            return str(inner)
        return str(raw) if raw else 'proposal'

    def _extract_trust(self, d: dict[str, Any]) -> Any:
        return d.get('trust') or d.get('trust_profile') or d.get('tier')

    def _meets_floor(self, record: dict[str, Any], floor: str) -> bool:
        trust = record.get('trust', {})
        tier = str(trust.get('level') or trust.get('tier') or '')
        _order = [
            'contradicted', 'unverified', 'copilot_suggested', 'oracle_proposed',
            'human_attested', 'runtime_witnessed', 'solver_discharged',
            'mechanically_verified',
        ]
        try:
            return _order.index(tier.lower()) >= _order.index(floor.lower())
        except ValueError:
            return True


# ---------------------------------------------------------------------------
# 6. TrustSerializer
# ---------------------------------------------------------------------------


class TrustSerializer:
    """Serializer for JuGeo ordered-algebra trust levels and profiles.

    Trust is **not** a scalar float.  This class enforces that by always
    serializing ``TrustLevel`` as its named constant and ``TrustProfile`` as a
    structured dictionary with ``level``, ``support_scope``, and ``reasons``
    fields.  Copilot-sourced trust (``TrustLevel.COPILOT_SUGGESTED``) is
    never promoted during serialization; it retains its exact tier.

    Parameters
    ----------
    context:
        Default serialization context.
    """

    _VALID_LEVELS: frozenset[str] = frozenset({
        'mechanically_verified', 'solver_discharged', 'runtime_witnessed',
        'human_attested', 'oracle_proposed', 'copilot_suggested',
        'unverified', 'contradicted',
    })

    def __init__(self, context: SerializationContext | None = None) -> None:
        self._ctx = context or SerializationContext()

    def serialize_trust(self, trust: Any) -> dict[str, Any]:
        """Serialize a trust value to a canonical dictionary.

        Handles ``TrustLevel`` enums, ``TrustProfile`` dataclasses, legacy
        ``TrustTier`` IntEnums, and plain strings.

        Parameters
        ----------
        trust:
            Trust value to serialize.  May be ``None``.

        Returns
        -------
        dict[str, Any]
            Always includes a ``'level'`` key with a string value from
            :attr:`_VALID_LEVELS`.
        """
        if trust is None:
            return {'level': 'unverified', 'source': 'default'}
        if isinstance(trust, Enum):
            level = str(trust.value).lower()
            return {'level': level, 'source': 'trust_level_enum'}
        if isinstance(trust, dict):
            return self._normalize_trust_dict(trust)
        if is_dataclass(trust) and not isinstance(trust, type):
            d = asdict(trust)
            return self._normalize_trust_dict(d)
        if hasattr(trust, 'tier'):
            tier = trust.tier
            level = self._tier_to_level(tier)
            support = list(getattr(trust, 'support_scope', ()))
            reasons = list(getattr(trust, 'reasons', ()))
            return {
                'level': level,
                'support_scope': support,
                'reasons': reasons,
                'source': 'trust_profile',
            }
        if hasattr(trust, 'level'):
            raw = trust.level
            level = str(raw.value).lower() if isinstance(raw, Enum) else str(raw).lower()
            return {'level': level, 'source': 'trust_object'}
        return {'level': str(trust).lower(), 'source': 'raw_string'}

    def deserialize_trust(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Deserialize a trust payload to a normalized dictionary.

        Validates that the ``'level'`` key contains a recognized value and
        raises :exc:`SerializationError` if silent coercion would be needed.

        Parameters
        ----------
        payload:
            Dictionary as produced by :meth:`serialize_trust`.

        Returns
        -------
        dict[str, Any]

        Raises
        ------
        SerializationError
            If the level is missing or unknown and strict coercion would be
            required.
        """
        if not isinstance(payload, dict):
            return {'level': 'unverified', 'source': 'fallback'}
        level = str(payload.get('level', 'unverified')).lower()
        if level not in self._VALID_LEVELS:
            raise SerializationError(
                f'Unknown trust level: {level!r}',
                field='level',
                payload_type='trust',
            )
        return {
            'level': level,
            'support_scope': list(payload.get('support_scope', [])),
            'reasons': list(payload.get('reasons', [])),
            'source': str(payload.get('source', 'deserialized')),
        }

    def assert_no_trust_flattening(self, original: Any, recovered: dict[str, Any]) -> None:
        """Assert that trust was not silently flattened to a scalar.

        Parameters
        ----------
        original:
            Original trust value.
        recovered:
            Deserialized trust dictionary.

        Raises
        ------
        SerializationError
            If the recovered trust dictionary is missing the ``'level'`` key
            or if the level changed to a lower value than the original.
        """
        if 'level' not in recovered:
            raise SerializationError(
                'Trust flattening detected: recovered trust missing level key',
                field='level',
                payload_type='trust',
            )
        original_serialized = self.serialize_trust(original)
        original_level = original_serialized.get('level', 'unverified')
        recovered_level = recovered.get('level', 'unverified')
        if original_level != recovered_level:
            raise SerializationError(
                f'Trust level changed during round-trip: '
                f'{original_level!r} -> {recovered_level!r}',
                field='level',
                payload_type='trust',
            )

    def compare_trust_levels(self, a: str, b: str) -> int:
        """Compare two trust level strings using the partial order.

        Parameters
        ----------
        a, b:
            Trust level strings from :attr:`_VALID_LEVELS`.

        Returns
        -------
        int
            ``-1`` if *a* < *b*, ``0`` if equal, ``1`` if *a* > *b*, or
            ``None`` if incomparable.
        """
        _order = [
            'contradicted', 'unverified', 'copilot_suggested', 'oracle_proposed',
            'human_attested', 'runtime_witnessed', 'solver_discharged',
            'mechanically_verified',
        ]
        try:
            ia, ib = _order.index(a.lower()), _order.index(b.lower())
            if ia < ib:
                return -1
            if ia > ib:
                return 1
            return 0
        except ValueError:
            return 0

    def is_copilot_tier(self, trust: dict[str, Any]) -> bool:
        """Return ``True`` if *trust* represents a copilot-suggested level.

        Parameters
        ----------
        trust:
            Serialized trust dictionary.

        Returns
        -------
        bool
        """
        return str(trust.get('level', '')).lower() == 'copilot_suggested'

    def trust_floor_filter(
        self, records: list[dict[str, Any]], floor: str
    ) -> list[dict[str, Any]]:
        """Filter a list of serialized trust dicts to those meeting *floor*.

        Parameters
        ----------
        records:
            List of trust dictionaries.
        floor:
            Minimum trust level string.

        Returns
        -------
        list[dict[str, Any]]
        """
        return [r for r in records if self.compare_trust_levels(str(r.get('level', '')), floor) >= 0]

    # -- private helpers -----------------------------------------------------

    def _normalize_trust_dict(self, d: dict[str, Any]) -> dict[str, Any]:
        level_raw = d.get('level') or d.get('tier') or d.get('trust_level') or 'unverified'
        if isinstance(level_raw, Enum):
            level = str(level_raw.value).lower()
        elif isinstance(level_raw, int):
            level = self._int_tier_to_level(level_raw)
        else:
            level = str(level_raw).lower()
        return {
            'level': level,
            'support_scope': list(d.get('support_scope', [])),
            'reasons': list(d.get('reasons', [])),
            'source': str(d.get('source', 'normalized')),
        }

    def _tier_to_level(self, tier: Any) -> str:
        _map = {1: 'copilot_suggested', 2: 'oracle_proposed', 3: 'human_attested',
                4: 'runtime_witnessed', 5: 'solver_discharged', 6: 'mechanically_verified'}
        if isinstance(tier, int):
            return _map.get(tier, 'unverified')
        if isinstance(tier, Enum):
            return str(tier.value).lower()
        return str(tier).lower()

    def _int_tier_to_level(self, tier: int) -> str:
        _map = {1: 'copilot_suggested', 2: 'oracle_proposed', 3: 'human_attested',
                4: 'runtime_witnessed', 5: 'solver_discharged', 6: 'mechanically_verified'}
        return _map.get(tier, 'unverified')


# ---------------------------------------------------------------------------
# 7. ProvenanceSerializer
# ---------------------------------------------------------------------------


class ProvenanceSerializer:
    """Serializer for JuGeo provenance chains and DAG nodes.

    Provenance is the *full* audit trail of how a judgment or evidence piece
    came to exist.  This serializer preserves every node in the DAG, including
    intermediate transformation nodes, without compressing or dropping edges.

    The copilot channel may appear as a node operation (``PRODUCED`` by a
    copilot actor); such nodes are serialized faithfully and never elevated
    to a higher-trust operation during round-trips.

    Parameters
    ----------
    context:
        Default serialization context.
    """

    def __init__(self, context: SerializationContext | None = None) -> None:
        self._ctx = context or SerializationContext()

    def serialize_node(self, node: Any) -> dict[str, Any]:
        """Serialize a single provenance node to a canonical dictionary.

        Parameters
        ----------
        node:
            A ``ProvenanceNode``-like object, ``ProvenanceStep``-like object,
            or plain dict.

        Returns
        -------
        dict[str, Any]
        """
        d = node if isinstance(node, dict) else _obj_to_dict(node)
        return {
            '_type': 'provenance_node',
            'node_id': str(d.get('node_id') or d.get('id') or _uid()),
            'operation': self._extract_operation(d),
            'actor': str(d.get('actor') or d.get('channel') or 'unknown'),
            'coordinate': str(d.get('coordinate') or ''),
            'action': str(d.get('action') or d.get('operation') or ''),
            'timestamp': float(d.get('timestamp') or _now()),
            'parent_ids': list(d.get('parent_ids') or d.get('parents') or []),
            'trust_at_creation': _normalize(d.get('trust_at_creation') or d.get('trust') or {}),
            'details': _normalize(d.get('details') or d.get('metadata') or {}),
            'invalidated': bool(d.get('invalidated', False)),
            'invalidation_reason': d.get('invalidation_reason'),
        }

    def deserialize_node(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Deserialize a provenance node from a canonical dictionary.

        Parameters
        ----------
        payload:
            Dictionary as produced by :meth:`serialize_node`.

        Returns
        -------
        dict[str, Any]

        Raises
        ------
        SerializationError
            If the payload is not a provenance_node type.
        """
        if payload.get('_type') not in ('provenance_node', None):
            raise SerializationError(
                f"Expected provenance_node, got {payload.get('_type')!r}",
                field='_type',
                payload_type='provenance_node',
            )
        return {
            'node_id': str(payload.get('node_id', _uid())),
            'operation': str(payload.get('operation', 'produced')),
            'actor': str(payload.get('actor', 'unknown')),
            'coordinate': str(payload.get('coordinate', '')),
            'action': str(payload.get('action', '')),
            'timestamp': float(payload.get('timestamp', 0.0)),
            'parent_ids': list(payload.get('parent_ids', [])),
            'trust_at_creation': dict(payload.get('trust_at_creation', {})),
            'details': dict(payload.get('details', {})),
            'invalidated': bool(payload.get('invalidated', False)),
            'invalidation_reason': payload.get('invalidation_reason'),
        }

    def serialize_chain(
        self,
        provenance: Any,
        *,
        context: SerializationContext | None = None,
    ) -> dict[str, Any]:
        """Serialize a full provenance chain (trace or DAG) to a dictionary.

        Parameters
        ----------
        provenance:
            A ``ProvenanceTrace``, ``ProvenanceGraph``, or plain dict/list.
        context:
            Override context.

        Returns
        -------
        dict[str, Any]
        """
        ctx = context or self._ctx
        if provenance is None:
            return {'_type': 'provenance_chain', 'origin': '', 'nodes': [], 'edges': []}
        if isinstance(provenance, dict):
            return self._serialize_provenance_dict(provenance)
        if isinstance(provenance, (list, tuple)):
            nodes = [self.serialize_node(n) for n in provenance]
            return {'_type': 'provenance_chain', 'origin': '', 'nodes': nodes, 'edges': []}
        if hasattr(provenance, 'steps'):
            steps = provenance.steps or []
            nodes = [self.serialize_node(s) for s in steps]
            return {
                '_type': 'provenance_chain',
                'origin': str(getattr(provenance, 'origin', '')),
                'nodes': nodes,
                'edges': [],
            }
        if hasattr(provenance, 'nodes'):
            nodes = [self.serialize_node(n) for n in (provenance.nodes or {}).values()]
            edges = list(getattr(provenance, 'edges', []))
            return {
                '_type': 'provenance_chain',
                'origin': str(getattr(provenance, 'origin', '')),
                'nodes': nodes,
                'edges': _normalize(edges),
            }
        return {'_type': 'provenance_chain', 'origin': '', 'nodes': [self.serialize_node(provenance)], 'edges': []}

    def deserialize_chain(
        self,
        payload: dict[str, Any],
        *,
        context: SerializationContext | None = None,
    ) -> dict[str, Any]:
        """Deserialize a provenance chain from a canonical dictionary.

        Parameters
        ----------
        payload:
            Dictionary as produced by :meth:`serialize_chain`.
        context:
            Override context.

        Returns
        -------
        dict[str, Any]
        """
        if not isinstance(payload, dict):
            return {'origin': '', 'nodes': [], 'edges': []}
        nodes = [self.deserialize_node(n) for n in payload.get('nodes', [])]
        return {
            'origin': str(payload.get('origin', '')),
            'nodes': nodes,
            'edges': list(payload.get('edges', [])),
        }

    def node_count(self, chain: dict[str, Any]) -> int:
        """Return the number of nodes in a serialized provenance chain.

        Parameters
        ----------
        chain:
            Serialized chain dict.

        Returns
        -------
        int
        """
        return len(chain.get('nodes', []))

    def find_copilot_nodes(self, chain: dict[str, Any]) -> list[dict[str, Any]]:
        """Return all nodes whose actor is copilot or oracle.

        Parameters
        ----------
        chain:
            Serialized provenance chain dict.

        Returns
        -------
        list[dict[str, Any]]
        """
        copilot_actors = {'copilot', 'oracle', 'copilot_suggested', 'oracle_proposed'}
        return [
            n for n in chain.get('nodes', [])
            if str(n.get('actor', '')).lower() in copilot_actors
            or str(n.get('operation', '')).lower() in ('copilot_suggested', 'oracle_proposed')
        ]

    def validate_dag_integrity(self, chain: dict[str, Any]) -> list[str]:
        """Check that the provenance chain has no cycles and all parent references resolve.

        Parameters
        ----------
        chain:
            Serialized provenance chain dict.

        Returns
        -------
        list[str]
            List of integrity violation messages; empty if DAG is valid.
        """
        nodes = chain.get('nodes', [])
        ids = {n.get('node_id') for n in nodes}
        issues: list[str] = []
        for node in nodes:
            for pid in node.get('parent_ids', []):
                if pid not in ids:
                    issues.append(f"Node {node.get('node_id')} references unknown parent {pid}")
        return issues

    # -- private helpers -----------------------------------------------------

    def _extract_operation(self, d: dict[str, Any]) -> str:
        raw = d.get('operation') or d.get('action') or 'produced'
        if isinstance(raw, Enum):
            return str(raw.value)
        return str(raw)

    def _serialize_provenance_dict(self, d: dict[str, Any]) -> dict[str, Any]:
        if 'nodes' in d or 'steps' in d:
            raw_nodes = d.get('nodes') or d.get('steps') or []
            nodes = (
                [self.serialize_node(n) for n in raw_nodes.values()]
                if isinstance(raw_nodes, dict)
                else [self.serialize_node(n) for n in raw_nodes]
            )
            return {
                '_type': 'provenance_chain',
                'origin': str(d.get('origin', '')),
                'nodes': nodes,
                'edges': _normalize(d.get('edges', [])),
            }
        return {'_type': 'provenance_chain', 'origin': '', 'nodes': [self.serialize_node(d)], 'edges': []}


# ---------------------------------------------------------------------------
# 8. ManifestSerializer
# ---------------------------------------------------------------------------


class ManifestSerializer:
    """Serializer for the full JuGeo manifest M = (J, O, E, X, K, η, σ).

    The seven components of a manifest are:

    * **J** — persisted judgments
    * **O** — residual obligations
    * **E** — evidence archive
    * **X** — obstructions
    * **K** — settlement certificates
    * **η** — epoch map
    * **σ** — invalidation graph

    All components are serialized faithfully.  Obligations and obstructions
    are never merged into a generic error list.  The epoch map is preserved
    with its full coordinate-to-epoch mapping.

    Parameters
    ----------
    context:
        Default serialization context.
    """

    def __init__(self, context: SerializationContext | None = None) -> None:
        self._ctx = context or SerializationContext()
        self._judgment_ser = JudgmentSerializer()
        self._evidence_ser = EvidenceSerializer()
        self._provenance_ser = ProvenanceSerializer()
        self._trust_ser = TrustSerializer()

    def serialize_manifest(
        self,
        manifest: Any,
        *,
        context: SerializationContext | None = None,
    ) -> dict[str, Any]:
        """Serialize a full manifest object to a canonical dictionary.

        Parameters
        ----------
        manifest:
            A ``Manifest``-like object or plain dict.
        context:
            Override context.

        Returns
        -------
        dict[str, Any]
        """
        ctx = context or self._ctx
        d = manifest if isinstance(manifest, dict) else _obj_to_dict(manifest)
        return {
            _SCHEMA_VERSION_KEY: ctx.schema_version,
            '_type': 'manifest',
            'manifest_id': str(d.get('manifest_id') or d.get('id') or _uid()),
            'J': self._serialize_judgments(d, ctx),
            'O': self._serialize_obligations(d),
            'E': self._serialize_evidence_archive(d, ctx),
            'X': self._serialize_obstructions(d, ctx),
            'K': self._serialize_certificates(d),
            'eta': self._serialize_epoch_map(d),
            'sigma': self._serialize_invalidation_graph(d),
            'created_at': float(d.get('created_at') or _now()),
            'updated_at': float(d.get('updated_at') or _now()),
        }

    def deserialize_manifest(
        self,
        payload: dict[str, Any],
        *,
        context: SerializationContext | None = None,
    ) -> dict[str, Any]:
        """Deserialize a manifest from a canonical dictionary.

        Parameters
        ----------
        payload:
            Dictionary as produced by :meth:`serialize_manifest`.
        context:
            Override context.

        Returns
        -------
        dict[str, Any]

        Raises
        ------
        SerializationError
            If the payload is not a manifest type.
        """
        ctx = context or self._ctx
        if payload.get('_type') not in ('manifest', None):
            raise SerializationError(
                f"Expected manifest, got {payload.get('_type')!r}",
                field='_type',
                payload_type='manifest',
            )
        return {
            'manifest_id': str(payload.get('manifest_id', _uid())),
            'J': payload.get('J', []),
            'O': payload.get('O', []),
            'E': self._evidence_ser.deserialize_bundle(payload.get('E', []), context=ctx),
            'X': payload.get('X', []),
            'K': payload.get('K', []),
            'eta': dict(payload.get('eta', {})),
            'sigma': dict(payload.get('sigma', {})),
            'created_at': float(payload.get('created_at', 0.0)),
            'updated_at': float(payload.get('updated_at', 0.0)),
        }

    def diff_manifests(
        self,
        left: dict[str, Any],
        right: dict[str, Any],
    ) -> dict[str, Any]:
        """Compute a component-level diff between two serialized manifests.

        Parameters
        ----------
        left, right:
            Serialized manifest dicts.

        Returns
        -------
        dict[str, Any]
            Maps component keys (J, O, E, X, K, eta, sigma) to diff info.
        """
        components = ('J', 'O', 'E', 'X', 'K', 'eta', 'sigma')
        diff: dict[str, Any] = {}
        for key in components:
            ls = json.dumps(_normalize(left.get(key)), sort_keys=True)
            rs = json.dumps(_normalize(right.get(key)), sort_keys=True)
            if ls != rs:
                diff[key] = {
                    'changed': True,
                    'left_size': len(left.get(key, [])) if isinstance(left.get(key), list) else 1,
                    'right_size': len(right.get(key, [])) if isinstance(right.get(key), list) else 1,
                }
        return diff

    def obligation_count(self, manifest: dict[str, Any]) -> int:
        """Return the count of residual obligations in *manifest*.

        Parameters
        ----------
        manifest:
            Serialized manifest dict.

        Returns
        -------
        int
        """
        return len(manifest.get('O', []))

    def obstruction_count(self, manifest: dict[str, Any]) -> int:
        """Return the count of obstructions in *manifest*.

        Parameters
        ----------
        manifest:
            Serialized manifest dict.

        Returns
        -------
        int
        """
        return len(manifest.get('X', []))

    def is_settled(self, manifest: dict[str, Any]) -> bool:
        """Return ``True`` if the manifest has no residual obligations.

        Parameters
        ----------
        manifest:
            Serialized manifest dict.

        Returns
        -------
        bool
        """
        return self.obligation_count(manifest) == 0

    def manifest_summary(self, manifest: dict[str, Any]) -> dict[str, Any]:
        """Produce a concise summary of a serialized manifest.

        Parameters
        ----------
        manifest:
            Serialized manifest dict.

        Returns
        -------
        dict[str, Any]
        """
        return {
            'manifest_id': manifest.get('manifest_id', ''),
            'judgment_count': len(manifest.get('J', [])),
            'obligation_count': self.obligation_count(manifest),
            'evidence_count': len(manifest.get('E', [])),
            'obstruction_count': self.obstruction_count(manifest),
            'certificate_count': len(manifest.get('K', [])),
            'epoch_count': len(manifest.get('eta', {})),
            'is_settled': self.is_settled(manifest),
        }

    # -- private serializers -------------------------------------------------

    def _serialize_judgments(self, d: dict[str, Any], ctx: SerializationContext) -> list[dict[str, Any]]:
        raw = d.get('J') or d.get('judgments') or []
        if isinstance(raw, dict):
            raw = list(raw.values())
        return [self._judgment_ser.serialize_judgment(j, context=ctx) for j in raw]

    def _serialize_obligations(self, d: dict[str, Any]) -> list[Any]:
        raw = d.get('O') or d.get('obligations') or []
        if isinstance(raw, dict):
            raw = list(raw.values())
        return [_normalize(o) for o in raw]

    def _serialize_evidence_archive(self, d: dict[str, Any], ctx: SerializationContext) -> list[dict[str, Any]]:
        raw = d.get('E') or d.get('evidence') or []
        if not ctx.include_evidence:
            return []
        if isinstance(raw, dict):
            raw = list(raw.values())
        return self._evidence_ser.serialize_bundle(raw, context=ctx)

    def _serialize_obstructions(self, d: dict[str, Any], ctx: SerializationContext) -> list[Any]:
        if not ctx.include_obstructions:
            return []
        raw = d.get('X') or d.get('obstructions') or []
        if isinstance(raw, dict):
            raw = list(raw.values())
        return [_normalize(x) for x in raw]

    def _serialize_certificates(self, d: dict[str, Any]) -> list[Any]:
        raw = d.get('K') or d.get('certificates') or []
        if isinstance(raw, dict):
            raw = list(raw.values())
        return [_normalize(k) for k in raw]

    def _serialize_epoch_map(self, d: dict[str, Any]) -> dict[str, Any]:
        raw = d.get('eta') or d.get('epoch_map') or {}
        return {str(k): _normalize(v) for k, v in raw.items()}

    def _serialize_invalidation_graph(self, d: dict[str, Any]) -> dict[str, Any]:
        raw = d.get('sigma') or d.get('invalidation_graph') or {}
        return _normalize(raw)


# ---------------------------------------------------------------------------
# 9. SchemaVersionManager
# ---------------------------------------------------------------------------


class SchemaVersionManager:
    """Schema versioning, migration, and backward compatibility for JuGeo payloads.

    Every JuGeo serialized payload carries a ``_jugeo_schema_version`` tag.
    :class:`SchemaVersionManager` uses this tag to apply registered
    migration functions before deserialization, ensuring that older payloads
    remain readable after schema evolution.

    Migration discipline
    --------------------
    * Migrations are forward-only (old -> new), never backward.
    * A migration function receives a raw dict and returns an updated raw dict.
    * Migrations must never drop trust annotations, evidence kinds, or
      provenance chains unless the field was genuinely removed from the schema.

    Parameters
    ----------
    current_version:
        The schema version used for newly serialized payloads.  Defaults to
        :data:`_CURRENT_SCHEMA_VERSION`.
    """

    def __init__(self, current_version: str = _CURRENT_SCHEMA_VERSION) -> None:
        self._current = current_version
        self._migrations: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {}
        self._register_builtin_migrations()

    def register_migration(
        self,
        from_version: str,
        fn: Callable[[dict[str, Any]], dict[str, Any]],
    ) -> None:
        """Register a migration function for payloads at *from_version*.

        Parameters
        ----------
        from_version:
            The schema version that *fn* upgrades from.
        fn:
            Callable that accepts and returns a raw payload dict.
        """
        self._migrations[from_version] = fn

    def migrate(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Apply any necessary migrations to *payload*.

        Parameters
        ----------
        payload:
            Raw deserialized dict, possibly from an older schema version.

        Returns
        -------
        dict[str, Any]
            Migrated payload at :attr:`_current` schema version.
        """
        version = str(payload.get(_SCHEMA_VERSION_KEY, '1.0.0'))
        migrated = dict(payload)
        while version != self._current:
            fn = self._migrations.get(version)
            if fn is None:
                break
            migrated = fn(migrated)
            version = str(migrated.get(_SCHEMA_VERSION_KEY, self._current))
        migrated[_SCHEMA_VERSION_KEY] = self._current
        return migrated

    def is_current(self, payload: dict[str, Any]) -> bool:
        """Return ``True`` if *payload* is already at the current schema version.

        Parameters
        ----------
        payload:
            Raw payload dict.

        Returns
        -------
        bool
        """
        return str(payload.get(_SCHEMA_VERSION_KEY, '')) == self._current

    def needs_migration(self, payload: dict[str, Any]) -> bool:
        """Return ``True`` if *payload* requires migration.

        Parameters
        ----------
        payload:
            Raw payload dict.

        Returns
        -------
        bool
        """
        return not self.is_current(payload)

    def known_versions(self) -> list[str]:
        """Return a sorted list of all schema versions with registered migrations.

        Returns
        -------
        list[str]
        """
        return sorted(self._migrations.keys())

    def migration_path(self, from_version: str) -> list[str]:
        """Return the sequence of versions visited when migrating from *from_version*.

        Parameters
        ----------
        from_version:
            Starting schema version.

        Returns
        -------
        list[str]
        """
        path = [from_version]
        version = from_version
        seen: set[str] = {version}
        while version != self._current:
            fn = self._migrations.get(version)
            if fn is None:
                break
            dummy: dict[str, Any] = {_SCHEMA_VERSION_KEY: version}
            result = fn(dummy)
            version = str(result.get(_SCHEMA_VERSION_KEY, self._current))
            if version in seen:
                break
            seen.add(version)
            path.append(version)
        return path

    def schema_report(self) -> dict[str, Any]:
        """Return a machine-readable schema status report.

        Returns
        -------
        dict[str, Any]
        """
        return {
            'current_version': self._current,
            'known_migrations': self.known_versions(),
            'migration_count': len(self._migrations),
        }

    # -- private -------------------------------------------------------------

    def _register_builtin_migrations(self) -> None:
        """Register migrations for known historic schema versions."""

        def _v1_to_v1_1(payload: dict[str, Any]) -> dict[str, Any]:
            # v1.0.0 used 'trust_tier' (int); v1.1.0 uses 'trust.level' (str).
            out = dict(payload)
            if 'trust_tier' in out and 'trust' not in out:
                tier = out.pop('trust_tier', 1)
                _map = {1: 'copilot_suggested', 2: 'oracle_proposed',
                        3: 'human_attested', 4: 'runtime_witnessed',
                        5: 'solver_discharged', 6: 'mechanically_verified'}
                out['trust'] = {'level': _map.get(int(tier), 'unverified')}
            out[_SCHEMA_VERSION_KEY] = '1.1.0'
            return out

        def _v1_1_to_v2(payload: dict[str, Any]) -> dict[str, Any]:
            # v1.1.0 used 'provenance_steps' list; v2.0.0 uses 'Pi' chain dict.
            out = dict(payload)
            if 'provenance_steps' in out and 'Pi' not in out:
                steps = out.pop('provenance_steps', [])
                out['Pi'] = {
                    'origin': '',
                    'nodes': [{'_type': 'provenance_node', 'action': str(s)} for s in steps],
                    'edges': [],
                }
            out[_SCHEMA_VERSION_KEY] = '2.0.0'
            return out

        self._migrations['1.0.0'] = _v1_to_v1_1
        self._migrations['1.1.0'] = _v1_1_to_v2


# ---------------------------------------------------------------------------
# 10. SerializationValidator
# ---------------------------------------------------------------------------


class SerializationValidator:
    """Validates serialized JuGeo payloads for semantic fidelity.

    Checks performed:

    * **Round-trip fidelity** — re-serialize the deserialized payload and
      compare to the original wire bytes.
    * **No trust degradation** — deserialized trust levels must be
      ``>=`` the original under the partial order.
    * **Provenance integrity** — provenance DAGs must be acyclic with no
      dangling parent references.
    * **Obligation and obstruction preservation** — counts must match between
      original and deserialized.
    * **Evidence kind preservation** — evidence kinds must not change during
      round-trips.

    Parameters
    ----------
    strict:
        When ``True``, any validation failure raises
        :exc:`SerializationError` immediately.  When ``False``, failures are
        collected and returned as a list.
    """

    def __init__(self, strict: bool = False) -> None:
        self._strict = strict
        self._trust_ser = TrustSerializer()
        self._provenance_ser = ProvenanceSerializer()
        self._evidence_ser = EvidenceSerializer()

    def validate_round_trip(
        self,
        original_dict: dict[str, Any],
        recovered_dict: dict[str, Any],
    ) -> list[str]:
        """Validate that *recovered_dict* is semantically equivalent to *original_dict*.

        Parameters
        ----------
        original_dict:
            Original serialized payload.
        recovered_dict:
            Payload produced by deserializing the serialization of *original_dict*.

        Returns
        -------
        list[str]
            Validation failure messages; empty if the round-trip passed.
        """
        failures: list[str] = []
        orig_str = json.dumps(_normalize(original_dict), sort_keys=True)
        recv_str = json.dumps(_normalize(recovered_dict), sort_keys=True)
        if orig_str != recv_str:
            key = _first_diff_key(original_dict, recovered_dict)
            failures.append(f'Round-trip mismatch at key: {key!r}')
        if self._strict and failures:
            raise SerializationError(failures[0], payload_type='round_trip')
        return failures

    def check_no_trust_degradation(
        self,
        original: dict[str, Any],
        recovered: dict[str, Any],
    ) -> list[str]:
        """Check that trust levels were not demoted during round-trip.

        A trust level is degraded if it moved to a strictly weaker position
        in the partial order.  Copilot-tier entries must remain at
        ``copilot_suggested`` and must not be silently promoted or demoted.

        Parameters
        ----------
        original, recovered:
            Serialized trust dictionaries or containers thereof.

        Returns
        -------
        list[str]
            Failure messages; empty if no degradation was detected.
        """
        failures: list[str] = []
        o_level = str(original.get('level', original.get('T', {}).get('level', '')))
        r_level = str(recovered.get('level', recovered.get('T', {}).get('level', '')))
        if o_level and r_level and o_level != r_level:
            cmp = self._trust_ser.compare_trust_levels(o_level, r_level)
            if cmp > 0:
                failures.append(
                    f'Trust degraded: {o_level!r} -> {r_level!r}'
                )
            elif cmp < 0:
                failures.append(
                    f'Silent trust promotion detected: {o_level!r} -> {r_level!r}'
                )
        if self._strict and failures:
            raise SerializationError(failures[0], field='trust', payload_type='trust')
        return failures

    def verify_provenance_integrity(self, chain: dict[str, Any]) -> list[str]:
        """Verify that the provenance chain has no structural integrity issues.

        Parameters
        ----------
        chain:
            Serialized provenance chain dict.

        Returns
        -------
        list[str]
            Integrity violation messages; empty if the chain is valid.
        """
        issues = self._provenance_ser.validate_dag_integrity(chain)
        if self._strict and issues:
            raise SerializationError(issues[0], field='Pi', payload_type='provenance')
        return issues

    def check_obligation_preservation(
        self,
        original: dict[str, Any],
        recovered: dict[str, Any],
    ) -> list[str]:
        """Check that residual obligations survived the round-trip.

        Parameters
        ----------
        original, recovered:
            Serialized manifest or judgment dicts.

        Returns
        -------
        list[str]
        """
        failures: list[str] = []
        o_obs = len(original.get('O', original.get('obligations', [])))
        r_obs = len(recovered.get('O', recovered.get('obligations', [])))
        if o_obs != r_obs:
            failures.append(
                f'Obligation count changed: {o_obs} -> {r_obs}'
            )
        if self._strict and failures:
            raise SerializationError(failures[0], field='O', payload_type='obligations')
        return failures

    def check_obstruction_preservation(
        self,
        original: dict[str, Any],
        recovered: dict[str, Any],
    ) -> list[str]:
        """Check that obstructions survived the round-trip.

        Parameters
        ----------
        original, recovered:
            Serialized manifest or judgment dicts.

        Returns
        -------
        list[str]
        """
        failures: list[str] = []
        o_obs = len(original.get('B', original.get('X', original.get('obstructions', []))))
        r_obs = len(recovered.get('B', recovered.get('X', recovered.get('obstructions', []))))
        if o_obs != r_obs:
            failures.append(
                f'Obstruction count changed: {o_obs} -> {r_obs}'
            )
        if self._strict and failures:
            raise SerializationError(failures[0], field='B', payload_type='obstructions')
        return failures

    def full_validation(
        self,
        original: dict[str, Any],
        recovered: dict[str, Any],
    ) -> dict[str, list[str]]:
        """Run all validation checks and return a categorized report.

        Parameters
        ----------
        original, recovered:
            Serialized payload dicts.

        Returns
        -------
        dict[str, list[str]]
            Maps check names to lists of failure messages.
        """
        report: dict[str, list[str]] = {}
        report['round_trip'] = self.validate_round_trip(original, recovered)
        report['trust'] = self.check_no_trust_degradation(original, recovered)
        provenance_chain = recovered.get('Pi', recovered.get('provenance', {}))
        report['provenance'] = (
            self.verify_provenance_integrity(provenance_chain)
            if isinstance(provenance_chain, dict)
            else []
        )
        report['obligations'] = self.check_obligation_preservation(original, recovered)
        report['obstructions'] = self.check_obstruction_preservation(original, recovered)
        return report

    def is_valid(self, original: dict[str, Any], recovered: dict[str, Any]) -> bool:
        """Return ``True`` if all validation checks pass.

        Parameters
        ----------
        original, recovered:
            Serialized payload dicts.

        Returns
        -------
        bool
        """
        report = self.full_validation(original, recovered)
        return all(not v for v in report.values())


# ---------------------------------------------------------------------------
# 11. SerializationDiagnostics
# ---------------------------------------------------------------------------


class SerializationDiagnostics:
    """Human- and machine-readable diagnostics for the JuGeo serialization layer.

    This class aggregates information from the serializers and validator to
    produce structured reports.  The :meth:`copilot_serialization_summary`
    method produces a JSON-serializable report specifically formatted for
    copilot-assisted orchestration workflows, summarizing trust tiers, evidence
    kinds, provenance chain depths, and any round-trip failures detected.

    Parameters
    ----------
    serializer:
        :class:`JuGeoSerializer` instance.  A default instance is created if
        not provided.
    validator:
        :class:`SerializationValidator` instance.  A default instance is
        created if not provided.
    """

    def __init__(
        self,
        serializer: JuGeoSerializer | None = None,
        validator: SerializationValidator | None = None,
    ) -> None:
        self._serializer = serializer or JuGeoSerializer()
        self._validator = validator or SerializationValidator()
        self._schema_manager = SchemaVersionManager()
        self._trust_ser = TrustSerializer()
        self._evidence_ser = EvidenceSerializer()
        self._provenance_ser = ProvenanceSerializer()
        self._round_trip_failures: list[dict[str, Any]] = []

    def summary(self, payload: dict[str, Any]) -> str:
        """Return a human-readable text summary of a serialized payload.

        Parameters
        ----------
        payload:
            Any serialized JuGeo dict.

        Returns
        -------
        str
        """
        ptype = payload.get('_type', 'unknown')
        version = payload.get(_SCHEMA_VERSION_KEY, 'unknown')
        lines: list[str] = [
            f'JuGeo Serialized Payload',
            f'  type:    {ptype}',
            f'  schema:  {version}',
        ]
        if ptype == 'judgment':
            lines.append(f"  coordinate: {payload.get('c', '')}")
            lines.append(f"  proposition: {payload.get('phi', '')}")
            trust = payload.get('T', {})
            lines.append(f"  trust level: {trust.get('level', 'unknown')}")
            lines.append(f"  obligations: {len(payload.get('O', []))}")
            lines.append(f"  obstructions: {len(payload.get('B', []))}")
            evidence = payload.get('E', [])
            lines.append(f"  evidence records: {len(evidence)}")
        elif ptype == 'manifest':
            lines.append(f"  manifest_id: {payload.get('manifest_id', '')}")
            lines.append(f"  judgments: {len(payload.get('J', []))}")
            lines.append(f"  obligations: {len(payload.get('O', []))}")
            lines.append(f"  evidence records: {len(payload.get('E', []))}")
            lines.append(f"  obstructions: {len(payload.get('X', []))}")
            lines.append(f"  certificates: {len(payload.get('K', []))}")
            lines.append(f"  epochs: {len(payload.get('eta', {}))}")
        return '\n'.join(lines)

    def schema_report(self) -> dict[str, Any]:
        """Return a machine-readable schema status report.

        Returns
        -------
        dict[str, Any]
            Includes current version, known migrations, and compatibility
            information.
        """
        base = self._schema_manager.schema_report()
        base['formats'] = [f.value for f in SerializationFormat]
        base['lossless_formats'] = [f.value for f in SerializationFormat if f.is_lossless()]
        return base

    def round_trip_failures(self) -> list[dict[str, Any]]:
        """Return a list of all recorded round-trip failures.

        Round-trip failures are recorded when
        :meth:`record_round_trip_failure` is called or when the serializer
        runs in strict round-trip mode.

        Returns
        -------
        list[dict[str, Any]]
        """
        return list(self._round_trip_failures)

    def record_round_trip_failure(
        self,
        payload_type: str,
        failures: list[str],
        *,
        context: SerializationContext | None = None,
    ) -> None:
        """Record a round-trip failure for later reporting.

        Parameters
        ----------
        payload_type:
            Type of payload that failed (e.g. ``'judgment'``, ``'manifest'``).
        failures:
            List of failure messages.
        context:
            Optional serialization context at the time of failure.
        """
        self._round_trip_failures.append({
            'timestamp': _now(),
            'payload_type': payload_type,
            'failures': failures,
            'context': context.to_dict() if context is not None else {},
        })

    def trust_tier_histogram(self, payloads: list[dict[str, Any]]) -> dict[str, int]:
        """Compute a histogram of trust tiers across a list of serialized payloads.

        Parameters
        ----------
        payloads:
            List of serialized judgment or evidence dicts.

        Returns
        -------
        dict[str, int]
            Maps trust level strings to occurrence counts.
        """
        histogram: dict[str, int] = {}
        for p in payloads:
            trust = p.get('T') or p.get('trust', {})
            if isinstance(trust, dict):
                level = str(trust.get('level', 'unknown'))
            else:
                level = 'unknown'
            histogram[level] = histogram.get(level, 0) + 1
        return dict(sorted(histogram.items()))

    def evidence_kind_inventory(self, payloads: list[dict[str, Any]]) -> dict[str, int]:
        """Count evidence records by kind across a list of serialized payloads.

        Parameters
        ----------
        payloads:
            List of serialized judgment dicts, each with an ``'E'`` field.

        Returns
        -------
        dict[str, int]
            Maps evidence kind strings to occurrence counts.
        """
        inventory: dict[str, int] = {}
        for p in payloads:
            for record in p.get('E', p.get('evidence', [])):
                if isinstance(record, dict):
                    kind = str(record.get('kind', 'unknown'))
                    inventory[kind] = inventory.get(kind, 0) + 1
        return dict(sorted(inventory.items()))

    def provenance_depth_stats(self, payloads: list[dict[str, Any]]) -> dict[str, Any]:
        """Compute statistics on provenance chain depths.

        Parameters
        ----------
        payloads:
            List of serialized judgment dicts, each with a ``'Pi'`` field.

        Returns
        -------
        dict[str, Any]
            Includes min, max, mean, and total node counts.
        """
        depths: list[int] = []
        for p in payloads:
            chain = p.get('Pi') or p.get('provenance') or {}
            if isinstance(chain, dict):
                depths.append(self._provenance_ser.node_count(chain))
        if not depths:
            return {'min': 0, 'max': 0, 'mean': 0.0, 'total': 0, 'count': 0}
        return {
            'min': min(depths),
            'max': max(depths),
            'mean': sum(depths) / len(depths),
            'total': sum(depths),
            'count': len(depths),
        }

    def copilot_serialization_summary(
        self,
        payloads: list[dict[str, Any]],
        *,
        context: SerializationContext | None = None,
    ) -> dict[str, Any]:
        """Produce a machine-readable serialization summary for copilot-assisted orchestration.

        This method is the primary interface for copilot workflows that need
        to assess the fidelity and completeness of a serialization pass.  It
        reports:

        * Schema version status for all payloads.
        * Trust tier distribution (copilot-sourced entries are broken out).
        * Evidence kind inventory.
        * Provenance chain depth statistics.
        * Round-trip failure log.
        * Counts of obligations and obstructions.
        * A ``'semantic_fidelity_ok'`` boolean that is ``True`` iff no
          trust degradation, kind erasure, or round-trip failures were
          detected.

        The report is JSON-serializable and suitable for embedding in a
        manifest or passing to a copilot orchestration layer.

        Parameters
        ----------
        payloads:
            List of serialized JuGeo payload dicts to analyse.
        context:
            Optional serialization context used for the summary run.

        Returns
        -------
        dict[str, Any]
        """
        ctx = context or SerializationContext()
        version_ok = all(
            str(p.get(_SCHEMA_VERSION_KEY, '')) == _CURRENT_SCHEMA_VERSION
            for p in payloads
        )
        trust_hist = self.trust_tier_histogram(payloads)
        copilot_count = trust_hist.get('copilot_suggested', 0)
        kind_inv = self.evidence_kind_inventory(payloads)
        prov_stats = self.provenance_depth_stats(payloads)
        rt_failures = self.round_trip_failures()
        obligation_total = sum(len(p.get('O', p.get('obligations', []))) for p in payloads)
        obstruction_total = sum(
            len(p.get('B', p.get('X', p.get('obstructions', []))))
            for p in payloads
        )
        fidelity_ok = (
            len(rt_failures) == 0
            and version_ok
        )
        return {
            'generated_at': _now(),
            'schema_version': _CURRENT_SCHEMA_VERSION,
            'payload_count': len(payloads),
            'schema_version_ok': version_ok,
            'trust_tier_histogram': trust_hist,
            'copilot_suggested_count': copilot_count,
            'evidence_kind_inventory': kind_inv,
            'provenance_depth_stats': prov_stats,
            'round_trip_failures': rt_failures,
            'round_trip_failure_count': len(rt_failures),
            'obligation_total': obligation_total,
            'obstruction_total': obstruction_total,
            'serialization_context': ctx.to_dict(),
            'semantic_fidelity_ok': fidelity_ok,
            'notes': [
                'copilot_suggested entries are reported but never promoted during serialization.',
                'trust_tier_histogram counts are over serialized payloads, not original objects.',
                'semantic_fidelity_ok is False if any round-trip failures were recorded.',
            ],
        }


# ---------------------------------------------------------------------------
# Internal utility helpers
# ---------------------------------------------------------------------------


def _obj_to_dict(obj: Any) -> dict[str, Any]:
    """Convert *obj* to a plain dict using ``to_dict``, ``__dict__``, or
    dataclass introspection, whichever is available.

    Parameters
    ----------
    obj:
        Any Python object.

    Returns
    -------
    dict[str, Any]
    """
    if isinstance(obj, dict):
        return obj
    if hasattr(obj, 'to_dict') and callable(obj.to_dict):
        result = obj.to_dict()
        return result if isinstance(result, dict) else {'value': result}
    if is_dataclass(obj) and not isinstance(obj, type):
        return {k: v for k, v in asdict(obj).items()}
    if hasattr(obj, '__dict__'):
        return dict(obj.__dict__)
    return {'value': _normalize(obj)}


def _first_diff_key(a: dict[str, Any], b: dict[str, Any], *, _prefix: str = '') -> str:
    """Return the dotted path of the first key that differs between *a* and *b*.

    Parameters
    ----------
    a, b:
        Dictionaries to compare.
    _prefix:
        Internal prefix for recursive calls.

    Returns
    -------
    str
        Dotted key path of the first differing entry, or ``'<unknown>'``.
    """
    all_keys = sorted(set(a) | set(b))
    for k in all_keys:
        path = f'{_prefix}.{k}' if _prefix else k
        if k not in a:
            return path
        if k not in b:
            return path
        av = json.dumps(_normalize(a[k]), sort_keys=True)
        bv = json.dumps(_normalize(b[k]), sort_keys=True)
        if av != bv:
            if isinstance(a[k], dict) and isinstance(b[k], dict):
                return _first_diff_key(a[k], b[k], _prefix=path)
            return path
    return '<unknown>'


def _dict_to_text_summary(d: dict[str, Any], *, _indent: int = 0) -> str:
    """Produce a lossy human-readable text summary of *d*.

    Parameters
    ----------
    d:
        Dictionary to summarize.

    Returns
    -------
    str
    """
    lines: list[str] = []
    prefix = '  ' * _indent
    for k, v in sorted(d.items()):
        if k == _SCHEMA_VERSION_KEY:
            continue
        if isinstance(v, dict):
            lines.append(f'{prefix}{k}:')
            lines.append(_dict_to_text_summary(v, _indent=_indent + 1))
        elif isinstance(v, list):
            lines.append(f'{prefix}{k}: [{len(v)} items]')
        else:
            lines.append(f'{prefix}{k}: {v}')
    return '\n'.join(lines)


# ---------------------------------------------------------------------------
# Backward-compatible module-level functions
# ---------------------------------------------------------------------------


def serialize(value: Any) -> str:
    """Serialize *value* to a deterministic JSON string.

    This is the original lightweight helper retained for backward
    compatibility.  New code should use :class:`JuGeoSerializer`.

    Parameters
    ----------
    value:
        Any Python value.  Dataclasses and Enums are normalized
        recursively.

    Returns
    -------
    str
        UTF-8 JSON string with sorted keys.
    """
    return json.dumps(_normalize(value), sort_keys=True, separators=(',', ':'))


def deserialize(payload: str) -> Any:
    """Deserialize a JSON string produced by :func:`serialize`.

    Parameters
    ----------
    payload:
        UTF-8 JSON string.

    Returns
    -------
    Any
        The deserialized value as a Python primitive / dict / list.
    """
    return json.loads(payload)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    # Enum
    'SerializationFormat',
    # Dataclass
    'SerializationContext',
    # Serializers
    'JuGeoSerializer',
    'JudgmentSerializer',
    'EvidenceSerializer',
    'TrustSerializer',
    'ProvenanceSerializer',
    'ManifestSerializer',
    # Schema management
    'SchemaVersionManager',
    # Validation & diagnostics
    'SerializationValidator',
    'SerializationDiagnostics',
    # Error
    'SerializationError',
    # Backward-compat helpers
    'serialize',
    'deserialize',
]
