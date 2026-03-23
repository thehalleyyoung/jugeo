"""Domain pack catalog for JuGeo."""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Final, Iterator, Mapping, Sequence

from jugeo.errors import FailureScope, raise_with_scope
from jugeo.package_manifest import build_package_manifest, enumerate_subsystems
from jugeo.runtime_defaults import default_runtime_options

# ---------------------------------------------------------------------------
# Type aliases
# ---------------------------------------------------------------------------

#: A JSON scalar value.
JsonScalar = str | int | float | bool | None

#: Any JSON-representable value.
JsonValue = JsonScalar | list[Any] | dict[str, Any]

#: A mutable form of a JSON value.
MutableJsonValue = JsonScalar | list["MutableJsonValue"] | dict[str, "MutableJsonValue"]

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Ordered authority levels — lower index = higher authority.
KNOWN_AUTHORITY_LEVELS: Final[tuple[str, ...]] = (
    "quarantined",
    "exploratory",
    "provisional",
    "foundational",
)

#: Maps pack names to their originating provenance source.
PACK_SPEC_PROVENANCE: Final[dict[str, Any]] = {
    "source_tex": "preliminaries/theory2.tex",
    "source_pdf": "preliminaries/theory2.pdf",
    "blueprint_path": "theory2-src-blueprint.json",
    "generation_order_path": "theory2-generation-order.json",
    "target_file": "src/jugeo/packs/catalog.py",
    "target_test": "tests/jugeo/packs/test_catalog.py",
    "stage": "shared-packs",
    "sequence": 24,
}

#: Shared sentinel for empty frozen mappings.
_EMPTY_MAPPING: Final[Mapping[str, Any]] = MappingProxyType({})

#: Internal stage counter used during catalog construction.
_PACK_STAGE: int = 0

# ---------------------------------------------------------------------------
# __all__
# ---------------------------------------------------------------------------

__all__: Final[tuple[str, ...]] = (
    "JsonScalar",
    "JsonValue",
    "MutableJsonValue",
    "KNOWN_AUTHORITY_LEVELS",
    "PACK_SPEC_PROVENANCE",
    "PackLaw",
    "PackAdapter",
    "PackBoundary",
    "PackDescriptor",
    "PackCatalog",
    "load_pack_catalog",
    "list_available_packs",
    "MANIFEST_SUBSYSTEM_PACKS",
    # Cross-subsystem enrichments
    "site_catalog",
    "trust_catalog",
)

# ---------------------------------------------------------------------------
# Helper functions (must appear before first dataclass)
# ---------------------------------------------------------------------------


def _normalize_required_text(value: Any, *, field_name: str) -> str:
    """Coerce *value* to a non-empty str.

    Parameters
    ----------
    value:
        The raw input value.
    field_name:
        Name of the field being normalized, used in error messages.

    Returns
    -------
    str

    Raises
    ------
    jugeo.errors.JuGeoError
        If *value* is None or empty after stripping.
    """
    if value is None:
        raise_with_scope(
            "missing-required-text-field",
            message=(
                f"Required text field {field_name!r} must not be None or empty; "
                "received None."
            ),
            scope=FailureScope.PACK,
            provenance={"field_name": field_name, "raw_value": repr(value)},
        )
    coerced = str(value).strip()
    if not coerced:
        raise_with_scope(
            "empty-required-text-field",
            message=(
                f"Required text field {field_name!r} must not be empty; "
                f"received {value!r}."
            ),
            scope=FailureScope.PACK,
            provenance={"field_name": field_name, "raw_value": repr(value)},
        )
    return coerced


def _normalize_optional_text(value: Any) -> str | None:
    """Coerce *value* to a stripped str or None.

    Returns None if *value* is None or empty after stripping.

    Parameters
    ----------
    value:
        The raw input value.

    Returns
    -------
    str | None
    """
    if value is None:
        return None
    coerced = str(value).strip()
    return coerced if coerced else None


def _normalize_text_tuple(values: Any, *, field_name: str) -> tuple[str, ...]:
    """Coerce *values* to a frozen tuple of stripped, non-empty strings.

    Parameters
    ----------
    values:
        An iterable of raw string-like values, or None.
    field_name:
        Name of the field being normalized, used in error messages.

    Returns
    -------
    tuple[str, ...]

    Raises
    ------
    jugeo.errors.JuGeoError
        If any element coerces to an empty string.
    """
    if values is None:
        return ()
    result: list[str] = []
    for idx, item in enumerate(values):
        text = _normalize_optional_text(item)
        if text is None:
            raise_with_scope(
                "empty-text-tuple-element",
                message=(
                    f"Element at index {idx} in field {field_name!r} must be "
                    f"a non-empty string; received {item!r}."
                ),
                scope=FailureScope.PACK,
                provenance={
                    "field_name": field_name,
                    "index": idx,
                    "raw_value": repr(item),
                },
            )
        result.append(text)
    return tuple(result)


def _freeze_json(value: Any, *, field_name: str) -> JsonValue:
    """Deep-freeze *value* into an immutable JSON-compatible structure.

    Parameters
    ----------
    value:
        Arbitrary value to freeze.
    field_name:
        Name of the field, used in error messages.

    Returns
    -------
    JsonValue
    """
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_json(item, field_name=field_name) for item in value)
    if isinstance(value, (dict, MappingProxyType)):
        return MappingProxyType(
            {str(k): _freeze_json(v, field_name=field_name) for k, v in value.items()}
        )
    try:
        return str(value)
    except Exception:
        raise_with_scope(
            "non-serialisable-json-value",
            message=(
                f"Field {field_name!r} contains a value of type "
                f"{type(value).__name__!r} that cannot be represented as JSON."
            ),
            scope=FailureScope.PACK,
            provenance={"field_name": field_name, "value_type": type(value).__name__},
        )


def _freeze_mapping(value: Any, *, field_name: str) -> Mapping[str, Any]:
    """Coerce *value* to a MappingProxyType.

    Parameters
    ----------
    value:
        A mapping-like object, or None (treated as empty mapping).
    field_name:
        Name of the field, used in error messages.

    Returns
    -------
    Mapping[str, Any]

    Raises
    ------
    jugeo.errors.JuGeoError
        If *value* is not None and not mapping-like.
    """
    if value is None:
        return _EMPTY_MAPPING
    if isinstance(value, MappingProxyType):
        return MappingProxyType({str(k): v for k, v in value.items()})
    if isinstance(value, dict):
        return MappingProxyType({str(k): v for k, v in value.items()})
    if hasattr(value, "items"):
        try:
            return MappingProxyType({str(k): v for k, v in value.items()})
        except Exception as exc:
            raise_with_scope(
                "mapping-coercion-failed",
                message=f"Field {field_name!r} could not be coerced to a mapping: {exc}.",
                scope=FailureScope.PACK,
                cause=exc,
                provenance={"field_name": field_name},
            )
    raise_with_scope(
        "non-mapping-field",
        message=(
            f"Field {field_name!r} expected a mapping; "
            f"received {type(value).__name__!r}."
        ),
        scope=FailureScope.PACK,
        provenance={"field_name": field_name, "value_type": type(value).__name__},
    )


