"""Structured failure surfaces shared across JuGeo.

copilot compatibility: these records are shared with later semantic subsystems
and automation layers, including copilot-backed proposal channels.

The theory2 specification treats failures as first-class semantic objects rather
than as disposable status codes.  This module therefore keeps the public error
surface explicit, dependency-light, and rich enough that higher-level JuGeo
systems can preserve provenance, trust accounting, and local-to-global repair
planning.

Governing design principles from ``preliminaries/theory2.tex``:

* **Obstructions are persistent semantic objects** — an obstruction records
  which coordinate it lives at, which admissibility condition was violated,
  what evidence was present, what the repair frontier looks like, and what
  downstream obligations are affected.  Obstructions should be treated as
  cohomology classes, not ephemeral error logs.

* **No silent trust promotion** — if a clause is already violated, adding
  evidence may not silently relabel it satisfied.  If residual, additional
  evidence may discharge or contradict but must not erase the fact that it was
  unresolved on a smaller coordinate.

* **Evidence plurality** — different clauses belong to different support
  channels (arithmetic to solver discharge, relational claims to provers,
  resource claims to runtime witnesses, semantic claims to controlled oracles).
  Error payloads must name which channel was involved so that later routing
  does not silently widen jurisdiction.

* **Certificates are faithful projections** — they must explicitly preserve
  partially established clauses, fragility declarations, and support scope.
  An error that prevents certification must explain *what* is missing, not
  merely that certification failed.

Public types
------------
:class:`FailureScope`
    Enum naming the subsystem where a failure originated.

:class:`FailureClassification`
    Enum naming the broad failure family so routing decisions can be made
    without parsing free-text messages.

:class:`EvidenceFamily`
    Enum naming the evidence channel involved in the failure, so that
    downstream trust accounting can preserve kind.

:class:`RepairHint`
    Immutable record suggesting one concrete repair action.

:class:`ObstructionRecord`
    Full theory2 obstruction object ``(c, κ, E, R, Δ)`` extended with
    provenance and trust context.

:class:`StructuredFailure`
    Immutable obstruction payload used for persistence, serialization,
    testing, and inter-subsystem transport.

:class:`JuGeoError`
    Package-level exception that can cross Python call boundaries while
    carrying a full :class:`StructuredFailure` payload.

:class:`FailureChain`
    Ordered collection of related failures that preserves overlap structure
    rather than collapsing into a single message.

:class:`FailureFilter`
    Predicate object for selecting failures by scope, classification,
    evidence family, coordinate prefix, or custom predicate.

Public functions
----------------
:func:`classify_error`
    Maps an arbitrary Python exception to a :class:`FailureClassification`.

:func:`as_failure_payload`
    Wraps any exception into a JSON-serializable dict via :class:`StructuredFailure`.

:func:`raise_with_scope`
    Convenience raiser that builds a :class:`StructuredFailure` and raises
    :class:`JuGeoError` in one call.

:func:`chain_failures`
    Combines multiple failures into a :class:`FailureChain` without
    collapsing overlap structure.

:func:`filter_failures`
    Selects failures from a sequence using a :class:`FailureFilter`.

:func:`merge_repair_hints`
    Merges repair hints from multiple failures, deduplicating by action
    and preserving the strongest priority.
"""

from __future__ import annotations

import json
import traceback as traceback_module
from dataclasses import dataclass, field, replace
from enum import Enum, IntEnum
from types import MappingProxyType, TracebackType
from typing import (
    Any,
    Callable,
    Final,
    Iterable,
    Mapping,
    NoReturn,
    Sequence,
)

# ---------------------------------------------------------------------------
# JSON type aliases used throughout the error surface
# ---------------------------------------------------------------------------

JsonScalar = None | bool | int | float | str
JsonValue = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]
FrozenJsonValue = (
    JsonScalar | tuple["FrozenJsonValue", ...] | Mapping[str, "FrozenJsonValue"]
)

# ---------------------------------------------------------------------------
# Canonical constants
# ---------------------------------------------------------------------------

CANONICAL_EVIDENCE_FAMILIES: Final[frozenset[str]] = frozenset(
    {"proof", "solver", "runtime", "semantic", "human", "mixed"}
)

CANONICAL_CLASSIFICATIONS: Final[frozenset[str]] = frozenset(
    {
        "local_repair",
        "cover_refinement",
        "treaty_change",
        "theory_extension",
        "trust_violation",
        "jurisdiction_exceeded",
        "descent_obstruction",
        "encoding_mismatch",
        "replay_invalidation",
        "unclassified",
    }
)


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class FailureScope(str, Enum):
    """Subsystem where a failure originated.

    The value set mirrors the shared subsystem order declared in
    ``package_manifest.enumerate_subsystems()``.
    """

    ROOT = "root"
    CONFIGURATION = "configuration"
    AUTHORITY = "authority"
    GEOMETRY = "geometry"
    JUDGMENT = "judgment"
    EVIDENCE = "evidence"
    PACK = "pack"
    SOLVER = "solver"
    RUNTIME = "runtime"
    GENERATION = "generation"
    ORCHESTRATION = "orchestration"
    IDEATION = "ideation"
    INTERFACE = "interface"
    CHAPTER = "chapter"
    UNKNOWN = "unknown"


FailureScope.LOCAL = FailureScope.GEOMETRY


class FailureClassification(str, Enum):
    """Broad failure family for routing decisions.

    These map onto the theory2 obstruction taxonomy: structural violations,
    semantic violations, relational mismatches, trust boundary errors, and
    orchestration failures.
    """

    LOCAL_REPAIR = "local_repair"
    COVER_REFINEMENT = "cover_refinement"
    TREATY_CHANGE = "treaty_change"
    THEORY_EXTENSION = "theory_extension"
    TRUST_VIOLATION = "trust_violation"
    JURISDICTION_EXCEEDED = "jurisdiction_exceeded"
    DESCENT_OBSTRUCTION = "descent_obstruction"
    ENCODING_MISMATCH = "encoding_mismatch"
    REPLAY_INVALIDATION = "replay_invalidation"
    MISSING_KEY = "missing_key"
    INVALID_VALUE = "invalid_value"
    TYPE_MISMATCH = "type_mismatch"
    IMPORT_FAILURE = "import_failure"
    TIMEOUT = "timeout"
    UNCLASSIFIED = "unclassified"


class EvidenceFamily(str, Enum):
    """Evidence channel involved in the failure.

    Preserving channel identity is required by theory2's evidence plurality
    doctrine: solver evidence stays solver-backed after aggregation, runtime
    evidence stays runtime-backed, and so on.
    """

    PROOF = "proof"
    SOLVER = "solver"
    RUNTIME = "runtime"
    SEMANTIC = "semantic"
    HUMAN = "human"
    MIXED = "mixed"
    UNKNOWN = "unknown"


class RepairPriority(IntEnum):
    """Urgency of a repair hint, from lowest to highest."""

    INFORMATIONAL = 0
    SUGGESTED = 1
    RECOMMENDED = 2
    REQUIRED = 3
    CRITICAL = 4
    LOW = SUGGESTED
    MEDIUM = RECOMMENDED
    HIGH = REQUIRED


# ---------------------------------------------------------------------------
# Frozen-JSON helpers
# ---------------------------------------------------------------------------

_EMPTY_MAPPING: Final[Mapping[str, FrozenJsonValue]] = MappingProxyType({})
_EMPTY_TUPLE: Final[tuple[()]] = ()
_EMPTY_TEXTS: Final[tuple[str, ...]] = ()


def _freeze_value(value: Any) -> FrozenJsonValue:
    """Recursively freeze a JSON-compatible value into an immutable form."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_value(item) for item in value)
    if isinstance(value, Mapping):
        return MappingProxyType(
            {str(k): _freeze_value(v) for k, v in value.items()}
        )
    return str(value)


def _thaw_value(value: FrozenJsonValue) -> JsonValue:
    """Recursively thaw a frozen JSON value back into mutable form."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, tuple):
        return [_thaw_value(item) for item in value]
    if isinstance(value, Mapping):
        return {k: _thaw_value(v) for k, v in value.items()}
    return str(value)


def _freeze_mapping(
    value: Mapping[str, Any] | None, *, field_name: str = ""
) -> Mapping[str, FrozenJsonValue]:
    """Freeze a mapping, returning an empty proxy for ``None``."""
    if value is None:
        return _EMPTY_MAPPING
    return MappingProxyType(
        {str(k): _freeze_value(v) for k, v in value.items()}
    )


def _freeze_text_sequence(
    value: Iterable[str] | None, *, field_name: str = ""
) -> tuple[str, ...]:
    """Freeze a sequence of strings."""
    if value is None:
        return _EMPTY_TEXTS
    return tuple(str(item) for item in value)


def _validate_json_round_trip(payload: dict[str, Any]) -> None:
    """Assert that a dict survives a JSON round trip without data loss."""
    encoded = json.dumps(payload, sort_keys=True, default=str)
    decoded = json.loads(encoded)
    re_encoded = json.dumps(decoded, sort_keys=True, default=str)
    if encoded != re_encoded:
        raise ValueError(
            f"JSON round-trip mismatch:\n  original: {encoded[:200]}\n"
            f"  decoded:  {re_encoded[:200]}"
        )


# ---------------------------------------------------------------------------
# RepairHint
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RepairHint:
    """One concrete repair suggestion attached to a failure.

    Repair hints are first-class so that orchestration can consume them
    directly instead of parsing free-text suggestions.

    Attributes
    ----------
    action : str
        Machine-readable action key (e.g. ``"tighten-overlap-treaty"``).
    description : str
        Human-readable explanation of the repair.
    priority : RepairPriority
        How urgent the repair is.
    target_coordinate : str or None
        The coordinate this repair applies to, if known.
    estimated_effort : str or None
        Rough effort classification (``"trivial"``, ``"moderate"``,
        ``"significant"``).
    prerequisites : tuple of str
        Other repair actions that must complete first.
    metadata : Mapping
        Arbitrary extra data for tooling.
    """

    action: str
    description: str
    priority: RepairPriority = RepairPriority.SUGGESTED
    target_coordinate: str | None = None
    estimated_effort: str | None = None
    prerequisites: tuple[str, ...] = ()
    metadata: Mapping[str, FrozenJsonValue] = field(
        default_factory=lambda: _EMPTY_MAPPING
    )

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "action": self.action,
            "description": self.description,
            "priority": self.priority.name.lower(),
            "target_coordinate": self.target_coordinate,
            "estimated_effort": self.estimated_effort,
            "prerequisites": list(self.prerequisites),
            "metadata": _thaw_value(self.metadata),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "RepairHint":
        priority_name = str(payload.get("priority", "suggested")).upper()
        try:
            priority = RepairPriority[priority_name]
        except KeyError:
            priority = RepairPriority.SUGGESTED
        return cls(
            action=str(payload.get("action", "")),
            description=str(payload.get("description", "")),
            priority=priority,
            target_coordinate=payload.get("target_coordinate"),
            estimated_effort=payload.get("estimated_effort"),
            prerequisites=tuple(
                str(p) for p in payload.get("prerequisites", ())
            ),
            metadata=_freeze_mapping(payload.get("metadata")),
        )