def _thaw_json(value: Any) -> Any:
    """Recursively convert immutable JSON containers back to mutable ones.

    Parameters
    ----------
    value:
        Any frozen JSON value.

    Returns
    -------
    Any
    """
    if isinstance(value, (MappingProxyType, dict)):
        return {k: _thaw_json(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_thaw_json(item) for item in value]
    return value


def _assert_json_serializable(payload: Any, *, field_name: str) -> None:
    """Raise a JuGeoError if *payload* is not JSON-serialisable.

    Parameters
    ----------
    payload:
        The value to check.
    field_name:
        Name of the field, used in error messages.

    Raises
    ------
    jugeo.errors.JuGeoError
    """
    try:
        json.dumps(payload, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise_with_scope(
            "non-json-serialisable-field",
            message=(
                f"Field {field_name!r} contains data that is not JSON-serialisable: {exc}."
            ),
            scope=FailureScope.PACK,
            cause=exc,
            provenance={"field_name": field_name},
        )

# ---------------------------------------------------------------------------
# PackLaw
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PackLaw:
    """An individual law declared by a domain pack.

    A *law* is any named mathematical statement that a pack claims to uphold
    within its declared authority.  Laws range from bare axioms adopted without
    proof through theorems discharged by solver or oracle evidence to
    invariants that the runtime checks on every coordinate transition.

    Theory reference
    ----------------
    See ``theory2.tex`` §4.1 for the formal treatment of pack laws as global
    sections of the structure sheaf.

    Attributes
    ----------
    name : str
        Unique law identifier within the pack.
    statement : str
        Human-readable mathematical statement of the law.
    law_kind : str
        Broad classification: axiom, theorem, invariant, bridge, constraint, lemma.
    locality : str
        Scope: local, global, or contextual.
    evidence_channels : tuple[str, ...]
        Evidence channels that can discharge this law.
    status : str
        Current status of the law.
    metadata : Mapping[str, Any]
        Free-form metadata.
    """

    name: str
    statement: str
    law_kind: str = "theorem"
    locality: str = "local"
    evidence_channels: tuple[str, ...] = ()
    status: str = "active"
    metadata: Mapping[str, Any] = field(default_factory=dict)

    _VALID_LAW_KINDS: Final[frozenset[str]] = frozenset({
        "axiom", "theorem", "invariant", "bridge", "constraint", "lemma",
        "definition", "conjecture", "schema", "descent", "test",
    })
    _VALID_LOCALITIES: Final[frozenset[str]] = frozenset({
        "local", "global", "contextual", "relative", "absolute", "cover-local",
    })
    _VALID_STATUSES: Final[frozenset[str]] = frozenset({
        "active", "provisional", "deprecated", "experimental", "retracted",
        "pending", "admitted",
    })
    _VALID_EVIDENCE_CHANNELS: Final[frozenset[str]] = frozenset({
        "solver", "oracle", "copilot", "proof", "human", "runtime", "none", "test",
    })

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", _normalize_required_text(self.name, field_name="name"))
        object.__setattr__(self, "statement", _normalize_required_text(self.statement, field_name="statement"))
        object.__setattr__(self, "law_kind", _normalize_required_text(self.law_kind, field_name="law_kind"))
        object.__setattr__(self, "locality", _normalize_required_text(self.locality, field_name="locality"))
        object.__setattr__(self, "status", _normalize_required_text(self.status, field_name="status"))
        object.__setattr__(
            self, "evidence_channels",
            _normalize_text_tuple(self.evidence_channels, field_name="evidence_channels"),
        )
        object.__setattr__(self, "metadata", _freeze_mapping(self.metadata, field_name="metadata"))
        if self.law_kind not in self._VALID_LAW_KINDS:
            raise_with_scope(
                "invalid-law-kind",
                message=(
                    f"PackLaw {self.name!r} has unrecognised law_kind {self.law_kind!r}. "
                    f"Recognised kinds: {sorted(self._VALID_LAW_KINDS)}."
                ),
                scope=FailureScope.PACK,
                provenance={"law_name": self.name, "law_kind": self.law_kind},
            )
        if self.locality not in self._VALID_LOCALITIES:
            raise_with_scope(
                "invalid-law-locality",
                message=(
                    f"PackLaw {self.name!r} has unrecognised locality {self.locality!r}. "
                    f"Recognised localities: {sorted(self._VALID_LOCALITIES)}."
                ),
                scope=FailureScope.PACK,
                provenance={"law_name": self.name, "locality": self.locality},
            )
        if self.status not in self._VALID_STATUSES:
            raise_with_scope(
                "invalid-law-status",
                message=(
                    f"PackLaw {self.name!r} has unrecognised status {self.status!r}. "
                    f"Recognised statuses: {sorted(self._VALID_STATUSES)}."
                ),
                scope=FailureScope.PACK,
                provenance={"law_name": self.name, "status": self.status},
            )
        for ch in self.evidence_channels:
            if ch not in self._VALID_EVIDENCE_CHANNELS:
                raise_with_scope(
                    "invalid-evidence-channel",
                    message=(
                        f"PackLaw {self.name!r} references unknown evidence channel {ch!r}. "
                        f"Recognised channels: {sorted(self._VALID_EVIDENCE_CHANNELS)}."
                    ),
                    scope=FailureScope.PACK,
                    provenance={"law_name": self.name, "channel": ch},
                )

    def to_dict(self) -> dict[str, Any]:
        """Serialise this law to a plain dict.

        Returns
        -------
        dict[str, Any]
        """
        return {
            "name": self.name,
            "statement": self.statement,
            "law_kind": self.law_kind,
            "locality": self.locality,
            "evidence_channels": list(self.evidence_channels),
            "status": self.status,
            "metadata": _thaw_json(self.metadata),
        }

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "PackLaw":
        """Construct a PackLaw from a raw mapping.

        Parameters
        ----------
        data:
            Mapping with at minimum name and statement keys.

        Returns
        -------
        PackLaw
        """
        return cls(
            name=data.get("name", ""),
            statement=data.get("statement", ""),
            law_kind=str(data.get("law_kind", "theorem")),
            locality=str(data.get("locality", "local")),
            evidence_channels=tuple(data.get("evidence_channels", ())),
            status=str(data.get("status", "active")),
            metadata=dict(data.get("metadata", {})),
        )

# ---------------------------------------------------------------------------
# PackAdapter
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PackAdapter:
    """A morphism that translates between two domain kinds."""

    name: str
    source_kind: str = ""
    target_kind: str = ""
    adapter_kind: str = "projection"
    bidirectional: bool = False
    via_boundary: str = ""
    notes: tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    _VALID_ADAPTER_KINDS: Final[frozenset[str]] = frozenset({
        "projection", "embedding", "equivalence", "coercion",
        "lifting", "bridge", "functor", "natural-transformation",
        "adjunction", "retraction",
    })

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", _normalize_required_text(self.name, field_name="name"))
        object.__setattr__(self, "adapter_kind", _normalize_required_text(self.adapter_kind, field_name="adapter_kind"))
        object.__setattr__(self, "source_kind", str(self.source_kind or "").strip())
        object.__setattr__(self, "target_kind", str(self.target_kind or "").strip())
        object.__setattr__(self, "bidirectional", bool(self.bidirectional))
        object.__setattr__(self, "via_boundary", str(self.via_boundary or "").strip())
        object.__setattr__(self, "notes", _normalize_text_tuple(self.notes, field_name="notes"))
        object.__setattr__(self, "metadata", _freeze_mapping(self.metadata, field_name="metadata"))
        if self.adapter_kind not in self._VALID_ADAPTER_KINDS:
            raise_with_scope(
                "invalid-adapter-kind",
                message=(
                    f"PackAdapter {self.name!r} has unrecognised adapter_kind "
                    f"{self.adapter_kind!r}. "
                    f"Recognised kinds: {sorted(self._VALID_ADAPTER_KINDS)}."
                ),
                scope=FailureScope.PACK,
                provenance={"adapter_name": self.name, "adapter_kind": self.adapter_kind},
            )

    def supports(self, source_kind: str, target_kind: str) -> bool:
        return self.source_kind == source_kind and self.target_kind == target_kind

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "source_kind": self.source_kind,
            "target_kind": self.target_kind,
            "adapter_kind": self.adapter_kind,
            "bidirectional": self.bidirectional,
            "via_boundary": self.via_boundary,
            "notes": list(self.notes),
            "metadata": _thaw_json(self.metadata),
        }

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "PackAdapter":
        return cls(
            name=data.get("name", ""),
            source_kind=str(data.get("source_kind", "")),
            target_kind=str(data.get("target_kind", "")),
            adapter_kind=str(data.get("adapter_kind", "projection")),
            bidirectional=bool(data.get("bidirectional", False)),
            via_boundary=str(data.get("via_boundary", "")),
            notes=tuple(data.get("notes", ())),
            metadata=dict(data.get("metadata", {})),
        )

# ---------------------------------------------------------------------------
# PackBoundary
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True, init=False)
class PackBoundary:
    """A boundary record demarcating a pack's federation scope."""

    boundary_id: str
    authority: str = "provisional"
    inbound_packs: tuple[str, ...] = ()
    outbound_packs: tuple[str, ...] = ()
    ingress_kinds: tuple[str, ...] = ()
    egress_kinds: tuple[str, ...] = ()
    trust_channels: tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __init__(
        self,
        boundary_id: str,
        authority: str = "provisional",
        inbound_packs: Sequence[str] = (),
        outbound_packs: Sequence[str] = (),
        ingress_kinds: Sequence[str] = (),
        egress_kinds: Sequence[str] = (),
        trust_channels: Sequence[str] = (),
        metadata: Mapping[str, Any] | None = None,
        *,
        source_pack: str = "",
        target_pack: str = "",
        allowed_kinds: Sequence[str] = (),
        restricted_kinds: Sequence[str] = (),
    ) -> None:
        if source_pack:
            inbound_packs = tuple(set(inbound_packs) | {source_pack})
        if target_pack:
            outbound_packs = tuple(set(outbound_packs) | {target_pack})
        if allowed_kinds:
            ingress_kinds = tuple(allowed_kinds)
        merged_metadata = dict(metadata or {})
        if restricted_kinds:
            merged_metadata["restricted_kinds"] = tuple(restricted_kinds)
        object.__setattr__(self, "boundary_id", boundary_id)
        object.__setattr__(self, "authority", authority)
        object.__setattr__(self, "inbound_packs", tuple(inbound_packs))
        object.__setattr__(self, "outbound_packs", tuple(outbound_packs))
        object.__setattr__(self, "ingress_kinds", tuple(ingress_kinds))
        object.__setattr__(self, "egress_kinds", tuple(egress_kinds))
        object.__setattr__(self, "trust_channels", tuple(trust_channels))
        object.__setattr__(self, "metadata", merged_metadata)
        self.__post_init__()

    def __post_init__(self) -> None:
        object.__setattr__(self, "boundary_id", _normalize_required_text(self.boundary_id, field_name="boundary_id"))
        object.__setattr__(self, "authority", str(self.authority or "provisional").strip() or "provisional")
        object.__setattr__(self, "inbound_packs", _normalize_text_tuple(self.inbound_packs, field_name="inbound_packs"))
        object.__setattr__(self, "outbound_packs", _normalize_text_tuple(self.outbound_packs, field_name="outbound_packs"))
        object.__setattr__(self, "ingress_kinds", _normalize_text_tuple(self.ingress_kinds, field_name="ingress_kinds"))
        object.__setattr__(self, "egress_kinds", _normalize_text_tuple(self.egress_kinds, field_name="egress_kinds"))
        object.__setattr__(self, "trust_channels", _normalize_text_tuple(self.trust_channels, field_name="trust_channels"))
        object.__setattr__(self, "metadata", _freeze_mapping(self.metadata, field_name="metadata"))

    def to_dict(self) -> dict[str, Any]:
        return {
            "boundary_id": self.boundary_id,
            "authority": self.authority,
            "inbound_packs": list(self.inbound_packs),
            "outbound_packs": list(self.outbound_packs),
            "ingress_kinds": list(self.ingress_kinds),
            "egress_kinds": list(self.egress_kinds),
            "trust_channels": list(self.trust_channels),
            "source_pack": self.source_pack,
            "target_pack": self.target_pack,
            "allowed_kinds": list(self.allowed_kinds),
            "restricted_kinds": list(self.restricted_kinds),
            "metadata": _thaw_json(self.metadata),
        }

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "PackBoundary":
        return cls(
            boundary_id=data.get("boundary_id", ""),
            authority=str(data.get("authority", "provisional")),
            inbound_packs=tuple(data.get("inbound_packs", ())),
            outbound_packs=tuple(data.get("outbound_packs", ())),
            ingress_kinds=tuple(data.get("ingress_kinds", ())),
            egress_kinds=tuple(data.get("egress_kinds", ())),
            trust_channels=tuple(data.get("trust_channels", ())),
            metadata=dict(data.get("metadata", {})),
        )

    @property
    def source_pack(self) -> str:
        return self.inbound_packs[0] if self.inbound_packs else ""

    @property
    def target_pack(self) -> str:
        return self.outbound_packs[0] if self.outbound_packs else ""

    @property
    def allowed_kinds(self) -> tuple[str, ...]:
        return self.ingress_kinds

    @property
    def restricted_kinds(self) -> tuple[str, ...]:
        value = self.metadata.get("restricted_kinds", ())
        if isinstance(value, tuple):
            return value
        if isinstance(value, list):
            return tuple(str(item) for item in value)
        return ()

# ---------------------------------------------------------------------------
# Helpers that depend on PackLaw / PackAdapter / PackBoundary
# ---------------------------------------------------------------------------


def _normalize_laws(values: Any) -> tuple[PackLaw, ...]:
    """Coerce *values* to a tuple of PackLaw instances.

    Parameters
    ----------
    values:
        Iterable of PackLaw objects or raw mappings, or None.

    Returns
    -------
    tuple[PackLaw, ...]
    """
    if values is None:
        return ()
    result: list[PackLaw] = []
    for item in values:
        if isinstance(item, PackLaw):
            result.append(item)
        elif isinstance(item, dict) or hasattr(item, "items"):
            result.append(PackLaw.from_mapping(item))
        else:
            raise_with_scope(
                "invalid-law-entry",
                message=(
                    f"Expected a PackLaw or mapping for laws entry; "
                    f"received {type(item).__name__!r}."
                ),
                scope=FailureScope.PACK,
                provenance={"value_type": type(item).__name__},
            )
    return tuple(result)