# ---------------------------------------------------------------------------
# ObstructionRecord
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ObstructionRecord:
    """Full theory2 obstruction object ``(c, κ, E, R, Δ)``.

    This mirrors the formal obstruction definition from theory2.tex:

    * ``coordinate`` — the semantic coordinate ``c`` where the violation
      was detected.
    * ``violated_condition`` — the admissibility condition ``κ`` that
      failed.
    * ``evidence`` — the evidence bundle ``E`` that was present when the
      violation was detected.
    * ``repair_frontier`` — the repair frontier ``R`` listing concrete
      repair actions.
    * ``downstream_effects`` — the downstream effects ``Δ`` on other
      obligations.

    Obstructions are persistent and treated as cohomology classes, not
    ephemeral error logs.  They survive serialization, transport, and
    comparison across subsystem boundaries.

    Attributes
    ----------
    coordinate : str
        Semantic coordinate where the violation was detected.
    violated_condition : str
        Which admissibility condition was violated.
    evidence_family : EvidenceFamily
        Which evidence channel was involved.
    evidence : Mapping
        Evidence bundle present at the time of violation.
    repair_hints : tuple of RepairHint
        Ordered list of suggested repair actions.
    downstream_obligations : tuple of str
        Obligation identifiers affected by this obstruction.
    support_scope : tuple of str
        Coordinates that supported this obstruction's detection.
    provenance : Mapping
        Where and how the obstruction was discovered.
    is_coboundary : bool or None
        Whether this obstruction is a coboundary (trivially resolvable)
        in the cohomological sense.
    """

    coordinate: str
    violated_condition: str
    evidence_family: EvidenceFamily = EvidenceFamily.UNKNOWN
    evidence: Mapping[str, FrozenJsonValue] = field(
        default_factory=lambda: _EMPTY_MAPPING
    )
    repair_hints: tuple[RepairHint, ...] = ()
    downstream_obligations: tuple[str, ...] = ()
    support_scope: tuple[str, ...] = ()
    provenance: Mapping[str, FrozenJsonValue] = field(
        default_factory=lambda: _EMPTY_MAPPING
    )
    is_coboundary: bool | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "evidence", _freeze_mapping(self.evidence)
        )
        object.__setattr__(
            self, "provenance", _freeze_mapping(self.provenance)
        )
        object.__setattr__(
            self,
            "downstream_obligations",
            _freeze_text_sequence(self.downstream_obligations),
        )
        object.__setattr__(
            self,
            "support_scope",
            _freeze_text_sequence(self.support_scope),
        )

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "coordinate": self.coordinate,
            "violated_condition": self.violated_condition,
            "evidence_family": self.evidence_family.value,
            "evidence": _thaw_value(self.evidence),
            "repair_hints": [h.to_dict() for h in self.repair_hints],
            "downstream_obligations": list(self.downstream_obligations),
            "support_scope": list(self.support_scope),
            "provenance": _thaw_value(self.provenance),
            "is_coboundary": self.is_coboundary,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ObstructionRecord":
        try:
            family = EvidenceFamily(payload.get("evidence_family", "unknown"))
        except ValueError:
            family = EvidenceFamily.UNKNOWN
        return cls(
            coordinate=str(payload.get("coordinate", "")),
            violated_condition=str(
                payload.get("violated_condition", "")
            ),
            evidence_family=family,
            evidence=payload.get("evidence") or {},
            repair_hints=tuple(
                RepairHint.from_dict(h)
                for h in payload.get("repair_hints", ())
            ),
            downstream_obligations=tuple(
                str(o) for o in payload.get("downstream_obligations", ())
            ),
            support_scope=tuple(
                str(s) for s in payload.get("support_scope", ())
            ),
            provenance=payload.get("provenance") or {},
            is_coboundary=payload.get("is_coboundary"),
        )

    def with_repair_hint(self, hint: RepairHint) -> "ObstructionRecord":
        """Return a copy with an additional repair hint."""
        return replace(self, repair_hints=(*self.repair_hints, hint))

    def with_downstream(self, *obligations: str) -> "ObstructionRecord":
        """Return a copy with additional downstream obligations."""
        merged = tuple(
            dict.fromkeys((*self.downstream_obligations, *obligations))
        )
        return replace(self, downstream_obligations=merged)