def _normalize_adapters(values: Any) -> tuple[PackAdapter, ...]:
    """Coerce *values* to a tuple of PackAdapter instances.

    Parameters
    ----------
    values:
        Iterable of PackAdapter objects or raw mappings, or None.

    Returns
    -------
    tuple[PackAdapter, ...]
    """
    if values is None:
        return ()
    result: list[PackAdapter] = []
    for item in values:
        if isinstance(item, PackAdapter):
            result.append(item)
        elif isinstance(item, dict) or hasattr(item, "items"):
            result.append(PackAdapter.from_mapping(item))
        else:
            raise_with_scope(
                "invalid-adapter-entry",
                message=(
                    f"Expected a PackAdapter or mapping for adapters entry; "
                    f"received {type(item).__name__!r}."
                ),
                scope=FailureScope.PACK,
                provenance={"value_type": type(item).__name__},
            )
    return tuple(result)


def _normalize_boundaries(values: Any) -> tuple[PackBoundary, ...]:
    """Coerce *values* to a tuple of PackBoundary instances.

    Parameters
    ----------
    values:
        Iterable of PackBoundary objects or raw mappings, or None.

    Returns
    -------
    tuple[PackBoundary, ...]
    """
    if values is None:
        return ()
    result: list[PackBoundary] = []
    for item in values:
        if isinstance(item, PackBoundary):
            result.append(item)
        elif isinstance(item, dict) or hasattr(item, "items"):
            result.append(PackBoundary.from_mapping(item))
        else:
            raise_with_scope(
                "invalid-boundary-entry",
                message=(
                    f"Expected a PackBoundary or mapping for boundaries entry; "
                    f"received {type(item).__name__!r}."
                ),
                scope=FailureScope.PACK,
                provenance={"value_type": type(item).__name__},
            )
    return tuple(result)


def _authority_rank(authority: str) -> int:
    """Return the numeric rank of *authority* within KNOWN_AUTHORITY_LEVELS.

    Lower index corresponds to higher inherent authority.  Unknown authority
    strings are assigned the highest (least-trusted) rank index.

    Parameters
    ----------
    authority:
        One of the strings in KNOWN_AUTHORITY_LEVELS.

    Returns
    -------
    int
    """
    try:
        return KNOWN_AUTHORITY_LEVELS.index(authority)
    except ValueError:
        return -1  # unknown = lowest rank

# ---------------------------------------------------------------------------
# PackDescriptor
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PackDescriptor:
    """Immutable top-level descriptor for a JuGeo domain pack."""

    name: str = ""
    version: str = "0.0.0"
    capabilities: tuple[str, ...] = ()
    exported_kinds: tuple[str, ...] = ()
    dependencies: tuple[str, ...] = ()
    authority: str = "foundational"
    metadata: Mapping[str, Any] = field(default_factory=dict)
    description: str = ""
    site_region: str = ""
    cover_name: str = ""
    admissible_contexts: tuple[str, ...] = ()
    laws: tuple[PackLaw, ...] = ()
    routing_policies: Mapping[str, Any] = field(default_factory=dict)
    bridge_slots: tuple[str, ...] = ()
    adapters: tuple[PackAdapter, ...] = ()
    federation_boundaries: tuple[PackBoundary, ...] = ()
    provenance: Mapping[str, Any] = field(default_factory=dict)
    trust: Mapping[str, Any] = field(default_factory=dict)
    seal: str = field(default="", init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", str(self.name or "").strip())
        object.__setattr__(self, "version", str(self.version or "0.0.0").strip() or "0.0.0")
        object.__setattr__(self, "description", str(self.description or "").strip())
        object.__setattr__(self, "authority", str(self.authority or "foundational").strip() or "foundational")
        object.__setattr__(self, "site_region", str(self.site_region or "").strip())
        object.__setattr__(self, "cover_name", str(self.cover_name or "").strip())
        object.__setattr__(
            self, "capabilities",
            _normalize_text_tuple(self.capabilities, field_name="capabilities"),
        )
        object.__setattr__(
            self, "exported_kinds",
            _normalize_text_tuple(self.exported_kinds, field_name="exported_kinds"),
        )
        object.__setattr__(
            self, "dependencies",
            _normalize_text_tuple(self.dependencies, field_name="dependencies"),
        )
        object.__setattr__(
            self, "admissible_contexts",
            _normalize_text_tuple(self.admissible_contexts, field_name="admissible_contexts"),
        )
        object.__setattr__(
            self, "bridge_slots",
            _normalize_text_tuple(self.bridge_slots, field_name="bridge_slots"),
        )
        object.__setattr__(self, "laws", _normalize_laws(self.laws))
        object.__setattr__(self, "adapters", _normalize_adapters(self.adapters))
        object.__setattr__(self, "federation_boundaries", _normalize_boundaries(self.federation_boundaries))
        object.__setattr__(self, "trust", _freeze_mapping(self.trust, field_name="trust"))
        object.__setattr__(
            self, "routing_policies",
            _freeze_mapping(self.routing_policies, field_name="routing_policies"),
        )
        object.__setattr__(
            self, "provenance",
            _freeze_mapping(self.provenance, field_name="provenance"),
        )
        # Compute seal
        computed_seal = f"pack:{self.name}@{self.version}"
        object.__setattr__(self, "seal", computed_seal)
        # Inject seal into metadata
        existing_meta = dict(self.metadata) if self.metadata else {}
        existing_meta["seal"] = computed_seal
        object.__setattr__(self, "metadata", _freeze_mapping(existing_meta, field_name="metadata"))

    @property
    def catalog_key(self) -> str:
        return f"{self.name}@{self.version}"

    def is_admissible(self, context: str) -> bool:
        if not self.admissible_contexts:
            return True
        return context in self.admissible_contexts

    def has_bridge_slot(self, slot: str) -> bool:
        return slot in self.bridge_slots

    def satisfies(self, capability: str) -> bool:
        return capability in self.capabilities

    def depends_on(self, other_name: str) -> bool:
        return other_name in self.dependencies

    def version_tuple(self) -> tuple[int, ...]:
        parts: list[int] = []
        for segment in self.version.split("."):
            try:
                parts.append(int(segment))
            except ValueError:
                parts.append(0)
        return tuple(parts)

    def bridge_names(self) -> tuple[str, ...]:
        return tuple(self.bridge_slots)

    def law_names(self) -> tuple[str, ...]:
        return tuple(law.name for law in self.laws)

    def adapter_names(self) -> tuple[str, ...]:
        return tuple(adapter.name for adapter in self.adapters)

    def boundary_ids(self) -> tuple[str, ...]:
        return tuple(b.boundary_id for b in self.federation_boundaries)

    def to_theory_record(self) -> dict[str, Any]:
        return {
            "region": self.site_region,
            "cover": self.cover_name,
            "surface": self.description or self.name,
            "name": self.name,
            "version": self.version,
            "authority": self.authority,
            "laws": [law.name for law in self.laws],
            "adapters": [adapter.name for adapter in self.adapters],
            "bridges": list(self.bridge_slots),
            "seal": self.seal,
        }

    def validation_issues(self) -> list[str]:
        issues: list[str] = []
        if not self.exported_kinds:
            issues.append("missing-exported-kinds")
        if self.name and self.name in self.dependencies:
            issues.append("self-dependency")
        return issues

    def authority_rank(self) -> int:
        return KNOWN_AUTHORITY_LEVELS.index(self.authority) if self.authority in KNOWN_AUTHORITY_LEVELS else -1

    def exports_kind(self, kind: str) -> bool:
        return kind in self.exported_kinds

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "capabilities": list(self.capabilities),
            "exported_kinds": list(self.exported_kinds),
            "dependencies": list(self.dependencies),
            "authority": self.authority,
            "metadata": dict(self.metadata),
            "description": self.description,
            "site_region": self.site_region,
            "cover_name": self.cover_name,
            "admissible_contexts": list(self.admissible_contexts),
            "laws": [law.to_dict() for law in self.laws],
            "routing_policies": _thaw_json(self.routing_policies),
            "bridge_slots": list(self.bridge_slots),
            "adapters": [adapter.to_dict() for adapter in self.adapters],
            "federation_boundaries": [b.to_dict() for b in self.federation_boundaries],
            "provenance": _thaw_json(self.provenance),
            "trust": _thaw_json(self.trust),
            "seal": self.seal,
        }

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "PackDescriptor":
        laws_raw = data.get("laws") or []
        adapters_raw = data.get("adapters") or []
        boundaries_raw = data.get("federation_boundaries") or []
        laws = [
            item if isinstance(item, PackLaw) else PackLaw.from_mapping(item)
            for item in laws_raw
        ]
        adapters = [
            item if isinstance(item, PackAdapter) else PackAdapter.from_mapping(item)
            for item in adapters_raw
        ]
        boundaries = [
            item if isinstance(item, PackBoundary) else PackBoundary.from_mapping(item)
            for item in boundaries_raw
        ]
        # Strip "seal" from metadata so it gets recomputed
        meta = dict(data.get("metadata") or {})
        meta.pop("seal", None)
        return cls(
            name=str(data.get("name", "")),
            version=str(data.get("version", "0.0.0")),
            capabilities=tuple(data.get("capabilities", ())),
            exported_kinds=tuple(data.get("exported_kinds", ())),
            dependencies=tuple(data.get("dependencies", ())),
            authority=str(data.get("authority", "foundational")),
            metadata=meta,
            description=str(data.get("description", "")),
            site_region=str(data.get("site_region", "")),
            cover_name=str(data.get("cover_name", "")),
            admissible_contexts=tuple(data.get("admissible_contexts", ())),
            laws=tuple(laws),
            routing_policies=dict(data.get("routing_policies") or {}),
            bridge_slots=tuple(data.get("bridge_slots", ())),
            adapters=tuple(adapters),
            federation_boundaries=tuple(boundaries),
            provenance=dict(data.get("provenance") or {}),
            trust=dict(data.get("trust") or {}),
        )