# ---------------------------------------------------------------------------
# StructuredFailure
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True, init=False)
class StructuredFailure:
    """Immutable obstruction payload shared by JuGeo subsystems.

    The record mirrors the theory2 obstruction object ``(c, κ, E, R, Δ)``
    and extends it with provenance, trust, semantic boundary, and
    exception-routing details needed in the Python runtime.

    A ``StructuredFailure`` is intentionally JSON-shaped.  Nested payloads
    are recursively frozen into tuples and read-only mappings so the record
    is safe to share across subsystem boundaries without accidental mutation.

    Attributes
    ----------
    message : str
        Human-readable summary of what went wrong.
    scope : FailureScope
        Which subsystem the failure originated in.
    classification : FailureClassification
        Broad failure family for routing.
    evidence_family : EvidenceFamily
        Which evidence channel was involved.
    coordinate : str or None
        Semantic coordinate where the failure was detected.
    support_scope : str or None
        Support region relevant to this failure.
    semantic_boundary : str or None
        Which semantic boundary was crossed.
    trust_boundary : str or None
        Which trust boundary was relevant.
    obstruction : ObstructionRecord or None
        Full obstruction record if available.
    repair_hints : tuple of RepairHint
        Suggested repairs.
    affected_obligations : tuple of str
        Obligations affected by this failure.
    provenance : Mapping
        Where and how the failure was discovered.
    trust : Mapping
        Trust-accounting context.
    metadata : Mapping
        Arbitrary extra data.
    exception_type : str or None
        Original Python exception class name if wrapping an exception.
    notes : tuple of str
        Free-text notes for humans.
    traceback_lines : tuple of str
        Formatted traceback lines.
    is_coboundary : bool or None
        Whether the underlying obstruction is trivially resolvable.
    recoverable : bool
        Whether the failure is expected to be recoverable.
    """

    message: str
    code: str = ""
    scope: FailureScope = FailureScope.UNKNOWN
    classification: FailureClassification = (
        FailureClassification.UNCLASSIFIED
    )
    evidence_family: EvidenceFamily = EvidenceFamily.UNKNOWN
    coordinate: str | None = None
    support_scope: str | None = None
    semantic_boundary: str | None = None
    trust_boundary: str | None = None
    obstruction: ObstructionRecord | None = None
    repair_hints: tuple[RepairHint, ...] = ()
    obligations: tuple[str, ...] = field(default_factory=lambda: _EMPTY_TEXTS)
    affected_obligations: tuple[str, ...] = field(
        default_factory=lambda: _EMPTY_TEXTS
    )
    provenance: Mapping[str, FrozenJsonValue] = field(
        default_factory=lambda: _EMPTY_MAPPING
    )
    trust: Mapping[str, FrozenJsonValue] = field(
        default_factory=lambda: _EMPTY_MAPPING
    )
    metadata: Mapping[str, FrozenJsonValue] = field(
        default_factory=lambda: _EMPTY_MAPPING
    )
    exception_type: str | None = None
    notes: tuple[str, ...] = field(default_factory=lambda: _EMPTY_TEXTS)
    traceback_lines: tuple[str, ...] = field(
        default_factory=lambda: _EMPTY_TEXTS
    )
    is_coboundary: bool | None = None
    recoverable: bool = False

    def __init__(
        self,
        message: str = "",
        code: str = "",
        scope: FailureScope = FailureScope.UNKNOWN,
        classification: FailureClassification | str = FailureClassification.UNCLASSIFIED,
        evidence_family: EvidenceFamily | str = EvidenceFamily.UNKNOWN,
        coordinate: str | None = None,
        support_scope: str | None = None,
        semantic_boundary: str | None = None,
        trust_boundary: str | None = None,
        obstruction: ObstructionRecord | None = None,
        repair_hints: Sequence[RepairHint] = (),
        obligations: Sequence[str] = (),
        affected_obligations: Sequence[str] = (),
        provenance: Mapping[str, Any] | None = None,
        trust: Mapping[str, Any] | None = None,
        metadata: Mapping[str, Any] | None = None,
        exception_type: str | None = None,
        notes: Sequence[str] = (),
        traceback_lines: Sequence[str] = (),
        is_coboundary: bool | None = None,
        recoverable: bool = False,
        *,
        summary: str | None = None,
        details: Mapping[str, Any] | None = None,
        trust_at_failure: Any = None,
    ) -> None:
        if scope is None:
            scope = FailureScope.UNKNOWN
        if summary is not None and not message:
            message = summary
        if details:
            merged_metadata = dict(metadata or {})
            merged_metadata.update(details)
            metadata = merged_metadata
        if trust_at_failure is not None:
            merged_trust = dict(trust or {})
            merged_trust["trust_at_failure"] = trust_at_failure
            trust = merged_trust
        if isinstance(classification, str):
            try:
                classification = FailureClassification[classification.upper()]
            except KeyError:
                classification = FailureClassification.UNCLASSIFIED
        if isinstance(evidence_family, str):
            try:
                evidence_family = EvidenceFamily[evidence_family.upper()]
            except KeyError:
                evidence_family = EvidenceFamily.UNKNOWN
        object.__setattr__(self, "message", message)
        object.__setattr__(self, "code", code)
        object.__setattr__(self, "scope", scope)
        object.__setattr__(self, "classification", classification)
        object.__setattr__(self, "evidence_family", evidence_family)
        object.__setattr__(self, "coordinate", coordinate)
        object.__setattr__(self, "support_scope", support_scope)
        object.__setattr__(self, "semantic_boundary", semantic_boundary)
        object.__setattr__(self, "trust_boundary", trust_boundary)
        object.__setattr__(self, "obstruction", obstruction)
        object.__setattr__(self, "repair_hints", tuple(repair_hints))
        object.__setattr__(self, "obligations", tuple(obligations))
        object.__setattr__(self, "affected_obligations", tuple(affected_obligations))
        object.__setattr__(self, "provenance", provenance or _EMPTY_MAPPING)
        object.__setattr__(self, "trust", trust or _EMPTY_MAPPING)
        object.__setattr__(self, "metadata", metadata or _EMPTY_MAPPING)
        object.__setattr__(self, "exception_type", exception_type)
        object.__setattr__(self, "notes", tuple(notes))
        object.__setattr__(self, "traceback_lines", tuple(traceback_lines))
        object.__setattr__(self, "is_coboundary", is_coboundary)
        object.__setattr__(self, "recoverable", recoverable)
        self.__post_init__()

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "obligations",
            _freeze_text_sequence(self.obligations),
        )
        object.__setattr__(
            self, "affected_obligations",
            _freeze_text_sequence(self.affected_obligations),
        )
        object.__setattr__(
            self, "provenance", _freeze_mapping(self.provenance)
        )
        object.__setattr__(self, "trust", _freeze_mapping(self.trust))
        object.__setattr__(
            self, "metadata", _freeze_mapping(self.metadata)
        )
        object.__setattr__(
            self, "notes", _freeze_text_sequence(self.notes)
        )
        object.__setattr__(
            self,
            "traceback_lines",
            _freeze_text_sequence(self.traceback_lines),
        )
        if self.code and "code" not in self.metadata:
            updated_meta = dict(self.metadata)
            updated_meta["code"] = self.code
            object.__setattr__(self, "metadata", _freeze_mapping(updated_meta))
        elif not self.code and "code" in self.metadata:
            object.__setattr__(self, "code", str(self.metadata["code"]))
        if self.obligations and not self.affected_obligations:
            object.__setattr__(self, "affected_obligations", self.obligations)
        elif self.affected_obligations and not self.obligations:
            object.__setattr__(self, "obligations", self.affected_obligations)

    # -- Serialization -------------------------------------------------------

    def to_dict(self) -> dict[str, JsonValue]:
        """Return a JSON-serializable representation with deterministic keys."""
        return {
            "message": self.message,
            "code": self.code,
            "scope": self.scope.value,
            "classification": self.classification.value,
            "evidence_family": self.evidence_family.value,
            "coordinate": self.coordinate,
            "support_scope": self.support_scope,
            "semantic_boundary": self.semantic_boundary,
            "trust_boundary": self.trust_boundary,
            "obstruction": (
                self.obstruction.to_dict() if self.obstruction else None
            ),
            "repair_hints": [h.to_dict() for h in self.repair_hints],
            "obligations": list(self.obligations),
            "affected_obligations": list(self.affected_obligations),
            "provenance": _thaw_value(self.provenance),
            "trust": _thaw_value(self.trust),
            "metadata": _thaw_value(self.metadata),
            "details": _thaw_value(self.metadata.get("details"))
            if "details" in self.metadata
            else None,
            "exception_type": self.exception_type,
            "notes": list(self.notes),
            "traceback_lines": list(self.traceback_lines),
            "is_coboundary": self.is_coboundary,
            "recoverable": self.recoverable,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "StructuredFailure":
        """Rebuild a failure payload from :meth:`to_dict` output.

        Unknown top-level keys are preserved under
        ``metadata['_extra_fields']`` so future producers do not silently
        lose information.
        """
        known_keys = {
            "message", "code", "scope", "classification", "evidence_family",
            "coordinate", "support_scope", "semantic_boundary",
            "trust_boundary", "obstruction", "repair_hints", "obligations",
            "affected_obligations", "provenance", "trust", "metadata",
            "details", "exception_type", "notes", "traceback_lines", "is_coboundary",
            "recoverable",
        }
        extras = {k: payload[k] for k in set(payload) - known_keys}
        metadata = dict(payload.get("metadata") or {})
        if payload.get("details") is not None and "details" not in metadata:
            metadata["details"] = payload.get("details")
        if extras:
            metadata["_extra_fields"] = extras

        scope_raw = payload.get("scope", "unknown")
        try:
            scope = FailureScope(scope_raw)
        except ValueError:
            scope = FailureScope.UNKNOWN

        classification_raw = payload.get("classification", "unclassified")
        try:
            classification = FailureClassification(classification_raw)
        except ValueError:
            classification = FailureClassification.UNCLASSIFIED

        evidence_raw = payload.get("evidence_family", "unknown")
        try:
            evidence_family = EvidenceFamily(evidence_raw)
        except ValueError:
            evidence_family = EvidenceFamily.UNKNOWN

        obstruction_raw = payload.get("obstruction")
        obstruction = (
            ObstructionRecord.from_dict(obstruction_raw)
            if obstruction_raw
            else None
        )

        return cls(
            message=str(payload.get("message", "")),
            code=str(payload.get("code", "")),
            scope=scope,
            classification=classification,
            evidence_family=evidence_family,
            coordinate=payload.get("coordinate"),
            support_scope=payload.get("support_scope"),
            semantic_boundary=payload.get("semantic_boundary"),
            trust_boundary=payload.get("trust_boundary"),
            obstruction=obstruction,
            repair_hints=tuple(
                RepairHint.from_dict(h)
                for h in payload.get("repair_hints", ())
            ),
            obligations=tuple(
                str(o) for o in payload.get("obligations", ())
            ),
            affected_obligations=tuple(
                str(o) for o in payload.get("affected_obligations", ())
            ),
            provenance=payload.get("provenance") or {},
            trust=payload.get("trust") or {},
            metadata=metadata,
            exception_type=payload.get("exception_type"),
            notes=tuple(
                str(n) for n in payload.get("notes", ())
            ),
            traceback_lines=tuple(
                str(t) for t in payload.get("traceback_lines", ())
            ),
            is_coboundary=payload.get("is_coboundary"),
            recoverable=bool(payload.get("recoverable", False)),
        )

    # -- Mutation-free transforms --------------------------------------------

    def with_scope(
        self, scope: FailureScope, /, **detail_updates: Any
    ) -> "StructuredFailure":
        """Return a copy relocated to *scope* with optional detail updates."""
        updated_meta = dict(self.metadata)
        updated_meta.update(detail_updates)
        return replace(self, scope=scope, metadata=updated_meta)

    def with_coordinate(self, coordinate: str) -> "StructuredFailure":
        """Return a copy pinned to *coordinate*."""
        return replace(self, coordinate=coordinate)

    def with_classification(
        self, classification: FailureClassification
    ) -> "StructuredFailure":
        """Return a copy reclassified to *classification*."""
        return replace(self, classification=classification)

    def with_repair_hint(self, hint: RepairHint) -> "StructuredFailure":
        """Return a copy with an additional repair hint."""
        return replace(
            self, repair_hints=(*self.repair_hints, hint)
        )

    def with_note(self, note: str) -> "StructuredFailure":
        """Return a copy with an additional free-text note."""
        return replace(self, notes=(*self.notes, note))

    def __getitem__(self, key: str) -> JsonValue:
        return self.to_dict()[key]

    def with_obligation(self, *obligations: str) -> "StructuredFailure":
        """Return a copy with additional affected obligations."""
        merged = tuple(
            dict.fromkeys(
                (*self.affected_obligations, *obligations)
            )
        )
        return replace(self, affected_obligations=merged)

    def mark_recoverable(self) -> "StructuredFailure":
        """Return a copy marked as recoverable."""
        return replace(self, recoverable=True)

    def mark_coboundary(self) -> "StructuredFailure":
        """Return a copy marked as a trivially resolvable coboundary."""
        return replace(self, is_coboundary=True)


# ---------------------------------------------------------------------------
# JuGeoError
# ---------------------------------------------------------------------------


class JuGeoError(RuntimeError):
    """Base exception for JuGeo runtime and semantic failures.

    The exception remains ergonomic for ordinary Python callers, but always
    exposes a :class:`StructuredFailure` payload so higher-level JuGeo
    systems can preserve provenance, trust, scope, and repair information.

    Attributes
    ----------
    failure : StructuredFailure
        The structured payload.
    message : str
        Shorthand for ``failure.message``.
    coordinate : str or None
        Shorthand for ``failure.coordinate``.
    scope : FailureScope
        Shorthand for ``failure.scope``.
    classification : FailureClassification
        Shorthand for ``failure.classification``.
    evidence_family : EvidenceFamily
        Shorthand for ``failure.evidence_family``.
    """

    def __init__(
        self,
        failure: StructuredFailure,
        *,
        cause: BaseException | None = None,
    ) -> None:
        super().__init__(failure.message)
        self.failure = failure
        self.message = failure.message
        self.coordinate = failure.coordinate
        self.scope = failure.scope
        self.classification = failure.classification
        self.evidence_family = failure.evidence_family
        if cause is not None:
            self.__cause__ = cause

    def __str__(self) -> str:
        parts = [self.message]
        if self.coordinate:
            parts.append(f"coordinate={self.coordinate}")
        if self.scope != FailureScope.UNKNOWN:
            parts.append(f"scope={self.scope.value}")
        if self.evidence_family != EvidenceFamily.UNKNOWN:
            parts.append(f"evidence={self.evidence_family.value}")
        if self.classification != FailureClassification.UNCLASSIFIED:
            parts.append(f"class={self.classification.value}")
        if len(parts) == 1:
            return parts[0]
        return f"{parts[0]} [{'; '.join(parts[1:])}]"

    def __repr__(self) -> str:
        return (
            f"JuGeoError(message={self.message!r}, "
            f"coordinate={self.coordinate!r}, "
            f"scope={self.scope!r}, "
            f"classification={self.classification!r})"
        )

    def to_dict(self) -> dict[str, JsonValue]:
        """Delegate to the underlying failure payload."""
        return self.failure.to_dict()


# ---------------------------------------------------------------------------
# FailureChain
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FailureChain:
    """Ordered collection of related failures.

    Theory2 requires that overlap structure is preserved, not collapsed.
    A chain of failures from different coordinates or different evidence
    channels must remain individually addressable rather than being
    flattened into a single error message.

    Attributes
    ----------
    failures : tuple of StructuredFailure
        Individual failures in encounter order.
    context_coordinate : str or None
        The coordinate that governs the whole chain if there is one.
    summary : str
        One-line summary suitable for logs or CLI output.
    """

    failures: tuple[StructuredFailure, ...]
    context_coordinate: str | None = None
    summary: str = ""

    def __post_init__(self) -> None:
        if not self.summary and self.failures:
            object.__setattr__(
                self,
                "summary",
                f"{len(self.failures)} failure(s) in chain"
                + (
                    f" at {self.context_coordinate}"
                    if self.context_coordinate
                    else ""
                ),
            )

    def __len__(self) -> int:
        return len(self.failures)

    def __iter__(self):
        return iter(self.failures)

    def __getitem__(self, index: int) -> StructuredFailure:
        return self.failures[index]

    def scopes(self) -> frozenset[FailureScope]:
        """Distinct scopes present in the chain."""
        return frozenset(f.scope for f in self.failures)

    def classifications(self) -> frozenset[FailureClassification]:
        """Distinct classifications present in the chain."""
        return frozenset(f.classification for f in self.failures)

    @property
    def evidence_families(self) -> frozenset[EvidenceFamily]:
        """Distinct evidence families present in the chain."""
        return frozenset(f.evidence_family for f in self.failures)

    def all_repair_hints(self) -> tuple[RepairHint, ...]:
        """All repair hints from all failures, in order."""
        return tuple(
            hint
            for failure in self.failures
            for hint in failure.repair_hints
        )

    def all_affected_obligations(self) -> tuple[str, ...]:
        """All affected obligations, deduplicated and ordered."""
        return tuple(
            dict.fromkeys(
                ob
                for failure in self.failures
                for ob in failure.affected_obligations
            )
        )

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "failures": [f.to_dict() for f in self.failures],
            "context_coordinate": self.context_coordinate,
            "summary": self.summary,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "FailureChain":
        return cls(
            failures=tuple(
                StructuredFailure.from_dict(f)
                for f in payload.get("failures", ())
            ),
            context_coordinate=payload.get("context_coordinate"),
            summary=str(payload.get("summary", "")),
        )

    def append(self, failure: StructuredFailure) -> "FailureChain":
        """Return a new chain with *failure* appended."""
        return replace(
            self,
            failures=(*self.failures, failure),
            summary="",
        )

    def filter_by_scope(self, scope: FailureScope) -> "FailureChain":
        """Return a new chain containing only failures from *scope*."""
        return replace(
            self,
            failures=tuple(
                f for f in self.failures if f.scope == scope
            ),
            summary="",
        )

    def filter_by_classification(
        self, classification: FailureClassification
    ) -> "FailureChain":
        """Return a new chain containing only failures with *classification*."""
        return replace(
            self,
            failures=tuple(
                f
                for f in self.failures
                if f.classification == classification
            ),
            summary="",
        )


# ---------------------------------------------------------------------------
# FailureFilter
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True, init=False)
class FailureFilter:
    """Predicate object for selecting failures from a sequence.

    Attributes
    ----------
    scopes : frozenset of FailureScope or None
        If set, only include failures whose scope is in this set.
    classifications : frozenset of FailureClassification or None
        If set, only include failures whose classification is in this set.
    evidence_families : frozenset of EvidenceFamily or None
        If set, only include failures whose evidence family is in this set.
    coordinate_prefix : str or None
        If set, only include failures whose coordinate starts with this prefix.
    recoverable_only : bool
        If True, only include recoverable failures.
    custom_predicate : callable or None
        If set, only include failures for which this returns True.
    """

    scopes: frozenset[FailureScope] | None = None
    classifications: frozenset[FailureClassification] | None = None
    evidence_families: frozenset[EvidenceFamily] | None = None
    coordinate_prefix: str | None = None
    recoverable_only: bool = False
    custom_predicate: Callable[[StructuredFailure], bool] | None = None

    def __init__(
        self,
        *,
        scope: FailureScope | None = None,
        scopes: frozenset[FailureScope] | Sequence[FailureScope] | None = None,
        classifications: frozenset[FailureClassification] | Sequence[FailureClassification] | None = None,
        evidence_families: frozenset[EvidenceFamily] | Sequence[EvidenceFamily] | None = None,
        coordinate_prefix: str | None = None,
        recoverable_only: bool = False,
        custom_predicate: Callable[[StructuredFailure], bool] | None = None,
    ) -> None:
        normalized_scopes = frozenset(scopes) if scopes is not None else None
        if scope is not None:
            normalized_scopes = frozenset({scope}) | (normalized_scopes or frozenset())
        object.__setattr__(self, 'scopes', normalized_scopes)
        object.__setattr__(
            self,
            'classifications',
            frozenset(classifications) if classifications is not None else None,
        )
        object.__setattr__(
            self,
            'evidence_families',
            frozenset(evidence_families) if evidence_families is not None else None,
        )
        object.__setattr__(self, 'coordinate_prefix', coordinate_prefix)
        object.__setattr__(self, 'recoverable_only', recoverable_only)
        object.__setattr__(self, 'custom_predicate', custom_predicate)

    def matches(self, failure: StructuredFailure) -> bool:
        """Return True if *failure* passes all filter criteria."""
        if self.scopes is not None and failure.scope not in self.scopes:
            return False
        if (
            self.classifications is not None
            and failure.classification not in self.classifications
        ):
            return False
        if (
            self.evidence_families is not None
            and failure.evidence_family not in self.evidence_families
        ):
            return False
        if self.coordinate_prefix is not None:
            coord = failure.coordinate or ""
            if not coord.startswith(self.coordinate_prefix):
                return False
        if self.recoverable_only and not failure.recoverable:
            return False
        if self.custom_predicate is not None:
            return self.custom_predicate(failure)
        return True

    def apply(
        self, failures: Iterable[StructuredFailure]
    ) -> tuple[StructuredFailure, ...]:
        """Return the subset of *failures* that pass the filter."""
        return tuple(f for f in failures if self.matches(f))