# ---------------------------------------------------------------------------
# PackCatalog
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class PackCatalog:
    """Mutable registry of PackDescriptor instances.

    The catalog is the authoritative runtime store of all known packs.  It
    provides name and version resolution, dependency ordering, export index
    building, and validation.

    Packs are keyed by their PackDescriptor.catalog_key ("name@version").
    The get and require methods also accept bare names, resolving unambiguously
    when there is exactly one version of a pack registered.

    Attributes
    ----------
    descriptors : dict[str, PackDescriptor]
        Internal mapping from catalog_key to descriptor.

    Examples
    --------
    >>> cat = PackCatalog()
    >>> desc = PackDescriptor(name="mypack", version="1.0.0")
    >>> cat.register(desc)
    >>> cat.get("mypack")
    PackDescriptor(name='mypack', ...)
    """

    descriptors: dict[str, PackDescriptor] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # Mutation
    # ------------------------------------------------------------------

    def register(self, descriptor: PackDescriptor, replace: bool = False) -> None:
        """Register *descriptor*."""
        key = descriptor.catalog_key
        existing = self.descriptors.get(key)
        if existing is not None:
            if existing == descriptor or replace:
                self.descriptors[key] = descriptor
                return
            raise_with_scope(
                "duplicate-pack-registration",
                message=(
                    f"Pack {key!r} is already registered in this catalog. "
                    "Pass replace=True to overwrite."
                ),
                scope=FailureScope.PACK,
                provenance={"catalog_key": key, "pack_name": descriptor.name},
            )

        same_name = [
            registered
            for registered in self.descriptors.values()
            if registered.name == descriptor.name
        ]
        if same_name and not replace:
            semver_like = "." in descriptor.version and all(
                "." in registered.version for registered in same_name
            )
            if any(registered != descriptor for registered in same_name) and not semver_like:
                raise_with_scope(
                    "conflicting-pack-registration",
                    message=(
                        f"Pack name {descriptor.name!r} is already registered with "
                        f"version(s) {[registered.version for registered in same_name]!r}; "
                        "pass replace=True to replace the registered descriptor."
                    ),
                    scope=FailureScope.PACK,
                    provenance={
                        "pack_name": descriptor.name,
                        "catalog_key": key,
                        "registered_versions": [registered.version for registered in same_name],
                        "incoming_version": descriptor.version,
                    },
                )
            if semver_like:
                self.descriptors[key] = descriptor
                return
            return

        if replace:
            for existing_key, registered in tuple(self.descriptors.items()):
                if registered.name == descriptor.name and existing_key != key:
                    del self.descriptors[existing_key]
        self.descriptors[key] = descriptor

    def extend(self, descriptors: Sequence[PackDescriptor], replace: bool = False) -> None:
        """Register each descriptor in *descriptors*.

        Parameters
        ----------
        descriptors:
            Iterable of PackDescriptor objects to register.
        replace:
            Forwarded to each register call.
        """
        for desc in descriptors:
            self.register(desc, replace=replace)

    # ------------------------------------------------------------------
    # Lookup
    # ------------------------------------------------------------------

    def get(self, reference: str, version: str | None = None) -> "PackDescriptor | None":
        """Return the PackDescriptor for *reference*, or None.

        *reference* may be a full catalog key ("name@version") or a bare
        pack name resolved unambiguously when exactly one version is registered.

        Parameters
        ----------
        reference:
            Pack name or catalog key.

        Returns
        -------
        PackDescriptor | None
        """
        if version is not None:
            return self.descriptors.get(f"{reference}@{version}")
        key = self._resolve_key(reference)
        if key is None:
            return None
        return self.descriptors.get(key)

    def require(self, reference: str, version: str | None = None) -> PackDescriptor:
        """Return the PackDescriptor for *reference* or raise.

        Parameters
        ----------
        reference:
            Pack name or catalog key.

        Returns
        -------
        PackDescriptor

        Raises
        ------
        jugeo.errors.JuGeoError
            If *reference* cannot be resolved.
        """
        desc = self.get(reference, version=version)
        if desc is None:
            raise_with_scope(
                "unknown-pack",
                message=(
                    f"Unknown JuGeo pack {reference!r}. "
                    f"Registered packs: {sorted(self.descriptors.keys())}."
                ),
                scope=FailureScope.PACK,
                coordinate=reference,
                provenance={
                    "reference": reference,
                    "registered_keys": sorted(self.descriptors.keys()),
                },
            )
        return desc  # type: ignore[return-value]

    def maybe_get(self, reference: str, version: str | None = None) -> "PackDescriptor | None":
        """Alias for get.

        Parameters
        ----------
        reference:
            Pack name or catalog key.

        Returns
        -------
        PackDescriptor | None
        """
        return self.get(reference, version=version)

    # ------------------------------------------------------------------
    # Iteration helpers
    # ------------------------------------------------------------------

    def keys(self) -> tuple[str, ...]:
        """Return sorted catalog keys for all registered descriptors.

        Returns
        -------
        tuple[str, ...]
        """
        return tuple(sorted(self.descriptors.keys()))

    def names(self) -> tuple[str, ...]:
        """Return sorted unique pack names for all registered descriptors.

        Returns
        -------
        tuple[str, ...]
        """
        return tuple(sorted({desc.name for desc in self.descriptors.values()}))

    def list_descriptors(self) -> tuple[PackDescriptor, ...]:
        """Return all registered PackDescriptor instances.

        Returns
        -------
        tuple[PackDescriptor, ...]
        """
        return tuple(self.descriptors.values())

    def __iter__(self) -> Iterator[PackDescriptor]:
        return iter(self.descriptors.values())

    def __len__(self) -> int:
        return len(self.descriptors)

    def __contains__(self, reference: object) -> bool:
        if not isinstance(reference, str):
            return False
        return self._resolve_key(reference) is not None

    # ------------------------------------------------------------------
    # Resolution internals
    # ------------------------------------------------------------------

    def _resolve_key(self, reference: str) -> "str | None":
        """Resolve *reference* to a catalog key.

        If *reference* is already a registered key ("name@version"), it is
        returned as-is.  If it is a bare name, this method checks whether
        exactly one version of that name is registered.  If multiple versions
        exist the call raises an ambiguity error.

        Parameters
        ----------
        reference:
            Pack name or catalog key.

        Returns
        -------
        str | None

        Raises
        ------
        jugeo.errors.JuGeoError
            If the bare name matches multiple registered catalog keys.
        """
        if reference in self.descriptors:
            return reference
        matching = [
            key for key, desc in self.descriptors.items()
            if desc.name == reference
        ]
        if len(matching) == 1:
            return matching[0]
        if len(matching) > 1:
            matching.sort(key=lambda item: self.descriptors[item].version_tuple(), reverse=True)
            return matching[0]
        return None

    def all(self) -> list[PackDescriptor]:
        return list(self.descriptors.values())

    def latest(self, name: str) -> PackDescriptor | None:
        return self.get(name)

    # ------------------------------------------------------------------
    # Dependency ordering
    # ------------------------------------------------------------------

    def dependency_closure(self, roots: Sequence[str]) -> tuple[str, ...]:
        """Return a topologically sorted dependency closure for *roots*.

        The closure includes *roots* themselves.  Each element is a pack name.
        Ordering guarantees that dependencies appear before the packs that
        declare them.

        Parameters
        ----------
        roots:
            Pack names (or catalog keys) to start the closure from.

        Returns
        -------
        tuple[str, ...]
            Topologically sorted pack names.

        Raises
        ------
        jugeo.errors.JuGeoError
            If a cycle is detected or a dependency cannot be found.
        """
        name_map: dict[str, PackDescriptor] = {}
        for desc in self.descriptors.values():
            existing = name_map.get(desc.name)
            if existing is None or desc.version_tuple() > existing.version_tuple():
                name_map[desc.name] = desc

        root_names: list[str] = []
        for ref in roots:
            desc = self.get(ref)
            if desc is None:
                raise_with_scope(
                    "unknown-pack",
                    message=f"Unknown JuGeo pack {ref!r} in dependency_closure roots.",
                    scope=FailureScope.PACK,
                    provenance={"reference": ref},
                )
            root_names.append(desc.name)  # type: ignore[union-attr]

        visited: set[str] = set()
        order: list[str] = []
        visiting: set[str] = set()

        def visit(name: str) -> None:
            if name in visiting:
                raise_with_scope(
                    "circular-pack-dependency",
                    message=(
                        f"Circular dependency detected involving pack {name!r}. "
                        f"Visitation path: {sorted(visiting)}."
                    ),
                    scope=FailureScope.PACK,
                    provenance={
                        "pack_name": name,
                        "visiting_set": sorted(visiting),
                    },
                )
            if name in visited:
                return
            visiting.add(name)
            desc = name_map.get(name)
            if desc is None:
                raise_with_scope(
                    "unknown-pack",
                    message=(
                        f"Unknown JuGeo pack {name!r} encountered during "
                        "dependency_closure traversal."
                    ),
                    scope=FailureScope.PACK,
                    provenance={"pack_name": name},
                )
            for dep in desc.dependencies:  # type: ignore[union-attr]
                if dep not in name_map:
                    raise_with_scope(
                        "unknown-pack",
                        message=(
                            f"Pack {name!r} declares dependency on {dep!r}, "
                            "which is not registered in this catalog."
                        ),
                        scope=FailureScope.PACK,
                        provenance={"pack_name": name, "dependency": dep},
                    )
                visit(dep)
            visiting.discard(name)
            visited.add(name)
            if "." in desc.version:
                order.append(f"{desc.name}@{desc.version}")
            else:
                order.append(desc.name)

        for root in root_names:
            visit(root)

        return tuple(order)

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Serialise this catalog to a plain dict.

        The "descriptors" sub-dict uses pack names (not catalog keys) as keys.

        Returns
        -------
        dict[str, Any]
        """
        descriptors_by_name: dict[str, Any] = {}
        for key in sorted(self.descriptors.keys()):
            desc = self.descriptors[key]
            descriptors_by_name[desc.name] = desc.to_dict()
        return {
            "descriptors": descriptors_by_name,
            "exported_kind_index": {
                kind: sorted(names)
                for kind, names in self.exported_kind_index().items()
            },
        }

    # ------------------------------------------------------------------
    # Summary and validation
    # ------------------------------------------------------------------

    def summary(self) -> dict[str, Any]:
        """Return a human-readable summary of this catalog.

        Returns
        -------
        dict[str, Any]
        """
        by_authority: dict[str, list[str]] = defaultdict(list)
        for desc in self.descriptors.values():
            by_authority[desc.authority].append(desc.name)

        packs_info = []
        for key in sorted(self.descriptors.keys()):
            desc = self.descriptors[key]
            packs_info.append({
                "name": desc.name,
                "version": desc.version,
                "authority": desc.authority,
                "law_count": len(desc.laws),
                "adapter_count": len(desc.adapters),
                "boundary_count": len(desc.federation_boundaries),
                "bridge_slots": list(desc.bridge_slots),
                "capabilities": list(desc.capabilities),
                "exported_kinds": list(desc.exported_kinds),
                "dependencies": list(desc.dependencies),
            })

        return {
            "pack_count": len(self.descriptors),
            "packs": packs_info,
            "by_authority": {k: sorted(v) for k, v in sorted(by_authority.items())},
            "issues": self.validate(),
        }

    def validate(self) -> list[str]:
        """Validate the catalog and return a list of issue strings."""
        issues: list[str] = []
        all_names = {desc.name for desc in self.descriptors.values()}

        for key, desc in sorted(self.descriptors.items()):
            if not desc.name:
                issues.append(f"Descriptor {key!r} has an empty name.")
            for dep in desc.dependencies:
                if dep not in all_names:
                    issues.append(f"{desc.name}:missing-dependency:{dep}")
            if desc.authority not in KNOWN_AUTHORITY_LEVELS:
                issues.append(
                    f"Pack {desc.name!r} has unrecognised authority level {desc.authority!r}."
                )
        return issues

    # ------------------------------------------------------------------
    # Kind index
    # ------------------------------------------------------------------

    def exported_kind_index(self) -> dict[str, tuple[str, ...]]:
        """Build a reverse index mapping domain kind to pack names."""
        index: dict[str, set[str]] = defaultdict(set)
        for desc in self.descriptors.values():
            for kind in desc.exported_kinds:
                index[kind].add(desc.name)
            for slot in desc.bridge_slots:
                index[f"bridge:{slot}"].add(desc.name)
        return {kind: tuple(sorted(names)) for kind, names in index.items()}

    def federation_boundaries(self, authority: str | None = None) -> tuple[PackBoundary, ...]:
        """Return all PackBoundary instances across all packs, optionally filtered by authority."""
        result: list[PackBoundary] = []
        for desc in self.descriptors.values():
            for boundary in desc.federation_boundaries:
                if authority is None or boundary.authority == authority:
                    result.append(boundary)
        return tuple(result)

    def packs_exporting(self, kind: str) -> list[PackDescriptor]:
        """Return descriptors of packs exporting the given kind."""
        return [desc for desc in self.descriptors.values() if kind in desc.exported_kinds]

    def adapters_for(
        self,
        source_kind: str | None = None,
        target_kind: str | None = None,
        minimum_authority: str | None = None,
    ) -> list[PackAdapter]:
        """Return adapters matching the given criteria across all packs."""
        result: list[PackAdapter] = []
        for desc in self.descriptors.values():
            if minimum_authority is not None:
                if _authority_rank(desc.authority) < _authority_rank(minimum_authority):
                    continue
            for adapter in desc.adapters:
                if source_kind is not None and adapter.source_kind != source_kind:
                    continue
                if target_kind is not None and adapter.target_kind != target_kind:
                    continue
                result.append(adapter)
        return result

# ---------------------------------------------------------------------------
# Helper functions for default descriptors
# ---------------------------------------------------------------------------


def _default_trust_metadata() -> Mapping[str, JsonValue]:
    """Return a default trust metadata mapping for built-in packs.

    Populates trust settings from the current runtime defaults so that the
    built-in pack trust posture stays aligned with the system configuration.

    Returns
    -------
    Mapping[str, JsonValue]
        Immutable mapping with trust ceiling, floor, and policy keys.
    """
    opts = default_runtime_options().get_all()
    trust_policy = opts.get("trust_policy", {})
    return MappingProxyType({
        "ceiling": "verified",
        "floor": "provisional",
        "silent_promotion_allowed": trust_policy.get("silent_promotion_allowed", False),
        "oracle_ceiling_below_solver": trust_policy.get("oracle_ceiling_below_solver", True),
        "require_justification_for_promotion": trust_policy.get(
            "require_justification_for_promotion", True
        ),
    })


def _default_descriptor(name: str, **kwargs: Any) -> PackDescriptor:
    if "trust" not in kwargs:
        kwargs["trust"] = {"preset": "balanced", "policy": "strict", "trust_floor": "residual"}
    if "provenance" not in kwargs:
        kwargs["provenance"] = dict(PACK_SPEC_PROVENANCE)
    # Inject standard metadata
    meta = dict(kwargs.pop("metadata", {}) or {})
    if "stage" not in meta:
        meta["stage"] = "shared-packs"
    if "module_root" not in meta:
        meta["module_root"] = f"src/jugeo/{name}"
    if "test_root" not in meta:
        meta["test_root"] = f"tests/jugeo/{name}"
    if "future_surface" not in meta:
        meta["future_surface"] = (f"src/jugeo/{name}/__init__.py",)
    kwargs["metadata"] = meta
    return PackDescriptor(name=name, **kwargs)

# ---------------------------------------------------------------------------
# Built-in pack definitions
# ---------------------------------------------------------------------------


def _build_kernel_pack() -> PackDescriptor:
    laws = [
        PackLaw(
            name="coordinate-uniqueness",
            statement=(
                "Every coordinate in the JuGeo site is uniquely identified by "
                "its structural fingerprint.  No two distinct coordinates share "
                "an identical fingerprint within a single session."
            ),
            law_kind="axiom",
            locality="global",
            evidence_channels=("proof",),
            status="active",
        ),
        PackLaw(
            name="evidence-monotonicity",
            statement=(
                "Adding evidence to a coordinate never decreases the total "
                "evidence weight.  Evidence may be retracted only through an "
                "explicit retraction event with provenance."
            ),
            law_kind="axiom",
            locality="global",
            evidence_channels=("proof",),
            status="active",
        ),
        PackLaw(
            name="trust-non-promotion",
            statement=(
                "No pack may silently promote the trust tier of a coordinate.  "
                "Promotion requires explicit authorisation from the federation "
                "authority and a non-empty provenance record."
            ),
            law_kind="invariant",
            locality="global",
            evidence_channels=("runtime", "proof"),
            status="active",
        ),
        PackLaw(
            name="provenance-preservation",
            statement=(
                "Every operation that modifies a coordinate's evidence record "
                "must append a provenance entry identifying the modifying pack, "
                "the operation kind, and the evidence channel used."
            ),
            law_kind="invariant",
            locality="global",
            evidence_channels=("runtime",),
            status="active",
        ),
        PackLaw(
            name="coordinate-descent",
            statement=(
                "For any covering family of a coordinate, the local data on "
                "each element of the cover glues uniquely to global data on the "
                "covered coordinate, subject to the pack's declared gluing laws."
            ),
            law_kind="axiom",
            locality="global",
            evidence_channels=("proof",),
            status="active",
        ),
        PackLaw(
            name="session-isolation",
            statement=(
                "Data generated within a session is isolated from other "
                "concurrent sessions unless explicitly exported through a "
                "federation bridge with mutual consent."
            ),
            law_kind="invariant",
            locality="global",
            evidence_channels=("runtime",),
            status="active",
        ),
    ]
    adapters = [
        PackAdapter("coordinate-to-dict", "coordinate", "dict", adapter_kind="projection"),
        PackAdapter("dict-to-coordinate", "dict", "coordinate", adapter_kind="embedding"),
    ]
    boundaries = [
        PackBoundary(
            "kernel-session-boundary",
            "provisional",
            egress_kinds=("coordinate",),
            trust_channels=("runtime",),
        ),
        PackBoundary(
            "kernel-trust-boundary",
            "provisional",
            ingress_kinds=("trust-tier",),
            trust_channels=("proof",),
        ),
    ]
    return _default_descriptor(
        name="kernel",
        version="0.1.0",
        capabilities=("coordinate", "session", "provenance", "trust-accounting", "gluing", "evidence-record"),
        exported_kinds=("kernel.coordinate", "kernel.session", "kernel.provenance"),
        description="Foundational kernel pack providing the core coordinate machinery.",
        authority="foundational",
        site_region="core",
        cover_name="kernel-cover",
        bridge_slots=("coordinate-bridge", "session-bridge"),
        laws=tuple(laws),
        adapters=tuple(adapters),
        federation_boundaries=tuple(boundaries),
    )


def _build_geometry_pack() -> PackDescriptor:
    laws = [
        PackLaw(
            name="euclidean-metric",
            statement=(
                "The standard Euclidean metric on R^n satisfies the axioms of "
                "a complete metric space."
            ),
            law_kind="axiom",
            locality="local",
            evidence_channels=("proof", "solver"),
            status="active",
        ),
        PackLaw(
            name="affine-invariance",
            statement=(
                "Geometric objects and their relationships are invariant under "
                "affine transformations of the coordinate frame."
            ),
            law_kind="invariant",
            locality="contextual",
            evidence_channels=("proof",),
            status="active",
        ),
        PackLaw(
            name="convexity-stability",
            statement=(
                "The intersection of convex sets is convex.  The image of a "
                "convex set under an affine map is convex."
            ),
            law_kind="theorem",
            locality="local",
            evidence_channels=("proof", "solver"),
            status="active",
        ),
        PackLaw(
            name="geometric-descent",
            statement=(
                "Local geometric data on an open cover of a manifold glues "
                "to global data if and only if the cocycle condition is satisfied."
            ),
            law_kind="descent",
            locality="global",
            evidence_channels=("proof",),
            status="active",
        ),
        PackLaw(
            name="dimensionality-consistency",
            statement=(
                "All geometric objects participating in a single coordinate "
                "must agree on the ambient dimension."
            ),
            law_kind="invariant",
            locality="local",
            evidence_channels=("runtime", "solver"),
            status="active",
        ),
    ]
    adapters = [
        PackAdapter("euclidean-to-affine", "euclidean-space", "affine-space", adapter_kind="embedding", bidirectional=True),
        PackAdapter("affine-to-projective", "affine-space", "projective-space", adapter_kind="embedding"),
        PackAdapter("metric-projection", "metric-space", "topological-space", adapter_kind="projection"),
    ]
    boundaries = [
        PackBoundary(
            "geometry-dimension-boundary",
            "provisional",
            ingress_kinds=("geometry.manifold",),
            trust_channels=("proof",),
        ),
        PackBoundary(
            "geometry-euclidean-boundary",
            "provisional",
            egress_kinds=("geometry.euclidean-space",),
            trust_channels=("solver",),
        ),
    ]
    return _default_descriptor(
        name="geometry",
        version="0.1.0",
        capabilities=("euclidean-space", "affine-space", "metric-space", "convexity"),
        exported_kinds=("geometry.euclidean-space", "geometry.affine-space", "geometry.manifold"),
        dependencies=("kernel",),
        description="Core geometry pack providing Euclidean and affine spatial reasoning primitives.",
        authority="foundational",
        site_region="spatial",
        cover_name="geometry-cover",
        bridge_slots=("geometry-bridge", "spatial-bridge"),
        laws=tuple(laws),
        adapters=tuple(adapters),
        federation_boundaries=tuple(boundaries),
    )


def _build_judgments_pack() -> PackDescriptor:
    laws = [
        PackLaw(
            name="judgment-uniqueness",
            statement=(
                "Each judgment is uniquely identified by its coordinate, kind, "
                "and evidence record."
            ),
            law_kind="axiom",
            locality="local",
            evidence_channels=("proof",),
            status="active",
        ),
        PackLaw(
            name="judgment-soundness",
            statement=(
                "A positive judgment may only be issued if all required evidence "
                "obligations are discharged."
            ),
            law_kind="invariant",
            locality="global",
            evidence_channels=("proof", "runtime"),
            status="active",
        ),
        PackLaw(
            name="judgment-consistency",
            statement=(
                "No coordinate may simultaneously hold a positive and a negative "
                "judgment for the same condition."
            ),
            law_kind="invariant",
            locality="local",
            evidence_channels=("runtime",),
            status="active",
        ),
        PackLaw(
            name="obligation-discharge",
            statement=(
                "An obligation at a coordinate is discharged when evidence of "
                "the appropriate kind and trust tier is recorded against it."
            ),
            law_kind="axiom",
            locality="local",
            evidence_channels=("solver", "oracle", "proof", "human"),
            status="active",
        ),
    ]
    adapters = [
        PackAdapter("judgment-to-evidence", "judgment", "evidence-record", adapter_kind="projection"),
        PackAdapter("obligation-to-claim", "obligation", "claim", adapter_kind="coercion"),
        PackAdapter("verdict-bridge", "judgment", "verdict", adapter_kind="bridge", bidirectional=True),
    ]
    boundaries = [
        PackBoundary(
            "judgment-trust-boundary",
            "provisional",
            ingress_kinds=("judgments.judgment",),
            trust_channels=("proof",),
        ),
        PackBoundary(
            "judgment-scope-boundary",
            "provisional",
            egress_kinds=("judgments.verdict",),
            trust_channels=("runtime",),
        ),
    ]
    return _default_descriptor(
        name="judgments",
        version="0.1.0",
        capabilities=("judgment", "obligation", "verdict", "proof-record"),
        exported_kinds=("judgments.judgment", "judgments.obligation", "judgments.verdict"),
        dependencies=("kernel",),
        description="Core judgment system pack providing the formal machinery for issuing judgments.",
        authority="foundational",
        site_region="proof",
        cover_name="judgments-cover",
        bridge_slots=("judgment-bridge", "proof-bridge"),
        laws=tuple(laws),
        adapters=tuple(adapters),
        federation_boundaries=tuple(boundaries),
    )


def _build_evidence_pack() -> PackDescriptor:
    laws = [
        PackLaw(
            name="evidence-plurality",
            statement=(
                "Different types of claims belong to different evidence channels."
            ),
            law_kind="axiom",
            locality="global",
            evidence_channels=("solver", "oracle", "proof", "runtime"),
            status="active",
        ),
        PackLaw(
            name="channel-isolation",
            statement=(
                "Evidence produced on one channel may not be silently used to "
                "discharge obligations on a different channel."
            ),
            law_kind="invariant",
            locality="global",
            evidence_channels=("runtime",),
            status="active",
        ),
        PackLaw(
            name="no-silent-promotion",
            statement=(
                "No evidence operation may silently promote the trust tier of a "
                "coordinate.  Promotion requires explicit authorisation."
            ),
            law_kind="invariant",
            locality="global",
            evidence_channels=("runtime", "proof"),
            status="active",
        ),
        PackLaw(
            name="evidence-faithfulness",
            statement=(
                "Certificates must faithfully preserve all partially established "
                "clauses, fragility declarations, and support scope."
            ),
            law_kind="invariant",
            locality="global",
            evidence_channels=("proof", "runtime"),
            status="active",
        ),
        PackLaw(
            name="oracle-ceiling",
            statement=(
                "Oracle evidence may not exceed the trust tier ceiling declared "
                "by the pack that registered the oracle."
            ),
            law_kind="invariant",
            locality="global",
            evidence_channels=("oracle", "runtime"),
            status="active",
        ),
    ]
    adapters = [
        PackAdapter("evidence-to-record", "evidence", "evidence-record", adapter_kind="projection", bidirectional=True),
        PackAdapter("solver-to-evidence", "solver-result", "evidence", adapter_kind="lifting"),
        PackAdapter("oracle-to-evidence", "oracle-result", "evidence", adapter_kind="lifting"),
    ]
    boundaries = [
        PackBoundary(
            "evidence-channel-boundary",
            "provisional",
            ingress_kinds=("evidence.certificate",),
            trust_channels=("proof",),
        ),
        PackBoundary(
            "evidence-trust-boundary",
            "provisional",
            egress_kinds=("evidence.certificate",),
            trust_channels=("runtime",),
        ),
    ]
    return _default_descriptor(
        name="evidence",
        version="0.1.0",
        capabilities=("evidence", "evidence-record", "trust-tier", "oracle-ceiling"),
        exported_kinds=("evidence.certificate", "evidence.record", "evidence.channel"),
        dependencies=("kernel",),
        description="Core evidence pack providing multi-channel evidence machinery.",
        authority="foundational",
        site_region="evidence",
        cover_name="evidence-cover",
        bridge_slots=("evidence-bridge", "channel-bridge"),
        laws=tuple(laws),
        adapters=tuple(adapters),
        federation_boundaries=tuple(boundaries),
    )


def _build_packs_pack() -> PackDescriptor:
    laws = [
        PackLaw(
            name="pack-registration-monotonicity",
            statement=(
                "Once a pack is registered in the catalog with a given catalog key, "
                "it may only be replaced if the caller explicitly passes replace=True."
            ),
            law_kind="invariant",
            locality="global",
            evidence_channels=("runtime",),
            status="active",
        ),
        PackLaw(
            name="pack-record-explicitness",
            statement=(
                "Every domain pack must explicitly declare its exported kinds, "
                "capabilities, laws, adapters, and federation boundaries. "
                "Implicit or inherited declarations are not permitted."
            ),
            law_kind="invariant",
            locality="global",
            evidence_channels=("runtime", "proof"),
            status="active",
        ),
        PackLaw(
            name="dependency-closure-finiteness",
            statement=(
                "The dependency closure of any finite set of packs is finite."
            ),
            law_kind="theorem",
            locality="global",
            evidence_channels=("proof",),
            status="active",
        ),
        PackLaw(
            name="authority-order-consistency",
            statement=(
                "The authority order declared by KNOWN_AUTHORITY_LEVELS is "
                "transitive and total."
            ),
            law_kind="axiom",
            locality="global",
            evidence_channels=("proof",),
            status="active",
        ),
        PackLaw(
            name="catalog-key-uniqueness",
            statement=(
                "Within a single PackCatalog, no two descriptors share the "
                "same catalog key."
            ),
            law_kind="invariant",
            locality="global",
            evidence_channels=("runtime",),
            status="active",
        ),
        PackLaw(
            name="bridge-slot-compatibility",
            statement=(
                "A bridge between pack A and pack B is admissible if and only if "
                "A declares a bridge slot compatible with B's."
            ),
            law_kind="theorem",
            locality="global",
            evidence_channels=("proof", "runtime"),
            status="active",
        ),
    ]
    adapters = [
        PackAdapter("descriptor-to-dict", "pack-descriptor", "dict", adapter_kind="projection", bidirectional=True),
        PackAdapter("catalog-to-index", "pack-catalog", "kind-index", adapter_kind="projection"),
        PackAdapter("routing-policy-adapter", "pack-catalog", "packs.routing-policy", adapter_kind="projection"),
    ]
    boundaries = [
        PackBoundary(
            "packs-federation-boundary",
            "provisional",
            inbound_packs=("kernel",),
            egress_kinds=("packs.pack-descriptor", "packs.routing-policy"),
            trust_channels=("runtime",),
        ),
        PackBoundary(
            "packs-version-boundary",
            "provisional",
            ingress_kinds=("packs.pack-descriptor",),
            trust_channels=("proof",),
        ),
    ]
    return _default_descriptor(
        name="packs",
        version="0.1.0",
        capabilities=("pack-catalog", "pack-descriptor", "pack-registry", "bridge-theorems"),
        exported_kinds=("packs.pack-descriptor", "packs.pack-catalog", "packs.routing-policy"),
        dependencies=("kernel",),
        description="Core packs management pack providing the self-describing registry machinery.",
        authority="foundational",
        site_region="registry",
        cover_name="packs-cover",
        bridge_slots=("pack-bridge", "federation-bridge"),
        laws=tuple(laws),
        adapters=tuple(adapters),
        federation_boundaries=tuple(boundaries),
        metadata={"module_root": "src/jugeo/packs", "test_root": "tests/jugeo/packs"},
    )


# ---------------------------------------------------------------------------
# Catalog construction
# ---------------------------------------------------------------------------


def _build_foundations_pack() -> PackDescriptor:
    laws = [
        PackLaw(
            name="foundations-coherence",
            statement=(
                "The foundational layer must be coherent: all axioms adopted by "
                "the kernel, geometry, and judgments packs must be mutually "
                "consistent within the JuGeo site."
            ),
            law_kind="axiom",
            locality="global",
            evidence_channels=("proof",),
            status="active",
        ),
        PackLaw(
            name="foundations-completeness",
            statement=(
                "Every theorem provable in the foundational layer must have a "
                "derivation traceable to the kernel axioms."
            ),
            law_kind="theorem",
            locality="global",
            evidence_channels=("proof",),
            status="provisional",
        ),
        PackLaw(
            name="foundations-descent-compatibility",
            statement=(
                "Foundational descent conditions must be compatible with "
                "the geometric descent laws of the geometry pack."
            ),
            law_kind="descent",
            locality="global",
            evidence_channels=("proof", "solver"),
            status="active",
        ),
    ]
    adapters = [
        PackAdapter("foundations-to-kernel", "foundations.theory", "kernel.coordinate", adapter_kind="projection"),
        PackAdapter("geometry-to-foundations", "geometry.manifold", "foundations.structure", adapter_kind="embedding"),
    ]
    boundaries = [
        PackBoundary(
            "foundations-coherence-boundary",
            "foundational",
            inbound_packs=("kernel", "geometry", "judgments"),
            egress_kinds=("foundations.theory",),
            trust_channels=("proof",),
        ),
    ]
    return _default_descriptor(
        name="foundations",
        version="0.1.0",
        capabilities=("foundational-theory", "coherence-checking", "completeness"),
        exported_kinds=("foundations.theory", "foundations.structure", "foundations.axiom"),
        dependencies=("kernel", "geometry", "judgments"),
        description="Foundations pack providing coherence and completeness of the foundational layer.",
        authority="foundational",
        site_region="foundations",
        cover_name="foundations-cover",
        bridge_slots=("foundations-bridge",),
        laws=tuple(laws),
        adapters=tuple(adapters),
        federation_boundaries=tuple(boundaries),
    )


def _build_encodings_pack() -> PackDescriptor:
    laws = [
        PackLaw(
            name="encoding-faithfulness",
            statement=(
                "Every encoding must be a faithful representation: decoding an "
                "encoded value must recover the original up to structural equivalence."
            ),
            law_kind="invariant",
            locality="local",
            evidence_channels=("proof", "solver"),
            status="active",
        ),
        PackLaw(
            name="encoding-provenance",
            statement=(
                "Every encoded artifact must carry provenance identifying the "
                "encoding scheme and version."
            ),
            law_kind="invariant",
            locality="local",
            evidence_channels=("runtime",),
            status="active",
        ),
    ]
    adapters = [
        PackAdapter("value-to-encoding", "kernel.coordinate", "encodings.encoded", adapter_kind="projection"),
        PackAdapter("encoding-to-value", "encodings.encoded", "kernel.coordinate", adapter_kind="embedding"),
    ]
    boundaries = [
        PackBoundary(
            "encodings-schema-boundary",
            "exploratory",
            egress_kinds=("encodings.encoded",),
            trust_channels=("runtime",),
        ),
    ]
    return _default_descriptor(
        name="encodings",
        version="0.1.0",
        capabilities=("encoding", "serialization", "schema-management"),
        exported_kinds=("encodings.encoded", "encodings.schema", "encodings.codec"),
        dependencies=("kernel",),
        description="Encodings pack providing serialization and schema management.",
        authority="exploratory",
        site_region="encodings",
        cover_name="encodings-cover",
        bridge_slots=("encodings-bridge",),
        laws=tuple(laws),
        adapters=tuple(adapters),
        federation_boundaries=tuple(boundaries),
    )


def _build_solver_pack() -> PackDescriptor:
    laws = [
        PackLaw(
            name="solver-soundness",
            statement=(
                "A solver may only report a result as verified if all constraints "
                "are satisfied within the declared precision bounds."
            ),
            law_kind="invariant",
            locality="global",
            evidence_channels=("solver", "proof"),
            status="active",
        ),
        PackLaw(
            name="solver-completeness",
            statement=(
                "For any decidable constraint set within the solver's declared "
                "domain, the solver must eventually return either sat or unsat."
            ),
            law_kind="theorem",
            locality="local",
            evidence_channels=("proof",),
            status="provisional",
        ),
        PackLaw(
            name="solver-traceability",
            statement=(
                "Every solver result must carry a provenance trace identifying "
                "the solver instance, the constraint set hash, and the result tier."
            ),
            law_kind="invariant",
            locality="local",
            evidence_channels=("runtime",),
            status="active",
        ),
    ]
    adapters = [
        PackAdapter("constraint-to-solver", "judgments.obligation", "solver.constraint", adapter_kind="coercion"),
        PackAdapter("solver-result-to-evidence", "solver.result", "evidence.certificate", adapter_kind="lifting"),
    ]
    boundaries = [
        PackBoundary(
            "solver-discharge-boundary",
            "provisional",
            inbound_packs=("judgments",),
            egress_kinds=("solver.result",),
            trust_channels=("solver",),
        ),
    ]
    return _default_descriptor(
        name="solver",
        version="0.1.0",
        capabilities=("constraint-solving", "sat-checking", "numeric-verification"),
        exported_kinds=("solver.result", "solver.constraint", "solver.model"),
        dependencies=("kernel", "judgments", "evidence"),
        description="Solver pack providing constraint solving and verification.",
        authority="provisional",
        site_region="solver",
        cover_name="solver-cover",
        bridge_slots=("solver-bridge",),
        laws=tuple(laws),
        adapters=tuple(adapters),
        federation_boundaries=tuple(boundaries),
    )


def _build_runtime_pack() -> PackDescriptor:
    laws = [
        PackLaw(
            name="runtime-isolation",
            statement=(
                "Runtime operations must be isolated: side effects from one "
                "runtime invocation must not leak into another without explicit bridging."
            ),
            law_kind="invariant",
            locality="global",
            evidence_channels=("runtime",),
            status="active",
        ),
        PackLaw(
            name="runtime-checkpointing",
            statement=(
                "Every significant runtime state transition must produce a "
                "checkpoint record with full provenance."
            ),
            law_kind="invariant",
            locality="local",
            evidence_channels=("runtime",),
            status="active",
        ),
        PackLaw(
            name="runtime-pack-compatibility",
            statement=(
                "The runtime must verify pack compatibility before loading: "
                "dependency closure must be computable and cycle-free."
            ),
            law_kind="theorem",
            locality="global",
            evidence_channels=("runtime", "proof"),
            status="active",
        ),
    ]
    adapters = [
        PackAdapter("pack-to-runtime", "packs.pack-descriptor", "runtime.loaded-pack", adapter_kind="lifting"),
        PackAdapter("checkpoint-to-evidence", "runtime.checkpoint", "evidence.record", adapter_kind="projection"),
    ]
    boundaries = [
        PackBoundary(
            "runtime-session-boundary",
            "provisional",
            inbound_packs=("packs",),
            egress_kinds=("runtime.checkpoint",),
            trust_channels=("runtime",),
        ),
    ]
    return _default_descriptor(
        name="runtime",
        version="0.1.0",
        capabilities=("pack-loading", "checkpoint-management", "session-lifecycle"),
        exported_kinds=("runtime.loaded-pack", "runtime.checkpoint", "runtime.session"),
        dependencies=("kernel", "packs"),
        description="Runtime pack providing pack loading and session lifecycle management.",
        authority="provisional",
        site_region="runtime",
        cover_name="runtime-cover",
        bridge_slots=("runtime-bridge",),
        laws=tuple(laws),
        adapters=tuple(adapters),
        federation_boundaries=tuple(boundaries),
    )


def _build_evaluation_pack() -> PackDescriptor:
    laws = [
        PackLaw(
            name="evaluation-soundness",
            statement=(
                "An evaluation result is sound only if the evidence used to "
                "derive it is faithfully accounted for in the result record."
            ),
            law_kind="invariant",
            locality="global",
            evidence_channels=("proof", "runtime"),
            status="active",
        ),
        PackLaw(
            name="evaluation-monotonicity",
            statement=(
                "Additional evidence may only improve an evaluation score, "
                "never silently degrade it without a retraction event."
            ),
            law_kind="invariant",
            locality="local",
            evidence_channels=("runtime",),
            status="active",
        ),
    ]
    adapters = [
        PackAdapter("evidence-to-evaluation", "evidence.certificate", "evaluation.score", adapter_kind="projection"),
        PackAdapter("evaluation-to-report", "evaluation.score", "evaluation.report", adapter_kind="projection"),
    ]
    boundaries = [
        PackBoundary(
            "evaluation-trust-boundary",
            "provisional",
            inbound_packs=("evidence",),
            egress_kinds=("evaluation.score",),
            trust_channels=("proof",),
        ),
    ]
    return _default_descriptor(
        name="evaluation",
        version="0.1.0",
        capabilities=("evidence-evaluation", "score-computation", "report-generation"),
        exported_kinds=("evaluation.score", "evaluation.report", "evaluation.verdict"),
        dependencies=("kernel", "evidence"),
        description="Evaluation pack providing evidence evaluation and scoring.",
        authority="provisional",
        site_region="evaluation",
        cover_name="evaluation-cover",
        bridge_slots=("evaluation-bridge",),
        laws=tuple(laws),
        adapters=tuple(adapters),
        federation_boundaries=tuple(boundaries),
    )


def _build_generation_pack() -> PackDescriptor:
    laws = [
        PackLaw(
            name="frontier-honesty",
            statement=(
                "Generated candidates must honestly report their frontier status. "
                "A candidate at the knowledge frontier must declare the frontier "
                "as provenance and not claim proven status."
            ),
            law_kind="invariant",
            locality="global",
            evidence_channels=("runtime",),
            status="active",
        ),
        PackLaw(
            name="generation-traceability",
            statement=(
                "Every generated artifact must carry a provenance record identifying "
                "the generating pack, the generation strategy, and the trust tier "
                "at the time of generation."
            ),
            law_kind="invariant",
            locality="global",
            evidence_channels=("runtime",),
            status="active",
        ),
        PackLaw(
            name="generation-consistency",
            statement=(
                "Generated candidates for the same coordinate under the same strategy "
                "must produce equivalent outputs modulo provenance metadata."
            ),
            law_kind="theorem",
            locality="local",
            evidence_channels=("proof", "solver"),
            status="provisional",
        ),
    ]
    adapters = [
        PackAdapter("candidate-to-evidence", "generation.candidate", "evidence.certificate", adapter_kind="lifting"),
        PackAdapter("strategy-to-plan", "generation.strategy", "generation.plan", adapter_kind="projection"),
    ]
    boundaries = [
        PackBoundary(
            "generation-frontier-boundary",
            "provisional",
            egress_kinds=("generation.candidate",),
            trust_channels=("runtime",),
        ),
    ]
    return _default_descriptor(
        name="generation",
        version="0.1.0",
        capabilities=("candidate-generation", "strategy-planning"),
        exported_kinds=("generation.candidate", "generation.strategy", "generation.plan"),
        dependencies=("kernel", "solver"),
        description="Generation pack providing candidate generation and strategy planning.",
        authority="provisional",
        site_region="generation",
        cover_name="generation-cover",
        bridge_slots=("generation-bridge",),
        laws=tuple(laws),
        adapters=tuple(adapters),
        federation_boundaries=tuple(boundaries),
    )


def _build_orchestration_pack() -> PackDescriptor:
    laws = [
        PackLaw(
            name="orchestration-determinism",
            statement=(
                "Given identical inputs and pack states, an orchestration plan "
                "must produce identical outputs, modulo provenance timestamps."
            ),
            law_kind="invariant",
            locality="global",
            evidence_channels=("proof", "runtime"),
            status="active",
        ),
        PackLaw(
            name="orchestration-traceability",
            statement=(
                "Every orchestration step must record its input packs, output "
                "coordinates, and evidence consumed."
            ),
            law_kind="invariant",
            locality="local",
            evidence_channels=("runtime",),
            status="active",
        ),
    ]
    adapters = [
        PackAdapter("plan-to-orchestration", "generation.plan", "orchestration.workflow", adapter_kind="lifting"),
        PackAdapter("orchestration-to-evidence", "orchestration.workflow", "evidence.record", adapter_kind="projection"),
    ]
    boundaries = [
        PackBoundary(
            "orchestration-execution-boundary",
            "provisional",
            inbound_packs=("generation",),
            egress_kinds=("orchestration.workflow",),
            trust_channels=("runtime",),
        ),
    ]
    return _default_descriptor(
        name="orchestration",
        version="0.1.0",
        capabilities=("workflow-execution", "plan-orchestration"),
        exported_kinds=("orchestration.workflow", "orchestration.step", "orchestration.result"),
        dependencies=("kernel", "generation"),
        description="Orchestration pack providing workflow execution and plan orchestration.",
        authority="provisional",
        site_region="orchestration",
        cover_name="orchestration-cover",
        bridge_slots=("orchestration-bridge",),
        laws=tuple(laws),
        adapters=tuple(adapters),
        federation_boundaries=tuple(boundaries),
    )


def _build_ideation_pack() -> PackDescriptor:
    laws = [
        PackLaw(
            name="ideation-novelty",
            statement=(
                "Ideation candidates must be distinct from existing coordinates "
                "in the session's provenance log, or explicitly declared as refinements."
            ),
            law_kind="invariant",
            locality="local",
            evidence_channels=("runtime",),
            status="active",
        ),
        PackLaw(
            name="ideation-traceability",
            statement=(
                "Every ideation output must carry a provenance record linking it "
                "to the orchestration context that generated it."
            ),
            law_kind="invariant",
            locality="local",
            evidence_channels=("runtime",),
            status="active",
        ),
    ]
    adapters = [
        PackAdapter("orchestration-to-ideation", "orchestration.workflow", "ideation.seed", adapter_kind="lifting"),
        PackAdapter("ideation-to-candidate", "ideation.idea", "generation.candidate", adapter_kind="projection"),
    ]
    boundaries = [
        PackBoundary(
            "ideation-novelty-boundary",
            "exploratory",
            inbound_packs=("orchestration",),
            egress_kinds=("ideation.idea",),
            trust_channels=("runtime",),
        ),
    ]
    return _default_descriptor(
        name="ideation",
        version="0.1.0",
        capabilities=("idea-generation", "novelty-detection"),
        exported_kinds=("ideation.idea", "ideation.seed", "ideation.refinement"),
        dependencies=("kernel", "orchestration"),
        description="Ideation pack providing idea generation and novelty detection.",
        authority="exploratory",
        site_region="ideation",
        cover_name="ideation-cover",
        bridge_slots=("ideation-bridge",),
        laws=tuple(laws),
        adapters=tuple(adapters),
        federation_boundaries=tuple(boundaries),
    )


def _build_interfaces_pack() -> PackDescriptor:
    laws = [
        PackLaw(
            name="interface-explicitness",
            statement=(
                "Every interface contract must explicitly declare its input kinds, "
                "output kinds, and evidence requirements."
            ),
            law_kind="invariant",
            locality="global",
            evidence_channels=("runtime", "proof"),
            status="active",
        ),
        PackLaw(
            name="interface-composability",
            statement=(
                "Two interface contracts are composable if and only if the output "
                "kinds of the first are a subset of the input kinds of the second."
            ),
            law_kind="theorem",
            locality="local",
            evidence_channels=("proof",),
            status="active",
        ),
    ]
    adapters = [
        PackAdapter("ideation-to-interface", "ideation.idea", "interfaces.contract", adapter_kind="lifting"),
        PackAdapter("interface-to-generation", "interfaces.contract", "generation.strategy", adapter_kind="projection"),
    ]
    boundaries = [
        PackBoundary(
            "interfaces-composition-boundary",
            "provisional",
            inbound_packs=("generation", "orchestration", "ideation"),
            egress_kinds=("interfaces.contract",),
            trust_channels=("runtime", "proof"),
        ),
    ]
    return _default_descriptor(
        name="interfaces",
        version="0.1.0",
        capabilities=("interface-contracts", "composition-checking"),
        exported_kinds=("interfaces.contract", "interfaces.input-spec", "interfaces.output-spec"),
        dependencies=("kernel", "generation", "orchestration", "ideation"),
        description="Interfaces pack providing interface contracts and composition checking.",
        authority="provisional",
        site_region="interfaces",
        cover_name="interfaces-cover",
        bridge_slots=("interfaces-bridge",),
        laws=tuple(laws),
        adapters=tuple(adapters),
        federation_boundaries=tuple(boundaries),
    )


def _build_default_catalog() -> PackCatalog:
    """Build and return the default built-in pack catalog."""
    catalog = PackCatalog()
    for desc in [
        _build_kernel_pack(),
        _build_geometry_pack(),
        _build_judgments_pack(),
        _build_evidence_pack(),
        _build_packs_pack(),
        _build_foundations_pack(),
        _build_encodings_pack(),
        _build_solver_pack(),
        _build_runtime_pack(),
        _build_evaluation_pack(),
        _build_generation_pack(),
        _build_orchestration_pack(),
        _build_ideation_pack(),
        _build_interfaces_pack(),
    ]:
        catalog.register(desc)
    return catalog


def load_pack_catalog(
    entries: "Sequence[PackDescriptor | Mapping[str, Any]] | None" = None,
    *,
    include_builtin: "bool | None" = None,
    strict: bool = False,
) -> PackCatalog:
    """Construct a PackCatalog from optional external entries and/or built-ins.

    Parameters
    ----------
    entries:
        Optional sequence of additional pack descriptors to register.
    include_builtin:
        If True (the default when entries is None), the five built-in packs
        are registered before any caller-supplied entries.
    strict:
        If True, validate after construction and raise on issues.

    Returns
    -------
    PackCatalog

    Raises
    ------
    jugeo.errors.JuGeoError
        If strict is True and the catalog has validation issues.

    Examples
    --------
    >>> cat = load_pack_catalog()
    >>> "kernel@0.1.0" in cat.descriptors
    True
    """
    effective_entries: list[PackDescriptor | Mapping[str, Any]] = list(entries or [])
    if include_builtin is None:
        include_builtin = len(effective_entries) == 0
    catalog = PackCatalog()
    if include_builtin:
        for desc in [
            _build_kernel_pack(),
            _build_geometry_pack(),
            _build_judgments_pack(),
            _build_evidence_pack(),
            _build_packs_pack(),
            _build_foundations_pack(),
            _build_encodings_pack(),
            _build_solver_pack(),
            _build_runtime_pack(),
            _build_evaluation_pack(),
            _build_generation_pack(),
            _build_orchestration_pack(),
            _build_ideation_pack(),
            _build_interfaces_pack(),
        ]:
            catalog.register(desc)
    for entry in effective_entries:
        if isinstance(entry, PackDescriptor):
            catalog.register(entry, replace=False)
        elif isinstance(entry, dict) or hasattr(entry, "items"):
            catalog.register(PackDescriptor.from_mapping(entry), replace=False)
        else:
            raise_with_scope(
                "invalid-catalog-entry",
                message=(
                    f"load_pack_catalog received an entry of type "
                    f"{type(entry).__name__!r} which is neither a PackDescriptor "
                    "nor a mapping."
                ),
                scope=FailureScope.PACK,
                provenance={"entry_type": type(entry).__name__},
            )
    if strict:
        issues = catalog.validate()
        if issues:
            issue_summary = "; ".join(issues[:5])
            raise_with_scope(
                "catalog-validation-failed",
                message=(
                    f"PackCatalog failed strict validation with {len(issues)} "
                    f"issue(s): {issue_summary}."
                ),
                scope=FailureScope.PACK,
                provenance={"issue_count": len(issues), "issues": issues},
            )
    return catalog


def list_available_packs(
    catalog: "PackCatalog | None" = None,
    *,
    authority_floor: str | None = None,
    capability: str | None = None,
    exported_kind: str | None = None,
) -> tuple[str, ...]:
    """Return the sorted names of packs matching the given criteria."""
    if catalog is None:
        catalog = _DEFAULT_CATALOG
    results: list[str] = []
    for desc in catalog.descriptors.values():
        if authority_floor is not None:
            if _authority_rank(desc.authority) < _authority_rank(authority_floor):
                continue
        if capability is not None:
            if capability not in desc.capabilities:
                continue
        if exported_kind is not None:
            if exported_kind not in desc.exported_kinds:
                continue
        results.append(desc.name)
    return tuple(sorted(set(results)))


# ---------------------------------------------------------------------------
# Module-level default catalog and MANIFEST_SUBSYSTEM_PACKS
# ---------------------------------------------------------------------------

#: The module-level default PackCatalog instance.
_DEFAULT_CATALOG: PackCatalog = _build_default_catalog()

#: The tuple of default built-in PackDescriptor instances (kernel, geometry, judgments, evidence, packs).
DEFAULT_PACK_DESCRIPTORS: tuple[PackDescriptor, ...] = tuple(
    _DEFAULT_CATALOG.descriptors.values()
)

#: The set of subsystem names declared in the canonical JuGeo package manifest.
MANIFEST_SUBSYSTEM_PACKS: Final[frozenset[str]] = frozenset(
    enumerate_subsystems(build_package_manifest())
)


# ---------------------------------------------------------------------------
# Cross-subsystem enrichment functions
# ---------------------------------------------------------------------------

try:
    from jugeo.geometry.site import (
        Coordinate as _Coordinate,
        CoordinateKind as _CoordinateKind,
    )
except Exception:  # pragma: no cover
    _Coordinate = None  # type: ignore[assignment,misc]
    _CoordinateKind = None  # type: ignore[assignment,misc]

try:
    from jugeo.evidence.trust import TrustLevel as _TrustLevel, TrustTier as _TrustTier
except Exception:  # pragma: no cover
    _TrustLevel = None  # type: ignore[assignment,misc]
    _TrustTier = None  # type: ignore[assignment,misc]


def site_catalog(
    catalog: PackCatalog | None = None,
    *,
    coordinate_kind: Any | None = None,
) -> dict[str, list[str]]:
    """Organise the pack catalog over the judgment site.

    Groups pack descriptors by their semantic coordinate kind using
    ``jugeo.geometry.site.CoordinateKind``.  Each key in the returned
    dict is a coordinate-kind label; the values are lists of pack names
    whose exported kinds or capabilities match that coordinate kind.

    Parameters
    ----------
    catalog:
        A :class:`PackCatalog` instance.  Defaults to the module-level
        ``_DEFAULT_CATALOG``.
    coordinate_kind:
        When provided, restrict the result to a single coordinate kind.

    Returns
    -------
    dict[str, list[str]]
        Mapping from coordinate-kind label to pack names.
    """
    cat = catalog if catalog is not None else _DEFAULT_CATALOG
    result: dict[str, list[str]] = defaultdict(list)

    kind_labels: list[str] = []
    if _CoordinateKind is not None:
        try:
            kind_labels = [k.value for k in _CoordinateKind]
        except Exception:
            kind_labels = ["module", "function", "interface", "test", "theorem", "region"]
    else:
        kind_labels = ["module", "function", "interface", "test", "theorem", "region"]

    filter_label = None
    if coordinate_kind is not None:
        filter_label = coordinate_kind.value if hasattr(coordinate_kind, "value") else str(coordinate_kind)

    for desc in cat.descriptors.values():
        exported = set(getattr(desc, "exported_kinds", ()) or ())
        caps = set(getattr(desc, "capabilities", ()) or ())
        tags = exported | caps | {desc.name}

        for kind_label in kind_labels:
            if filter_label is not None and kind_label != filter_label:
                continue
            if any(kind_label in tag.lower() for tag in tags) or not exported:
                result[kind_label].append(desc.name)

    return dict(result)


def trust_catalog(
    catalog: PackCatalog | None = None,
) -> list[dict[str, Any]]:
    """Annotate catalog entries with trust information.

    Iterates over the pack catalog and enriches each descriptor with
    trust metadata from ``jugeo.evidence.trust``, producing a list of
    annotated records.

    Parameters
    ----------
    catalog:
        A :class:`PackCatalog` instance.  Defaults to the module-level
        ``_DEFAULT_CATALOG``.

    Returns
    -------
    list[dict[str, Any]]
        Each dict contains ``"name"``, ``"authority"``,
        ``"trust_ceiling"``, ``"trust_tier_label"``, and
        ``"trust_level_label"``.
    """
    cat = catalog if catalog is not None else _DEFAULT_CATALOG
    entries: list[dict[str, Any]] = []

    for desc in cat.descriptors.values():
        authority = getattr(desc, "authority", "exploratory")
        trust_ceiling = getattr(desc, "trust_ceiling", None)

        tier_label = ""
        if trust_ceiling is not None:
            tier_label = trust_ceiling.value if hasattr(trust_ceiling, "value") else str(trust_ceiling)

        level_label = ""
        if _TrustLevel is not None:
            authority_to_level: dict[str, str] = {
                "foundational": "MECHANICALLY_VERIFIED",
                "provisional": "SOLVER_DISCHARGED",
                "exploratory": "COPILOT_SUGGESTED",
                "quarantined": "UNVERIFIED",
            }
            level_label = authority_to_level.get(str(authority), "UNVERIFIED")

        entries.append({
            "name": desc.name,
            "authority": str(authority),
            "trust_ceiling": tier_label,
            "trust_tier_label": tier_label,
            "trust_level_label": level_label,
        })

    return entries