# ---------------------------------------------------------------------------
# Public helper functions
# ---------------------------------------------------------------------------


def classify_error(
    error: BaseException,
    *,
    regime_language: str | None = None,
) -> tuple[FailureClassification, FailureScope]:
    """Map an arbitrary Python exception to a classification and scope.

    Parameters
    ----------
    error : BaseException
        The exception to classify.
    regime_language : str or None
        Optional hint about the semantic regime, unused for now but
        reserved for future pack-specific classification.

    Returns
    -------
    tuple of (FailureClassification, FailureScope)
    """
    if isinstance(error, JuGeoError):
        return error.classification, error.scope

    _MAP: dict[type, tuple[FailureClassification, FailureScope]] = {
        KeyError: (
            FailureClassification.MISSING_KEY,
            FailureScope.ROOT,
        ),
        ValueError: (
            FailureClassification.INVALID_VALUE,
            FailureScope.ROOT,
        ),
        TypeError: (
            FailureClassification.TYPE_MISMATCH,
            FailureScope.ROOT,
        ),
        ImportError: (
            FailureClassification.IMPORT_FAILURE,
            FailureScope.ROOT,
        ),
        TimeoutError: (
            FailureClassification.TIMEOUT,
            FailureScope.RUNTIME,
        ),
    }
    for exc_type, result in _MAP.items():
        if isinstance(error, exc_type):
            return result
    return FailureClassification.UNCLASSIFIED, FailureScope.UNKNOWN


def as_failure_payload(
    error: BaseException,
    *,
    scope: FailureScope | None = None,
    context_override: Mapping[str, Any] | None = None,
) -> StructuredFailure:
    """Wrap any exception into a :class:`StructuredFailure`.

    Parameters
    ----------
    error : BaseException
        The exception to wrap.
    scope : FailureScope or None
        Override the auto-detected scope.
    context_override : Mapping or None
        Extra metadata to merge into the failure.

    Returns
    -------
    StructuredFailure
    """
    if isinstance(error, JuGeoError):
        failure = error.failure
        if scope is not None:
            failure = failure.with_scope(scope)
        if context_override:
            updated_meta = dict(failure.metadata)
            updated_meta.update(context_override)
            failure = replace(failure, metadata=updated_meta)
        return failure

    classification, auto_scope = classify_error(error)
    effective_scope = scope or auto_scope

    tb_lines: tuple[str, ...] = ()
    if error.__traceback__ is not None:
        tb_lines = tuple(
            traceback_module.format_exception(
                type(error), error, error.__traceback__
            )
        )

    metadata = dict(context_override or {})
    metadata["exception_type"] = type(error).__name__

    return StructuredFailure(
        message=str(error) or type(error).__name__,
        scope=effective_scope,
        classification=classification,
        exception_type=type(error).__name__,
        traceback_lines=tb_lines,
        metadata=metadata,
        recoverable=False,
    )


def raise_with_scope(
    code_or_message: str = "",
    *,
    message: str | None = None,
    code: str = "",
    scope: FailureScope = FailureScope.ROOT,
    classification: FailureClassification = (
        FailureClassification.UNCLASSIFIED
    ),
    evidence_family: EvidenceFamily = EvidenceFamily.UNKNOWN,
    coordinate: str | None = None,
    support_scope: str | None = None,
    trust_boundary: str | None = None,
    obstruction: ObstructionRecord | None = None,
    repair_hints: tuple[RepairHint, ...] = (),
    affected_obligations: tuple[str, ...] = (),
    provenance: Mapping[str, Any] | None = None,
    notes: tuple[str, ...] = (),
    recoverable: bool = False,
    cause: BaseException | None = None,
    details: Mapping[str, Any] | None = None,
) -> NoReturn:
    """Build a :class:`StructuredFailure` and raise :class:`JuGeoError`.

    This is the preferred way to raise semantic errors throughout JuGeo.
    """
    if message is None:
        effective_message = code_or_message
        effective_code = code
    else:
        effective_message = message
        effective_code = code or code_or_message

    metadata: dict[str, Any] = {}
    if effective_code:
        metadata["code"] = effective_code
    if details:
        metadata["details"] = dict(details)
    failure = StructuredFailure(
        message=effective_message,
        code=effective_code,
        scope=scope,
        classification=classification,
        evidence_family=evidence_family,
        coordinate=coordinate,
        support_scope=support_scope,
        trust_boundary=trust_boundary,
        obstruction=obstruction,
        repair_hints=repair_hints,
        affected_obligations=affected_obligations,
        provenance=provenance or {},
        metadata=metadata,
        notes=notes,
        recoverable=recoverable,
    )
    raise JuGeoError(failure, cause=cause)


def chain_failures(
    *failures: StructuredFailure,
    context_coordinate: str | None = None,
) -> FailureChain:
    """Combine multiple failures into a :class:`FailureChain`.

    This preserves overlap structure rather than collapsing into a single
    message, as required by theory2's treatment of overlaps and descent.
    """
    return FailureChain(
        failures=failures,
        context_coordinate=context_coordinate,
    )


def filter_failures(
    failures: Iterable[StructuredFailure],
    filt: FailureFilter,
) -> tuple[StructuredFailure, ...]:
    """Select failures matching *filt* from *failures*."""
    return filt.apply(failures)


def merge_repair_hints(
    *sources: StructuredFailure | FailureChain | Iterable[RepairHint],
) -> tuple[RepairHint, ...]:
    """Merge repair hints from multiple sources.

    Deduplicates by action key and preserves the highest priority seen
    for each action.
    """
    best: dict[str, RepairHint] = {}
    for source in sources:
        hints: Iterable[RepairHint]
        if isinstance(source, FailureChain):
            hints = source.all_repair_hints()
        elif isinstance(source, StructuredFailure):
            hints = source.repair_hints
        else:
            hints = source
        for hint in hints:
            existing = best.get(hint.action)
            if existing is None or hint.priority > existing.priority:
                best[hint.action] = hint
    return tuple(
        sorted(best.values(), key=lambda h: (-h.priority, h.action))
    )


# ---------------------------------------------------------------------------
# __all__
# ---------------------------------------------------------------------------

__all__ = [
    "CANONICAL_CLASSIFICATIONS",
    "CANONICAL_EVIDENCE_FAMILIES",
    "EvidenceFamily",
    "FailureChain",
    "FailureClassification",
    "FailureFilter",
    "FailureScope",
    "JuGeoError",
    "ObstructionRecord",
    "RepairHint",
    "RepairPriority",
    "StructuredFailure",
    "as_failure_payload",
    "chain_failures",
    "classify_error",
    "filter_failures",
    "merge_repair_hints",
    "raise_with_scope",
    # Unified judgment-geometric error types
    "JudgmentError",
    "DescentError",
    "TrustViolationError",
    "EncodingError",
]


# ---------------------------------------------------------------------------
# Unified judgment-geometric error types
# ---------------------------------------------------------------------------


class JudgmentError(JuGeoError):
    """Raised when a judgment cannot be formed or validated.

    Covers failures in ``jugeo.judgments`` — malformed judgment
    descriptors, missing term constructors, or arity mismatches in the
    judgment term algebra.
    """


class DescentError(JuGeoError):
    """Raised when geometric descent fails or violates its invariants.

    Covers failures in ``jugeo.geometry.descent`` — non-terminating
    descent, ill-formed sites, or coordinate-system inconsistencies.
    """


class TrustViolationError(JuGeoError):
    """Raised when the trust lattice invariant is violated.

    Covers failures in ``jugeo.evidence.trust`` — silent trust promotion,
    evidence-channel mismatch, or lattice-ordering breaches.
    """


class EncodingError(JuGeoError):
    """Raised when a program encoding is invalid or unsupported.

    Covers failures in ``jugeo.encodings`` — unrecognised encoding
    families, schema violations, or round-trip fidelity failures.
    """
