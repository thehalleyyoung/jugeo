"""Wire schemas for JuGeo interface payloads.

This module defines a compact, explicit schema layer for JuGeo's public wire
surfaces. The goal is not merely to validate dictionaries: it is to preserve
semantic boundaries that matter to the project worldview described in
``preliminaries/theory2.tex``.

Theory alignment
----------------
JuGeo's public data should remain faithful to at least four persistent ideas in
``theory2.tex``:

* trust is structured state rather than a single confidence number;
* provenance remains challengeable and visible rather than renderer garnish;
* manifests are authority-bearing memory, not just caches; and
* diagnostics and reports must preserve residual obligations instead of hiding
  uncertainty behind success-shaped prose.

Accordingly, the wire layer here does three things on purpose:

* it uses explicit field specifications instead of ad hoc shape checks;
* it normalizes nearby JuGeo objects into stable JSON-compatible payloads
  without silently promoting trust or erasing provenance; and
* it emits deterministic envelopes so later transport and persistence layers can
  build on the same contract.

The blueprint entry for this file names two primary classes and two primary
functions:

* :class:`WireSchema`
* :class:`SchemaRegistry`
* :func:`encode_wire_payload`
* :func:`decode_wire_payload`

Compatibility notes
-------------------
This module preserves the small legacy helpers ``judgment_schema()`` and
``certificate_schema()`` because the existing tests and nearby generated files
expect them. The richer API is intentionally centered around ``WireSchema`` and
``SchemaRegistry``.
"""

from __future__ import annotations

import base64
import hashlib
import inspect
import json
from dataclasses import dataclass, fields, is_dataclass
from datetime import date, datetime, time
from enum import Enum
from pathlib import Path
from typing import Any, Callable, ClassVar, Final, Literal, Mapping, Sequence, TypeAlias, cast

SCHEMA_VERSION: Final[str] = "2.0.0"
THEORY_SOURCE: Final[str] = "preliminaries/theory2.tex"
BLUEPRINT_SOURCE: Final[str] = "theory2-src-blueprint.json"
GENERATION_ORDER_SOURCE: Final[str] = "theory2-generation-order.json"
SCHEMA_DIALECT: Final[str] = "https://json-schema.org/draft/2020-12/schema"
SCHEMA_ID_PREFIX: Final[str] = "https://jugeo.dev/schemas"

WIRE_SCHEMA_NAME_KEY: Final[str] = "_jugeo_wire_schema"
WIRE_SCHEMA_VERSION_KEY: Final[str] = "_jugeo_schema_version"
WIRE_SEMANTIC_BOUNDARY_KEY: Final[str] = "_jugeo_semantic_boundary"
WIRE_TRUST_BOUNDARY_KEY: Final[str] = "_jugeo_trust_boundary"
WIRE_PROVENANCE_REQUIREMENT_KEY: Final[str] = "_jugeo_provenance_requirement"
WIRE_PAYLOAD_HASH_KEY: Final[str] = "_jugeo_payload_sha256"
WIRE_PAYLOAD_KEY: Final[str] = "payload"

JsonScalar: TypeAlias = None | bool | int | float | str
JsonValue: TypeAlias = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]
JsonObject: TypeAlias = dict[str, JsonValue]
FieldType: TypeAlias = Literal["string", "integer", "number", "boolean", "object", "array", "any"]

_MISSING = object()

CANONICAL_TRUST_LABELS: Final[tuple[str, ...]] = (
    "contradicted",
    "untrusted",
    "unverified",
    "proposal",
    "provisional",
    "copilot_suggested",
    "oracle_proposed",
    "human_attested",
    "reviewed",
    "runtime_witnessed",
    "solver_discharged",
    "verified",
    "mechanically_verified",
    "certified",
)

_CERTIFICATE_TRUST_LABELS: Final[tuple[str, ...]] = (
    "proposal",
    "reviewed",
    "verified",
    "certified",
    "provisional",
    "untrusted",
    "unverified",
)

_DIAGNOSTIC_LEVELS: Final[tuple[str, ...]] = ("info", "warning", "error")
_REPORT_STATUSES: Final[tuple[str, ...]] = (
    "pending",
    "in_progress",
    "settled",
    "obstructed",
    "residual",
    "failed",
)

_THEOREM_TARGETS: Final[tuple[str, ...]] = (
    "serialization determinism",
    "dependency-trace integrity",
    "stale-manifest conservativity",
    "projection faithfulness",
    "scope honesty",
    "no-silent-promotion",
)


class SchemaError(ValueError):
    """Base class for wire-schema failures."""


class SchemaValidationError(SchemaError):
    """Raised when payload normalization finds semantic or structural errors."""


class SchemaDecodeError(SchemaError):
    """Raised when a wire envelope cannot be decoded honestly."""


@dataclass(frozen=True, slots=True)
class SchemaIssue:
    """Single validation issue produced during normalization."""

    path: str
    code: str
    message: str
    severity: Literal["error", "warning"] = "error"
    expected: str | None = None
    actual: JsonValue | None = None

    def to_dict(self) -> JsonObject:
        return {
            "path": self.path,
            "code": self.code,
            "message": self.message,
            "severity": self.severity,
            "expected": self.expected,
            "actual": self.actual,
        }


@dataclass(frozen=True, slots=True)
class ValidationResult:
    """Outcome of validating or normalizing a payload against a wire schema."""

    schema_name: str
    normalized: JsonObject | None
    issues: tuple[SchemaIssue, ...] = ()

    @property
    def ok(self) -> bool:
        return not any(issue.severity == "error" for issue in self.issues)

    @property
    def errors(self) -> tuple[SchemaIssue, ...]:
        return tuple(issue for issue in self.issues if issue.severity == "error")

    @property
    def warnings(self) -> tuple[SchemaIssue, ...]:
        return tuple(issue for issue in self.issues if issue.severity == "warning")

    def raise_for_errors(self) -> JsonObject:
        if self.ok and self.normalized is not None:
            return self.normalized
        detail = "\n".join(f"- {issue.path}: {issue.message}" for issue in self.errors)
        raise SchemaValidationError(
            f"Payload failed schema {self.schema_name!r} validation.\n{detail}"
        )


@dataclass(frozen=True, slots=True)
class FieldSpec:
    """Specification for one stable wire field."""

    name: str
    value_type: FieldType = "any"
    description: str = ""
    required: bool = True
    nullable: bool = False
    repeated: bool = False
    aliases: tuple[str, ...] = ()
    enum_values: tuple[str, ...] = ()
    default: JsonValue | object = _MISSING
    ref_schema: str | None = None
    item_ref_schema: str | None = None
    preprocess: Callable[[Any], Any] | None = None

    def has_default(self) -> bool:
        return self.default is not _MISSING

    def read_from(self, source: Mapping[str, Any]) -> tuple[str | None, Any]:
        if self.name in source:
            return self.name, source[self.name]
        for alias in self.aliases:
            if alias in source:
                return alias, source[alias]
        return None, _MISSING

    def to_descriptor(self, registry: "SchemaRegistry | None" = None) -> JsonObject:
        property_descriptor: JsonObject = {
            "description": self.description,
            "required": self.required,
            "nullable": self.nullable,
            "repeated": self.repeated,
            "aliases": list(self.aliases),
        }
        if self.enum_values:
            property_descriptor["enum"] = list(self.enum_values)
        if self.has_default():
            property_descriptor["default"] = cast(JsonValue, self.default)
        if self.ref_schema:
            property_descriptor["ref_schema"] = self.ref_schema
            if registry is not None:
                property_descriptor["$ref"] = registry.resolve(self.ref_schema).schema_id
        if self.item_ref_schema:
            property_descriptor["item_ref_schema"] = self.item_ref_schema
            if registry is not None:
                property_descriptor["items"] = {"$ref": registry.resolve(self.item_ref_schema).schema_id}
        if self.value_type != "any":
            property_descriptor["type"] = "array" if self.repeated else self.value_type
        return property_descriptor

    def json_schema_property(self, registry: "SchemaRegistry | None" = None) -> JsonObject:
        if self.repeated:
            if self.item_ref_schema is not None:
                items = {"$ref": registry.resolve(self.item_ref_schema).schema_id} if registry else {"$ref": self.item_ref_schema}
            elif self.ref_schema is not None:
                items = {"$ref": registry.resolve(self.ref_schema).schema_id} if registry else {"$ref": self.ref_schema}
            else:
                items = _primitive_property_descriptor(self.value_type, self.enum_values)
            descriptor: JsonObject = {
                "type": "array",
                "description": self.description,
                "items": items,
            }
        elif self.ref_schema is not None:
            ref = registry.resolve(self.ref_schema).schema_id if registry else self.ref_schema
            descriptor = {"allOf": [{"$ref": ref}], "description": self.description}
        else:
            descriptor = _primitive_property_descriptor(self.value_type, self.enum_values)
            descriptor["description"] = self.description
        if self.nullable:
            descriptor = {"anyOf": [descriptor, {"type": "null"}]}
        if self.aliases:
            descriptor["x-jugeo-aliases"] = list(self.aliases)
        if self.has_default():
            descriptor["default"] = cast(JsonValue, self.default)
        return descriptor

    def normalize_value(
        self,
        value: Any,
        *,
        registry: "SchemaRegistry",
        path: str,
    ) -> tuple[JsonValue | None, list[SchemaIssue]]:
        if value is None:
            if self.nullable:
                return None, []
            return None, [_issue(path, "null-not-allowed", f"Field {self.name!r} is not nullable.")]
        raw = self.preprocess(value) if self.preprocess is not None else value
        if raw is None:
            if self.nullable:
                return None, []
            return None, [_issue(path, "null-not-allowed", f"Field {self.name!r} is not nullable.")]
        if self.repeated:
            if not _is_non_text_sequence(raw):
                return None, [_issue(path, "type-mismatch", "Expected a sequence for repeated field.", expected="array", actual=_safe_actual(raw))]
            normalized_items: list[JsonValue] = []
            issues: list[SchemaIssue] = []
            nested_schema = self.item_ref_schema or self.ref_schema
            for index, item in enumerate(raw):
                item_path = f"{path}[{index}]"
                item_value, item_issues = self._normalize_single(item, registry=registry, path=item_path, nested_schema=nested_schema)
                if item_value is not None:
                    normalized_items.append(item_value)
                issues.extend(item_issues)
            return normalized_items, issues
        return self._normalize_single(raw, registry=registry, path=path, nested_schema=self.ref_schema)

    def _normalize_single(
        self,
        value: Any,
        *,
        registry: "SchemaRegistry",
        path: str,
        nested_schema: str | None,
    ) -> tuple[JsonValue | None, list[SchemaIssue]]:
        if nested_schema is not None:
            projected = _project_mapping(value)
            if projected is None:
                return None, [_issue(path, "type-mismatch", f"Expected an object compatible with nested schema {nested_schema!r}.", expected="object", actual=_safe_actual(value))]
            nested = registry.resolve(nested_schema).validate(projected, registry=registry, path=path)
            return nested.normalized, list(nested.issues)

        normalized, issue = _normalize_primitive(value, self.value_type, path)
        if issue is not None:
            return None, [issue]
        if self.enum_values and normalized is not None:
            if not isinstance(normalized, str):
                return None, [_issue(path, "enum-type-mismatch", "Enum-constrained values must normalize to strings.", expected="string", actual=_safe_actual(normalized))]
            normalized = _normalize_symbol(normalized)
            if normalized not in self.enum_values:
                return None, [_issue(path, "enum-mismatch", f"Value {normalized!r} is not one of the allowed labels.", expected=", ".join(self.enum_values), actual=normalized)]
        return normalized, []


@dataclass(frozen=True, slots=True)
class WireSchema:
    """Explicit schema for one JSON-compatible JuGeo wire surface."""

    name: str
    title: str
    description: str
    fields: tuple[FieldSpec, ...]
    version: str = SCHEMA_VERSION
    semantic_boundary: str = "interface"
    trust_boundary: str = "preserve-kind"
    provenance_requirement: str = "preserve-visible-provenance"
    allow_extra_fields: bool = True
    aliases: tuple[str, ...] = ()
    theorem_targets: tuple[str, ...] = _THEOREM_TARGETS
    root_preprocess: Callable[[Any], Any] | None = None

    def __post_init__(self) -> None:
        field_names = [field.name for field in self.fields]
        if len(field_names) != len(set(field_names)):
            raise ValueError(f"Schema {self.name!r} declares duplicate field names.")

    @property
    def schema_id(self) -> str:
        return f"{SCHEMA_ID_PREFIX}/{self.name}/{self.version}"

    @property
    def field_map(self) -> dict[str, FieldSpec]:
        return {field.name: field for field in self.fields}

    def required_field_names(self) -> tuple[str, ...]:
        return tuple(field.name for field in self.fields if field.required)

    def descriptor(self, registry: "SchemaRegistry | None" = None) -> JsonObject:
        return {
            "$schema": SCHEMA_DIALECT,
            "$id": self.schema_id,
            "title": self.title,
            "description": self.description,
            "type": "object",
            "version": self.version,
            "properties": {field.name: field.json_schema_property(registry) for field in self.fields},
            "required": list(self.required_field_names()),
            "additionalProperties": self.allow_extra_fields,
            "x-jugeo": {
                "schema_name": self.name,
                "aliases": list(self.aliases),
                "semantic_boundary": self.semantic_boundary,
                "trust_boundary": self.trust_boundary,
                "provenance_requirement": self.provenance_requirement,
                "theory_source": THEORY_SOURCE,
                "structural_blueprint": BLUEPRINT_SOURCE,
                "generation_order": GENERATION_ORDER_SOURCE,
                "theorem_targets": list(self.theorem_targets),
                "field_descriptors": {field.name: field.to_descriptor(registry) for field in self.fields},
            },
        }

    def validate(
        self,
        payload: Any,
        *,
        registry: "SchemaRegistry | None" = None,
        path: str = "payload",
    ) -> ValidationResult:
        active_registry = registry or DEFAULT_SCHEMA_REGISTRY
        candidate = self.root_preprocess(payload) if self.root_preprocess is not None else payload
        projected = _project_mapping(candidate)
        if projected is None:
            return ValidationResult(
                schema_name=self.name,
                normalized=None,
                issues=(_issue(path, "type-mismatch", "Payload must normalize to a JSON object.", expected="object", actual=_safe_actual(candidate)),),
            )
        normalized: JsonObject = {}
        issues: list[SchemaIssue] = []
        consumed_keys: set[str] = set()

        for field in self.fields:
            raw_key, raw_value = field.read_from(projected)
            if raw_key is None:
                if field.has_default():
                    normalized[field.name] = cast(JsonValue, field.default)
                elif field.required:
                    issues.append(_issue(f"{path}.{field.name}", "missing-field", f"Missing required field {field.name!r}."))
                continue
            consumed_keys.add(raw_key)
            value, field_issues = field.normalize_value(raw_value, registry=active_registry, path=f"{path}.{field.name}")
            if value is not None or field.nullable:
                normalized[field.name] = value
            issues.extend(field_issues)

        extra_keys = sorted(set(projected) - consumed_keys - set(self.field_map))
        if extra_keys and not self.allow_extra_fields:
            for extra_key in extra_keys:
                issues.append(_issue(f"{path}.{extra_key}", "unknown-field", f"Unknown field {extra_key!r} is not admitted by schema {self.name!r}."))
        elif extra_keys:
            for extra_key in extra_keys:
                normalized[extra_key] = _jsonify(projected[extra_key])

        return ValidationResult(schema_name=self.name, normalized=_ordered_payload(self, normalized), issues=tuple(issues))

    def encode(
        self,
        payload: Any,
        *,
        registry: "SchemaRegistry | None" = None,
        indent: int | None = None,
        include_descriptor: bool = False,
    ) -> str:
        active_registry = registry or DEFAULT_SCHEMA_REGISTRY
        validated = self.validate(payload, registry=active_registry)
        normalized = validated.raise_for_errors()
        wire: JsonObject = {
            WIRE_SCHEMA_NAME_KEY: self.name,
            WIRE_SCHEMA_VERSION_KEY: self.version,
            WIRE_SEMANTIC_BOUNDARY_KEY: self.semantic_boundary,
            WIRE_TRUST_BOUNDARY_KEY: self.trust_boundary,
            WIRE_PROVENANCE_REQUIREMENT_KEY: self.provenance_requirement,
            WIRE_PAYLOAD_KEY: normalized,
        }
        wire[WIRE_PAYLOAD_HASH_KEY] = _sha256(_canonical_json(normalized))
        if include_descriptor:
            wire["schema_descriptor"] = self.descriptor(active_registry)
        return _canonical_json(wire, indent=indent)

    def decode(
        self,
        payload: str | bytes | Mapping[str, Any],
        *,
        registry: "SchemaRegistry | None" = None,
    ) -> JsonObject:
        active_registry = registry or DEFAULT_SCHEMA_REGISTRY
        raw = _load_wire_input(payload)
        if WIRE_PAYLOAD_KEY in raw:
            declared_name = raw.get(WIRE_SCHEMA_NAME_KEY)
            if declared_name is not None and str(declared_name) not in {self.name, *self.aliases}:
                raise SchemaDecodeError(
                    f"Envelope declares schema {declared_name!r}, not {self.name!r}."
                )
            hashed_payload = raw.get(WIRE_PAYLOAD_HASH_KEY)
            candidate_payload = raw[WIRE_PAYLOAD_KEY]
            if hashed_payload is not None:
                actual_hash = _sha256(_canonical_json(_jsonify(candidate_payload)))
                if actual_hash != str(hashed_payload):
                    raise SchemaDecodeError("Wire payload hash mismatch; envelope contents were altered or corrupted.")
            validated = self.validate(candidate_payload, registry=active_registry)
            return validated.raise_for_errors()
        return self.validate(raw, registry=active_registry).raise_for_errors()


ObjectSchema = WireSchema


class SchemaRegistry:
    """Registry of named :class:`WireSchema` contracts."""

    _default_instance: ClassVar["SchemaRegistry | None"] = None

    def __init__(self) -> None:
        self._schemas: dict[str, WireSchema] = {}
        self._aliases: dict[str, str] = {}

    def register(self, schema: WireSchema) -> None:
        self._schemas[schema.name] = schema
        for alias in schema.aliases:
            self._aliases[alias] = schema.name

    def resolve(self, schema: str | WireSchema) -> WireSchema:
        if isinstance(schema, WireSchema):
            return schema
        name = self._aliases.get(schema, schema)
        try:
            return self._schemas[name]
        except KeyError as exc:
            raise KeyError(f"Unknown wire schema {schema!r}. Known schemas: {sorted(self._schemas)}") from exc

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._schemas))

    def aliases(self) -> Mapping[str, str]:
        return dict(self._aliases)

    def validate(self, schema: str | WireSchema, payload: Any) -> ValidationResult:
        return self.resolve(schema).validate(payload, registry=self)

    def encode(
        self,
        schema: str | WireSchema,
        payload: Any,
        *,
        indent: int | None = None,
        include_descriptor: bool = False,
    ) -> str:
        return self.resolve(schema).encode(payload, registry=self, indent=indent, include_descriptor=include_descriptor)

    def decode(self, payload: str | bytes | Mapping[str, Any], schema: str | WireSchema | None = None) -> JsonObject:
        raw = _load_wire_input(payload)
        target = schema
        if target is None:
            declared = raw.get(WIRE_SCHEMA_NAME_KEY)
            if declared is None:
                raise SchemaDecodeError("Cannot infer schema: envelope does not declare one and no schema argument was provided.")
            target = str(declared)
        return self.resolve(target).decode(raw, registry=self)

    def descriptor_bundle(self) -> JsonObject:
        return {
            "version": SCHEMA_VERSION,
            "theory_source": THEORY_SOURCE,
            "schemas": {name: schema.descriptor(self) for name, schema in sorted(self._schemas.items())},
        }

    @classmethod
    def default(cls) -> "SchemaRegistry":
        if cls._default_instance is None:
            cls._default_instance = _build_default_registry()
        return cls._default_instance


def _issue(
    path: str,
    code: str,
    message: str,
    *,
    severity: Literal["error", "warning"] = "error",
    expected: str | None = None,
    actual: JsonValue | None = None,
) -> SchemaIssue:
    return SchemaIssue(path=path, code=code, message=message, severity=severity, expected=expected, actual=actual)


def _safe_actual(value: Any) -> JsonValue | None:
    try:
        return _jsonify(value)
    except TypeError:
        return str(type(value).__name__)


def _primitive_property_descriptor(value_type: FieldType, enum_values: tuple[str, ...]) -> JsonObject:
    descriptor: JsonObject = {}
    if value_type != "any":
        descriptor["type"] = value_type
    if enum_values:
        descriptor["enum"] = list(enum_values)
    return descriptor


def _is_non_text_sequence(value: Any) -> bool:
    return isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray, memoryview))


def _canonical_json(value: JsonValue, *, indent: int | None = None) -> str:
    if indent is None:
        return json.dumps(value, sort_keys=True, separators=(",", ":"))
    return json.dumps(value, sort_keys=True, indent=indent)


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _call_zero_arg_method(value: Any, method_names: Sequence[str]) -> Any:
    for method_name in method_names:
        candidate = getattr(value, method_name, None)
        if candidate is None or not callable(candidate):
            continue
        signature = inspect.signature(candidate)
        if signature.parameters:
            continue
        return candidate()
    return _MISSING


def _project_mapping(value: Any) -> JsonObject | None:
    projected = _project_object(value)
    if not isinstance(projected, dict):
        return None
    return projected


def _project_object(value: Any) -> JsonValue:
    if isinstance(value, Mapping):
        return {str(key): _jsonify(item) for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))}
    return _jsonify(value)


def _jsonify(value: Any) -> JsonValue:
    if value is None or isinstance(value, (bool, int, float, str)):
        return cast(JsonValue, value)
    if isinstance(value, Enum):
        return _jsonify(value.value)
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        return {
            "__kind__": "bytes",
            "base64": base64.b64encode(bytes(value)).decode("ascii"),
        }
    if is_dataclass(value) and not isinstance(value, type):
        return {
            field.name: _jsonify(getattr(value, field.name))
            for field in fields(value)
        }
    if isinstance(value, Mapping):
        return {str(key): _jsonify(item) for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))}
    if _is_non_text_sequence(value):
        return [_jsonify(item) for item in value]

    projected = _call_zero_arg_method(value, ("to_dict", "snapshot", "project_public", "serialize"))
    if projected is not _MISSING:
        if isinstance(projected, str):
            stripped = projected.strip()
            if stripped.startswith("{") or stripped.startswith("["):
                return _jsonify(json.loads(projected))
            return stripped
        return _jsonify(projected)

    if hasattr(value, "__dict__"):
        public_attributes = {name: attr for name, attr in vars(value).items() if not name.startswith("_")}
        if public_attributes:
            return _jsonify(public_attributes)
    raise TypeError(f"Value of type {type(value).__name__!r} is not JSON-compatible for JuGeo wire transport.")


def _normalize_symbol(text: str) -> str:
    return text.strip().lower().replace("-", "_").replace(" ", "_")


def _normalize_trust_label(value: Any) -> str:
    if isinstance(value, Enum):
        return _normalize_trust_label(value.value)
    if isinstance(value, int) and not isinstance(value, bool):
        trust_map = {
            0: "untrusted",
            1: "proposal",
            2: "reviewed",
            3: "verified",
            10: "provisional",
            20: "reviewed",
            30: "verified",
            40: "certified",
        }
        return trust_map.get(value, str(value))
    normalized = _normalize_symbol(str(value))
    alias_map = {
        "mechanicallyverified": "mechanically_verified",
        "solverdischarged": "solver_discharged",
        "runtimewitnessed": "runtime_witnessed",
        "humanattested": "human_attested",
        "oracleproposed": "oracle_proposed",
        "copilotsuggested": "copilot_suggested",
    }
    normalized = alias_map.get(normalized, normalized)
    return normalized


def _adapt_trust_profile(value: Any) -> JsonObject:
    if isinstance(value, str) or isinstance(value, Enum) or isinstance(value, int):
        return {
            "tier": _normalize_trust_label(value),
            "support_scope": [],
            "reasons": [],
        }
    projected = _project_mapping(value)
    if projected is None:
        raise TypeError("Trust payloads must normalize to an object, string, enum, or integer label.")
    raw_tier = projected.get("tier", projected.get("trust_level", projected.get("level", projected.get("trust", "proposal"))))
    support_scope = projected.get("support_scope", projected.get("scope", []))
    reasons = projected.get("reasons", projected.get("notes", []))
    if isinstance(support_scope, str):
        support_scope = [support_scope]
    if isinstance(reasons, str):
        reasons = [reasons]
    return {
        "tier": _normalize_trust_label(raw_tier),
        "support_scope": _jsonify(support_scope),
        "reasons": _jsonify(reasons),
    }


def _adapt_provenance_trace(value: Any) -> JsonObject:
    if isinstance(value, str):
        return {"origin": value, "steps": []}
    projected = _project_mapping(value)
    if projected is None:
        raise TypeError("Provenance payloads must normalize to a mapping or string origin.")
    origin = projected.get("origin", projected.get("source", ""))
    steps = projected.get("steps", projected.get("trace", []))
    if isinstance(steps, str):
        steps = [{"actor": "unknown", "action": steps, "coordinate": "", "details": {}}]
    return {"origin": _jsonify(origin), "steps": _jsonify(steps)}


def _adapt_diagnostic_report(value: Any) -> JsonObject:
    projected = _project_mapping(value)
    if projected is None:
        raise TypeError("Diagnostic report payloads must normalize to a mapping-like object.")

    messages = projected.get("messages", [])
    if not messages:
        legacy_messages = projected.get("report_id")
        if _is_non_text_sequence(legacy_messages):
            messages = legacy_messages

    return {
        "messages": _jsonify(messages),
        "summary": _jsonify(projected.get("summary", "")),
        "generated_at": _jsonify(projected.get("generated_at")),
        "trust": projected.get("trust"),
        "provenance": projected.get("provenance"),
    }


def _adapt_string_list(value: Any) -> Any:
    if isinstance(value, str):
        return [value]
    return value


def _adapt_obstruction_list(value: Any) -> Any:
    if value is None:
        return []
    if isinstance(value, Mapping):
        return [value]
    return value


def _normalize_primitive(value: Any, value_type: FieldType, path: str) -> tuple[JsonValue | None, SchemaIssue | None]:
    if value_type == "any":
        return _jsonify(value), None
    if value_type == "string":
        normalized = _jsonify(value)
        if isinstance(normalized, str):
            return normalized, None
        return None, _issue(path, "type-mismatch", "Expected a string.", expected="string", actual=_safe_actual(value))
    if value_type == "integer":
        if isinstance(value, int) and not isinstance(value, bool):
            return value, None
        return None, _issue(path, "type-mismatch", "Expected an integer.", expected="integer", actual=_safe_actual(value))
    if value_type == "number":
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return cast(JsonValue, value), None
        return None, _issue(path, "type-mismatch", "Expected a numeric value.", expected="number", actual=_safe_actual(value))
    if value_type == "boolean":
        if isinstance(value, bool):
            return value, None
        return None, _issue(path, "type-mismatch", "Expected a boolean.", expected="boolean", actual=_safe_actual(value))
    if value_type == "object":
        projected = _project_mapping(value)
        if projected is not None:
            return projected, None
        return None, _issue(path, "type-mismatch", "Expected an object.", expected="object", actual=_safe_actual(value))
    if value_type == "array":
        if _is_non_text_sequence(value):
            return [_jsonify(item) for item in value], None
        return None, _issue(path, "type-mismatch", "Expected an array.", expected="array", actual=_safe_actual(value))
    raise ValueError(f"Unsupported field type {value_type!r}.")


def _ordered_payload(schema: WireSchema, payload: JsonObject) -> JsonObject:
    ordered: JsonObject = {}
    declared = {field.name for field in schema.fields}
    for field in schema.fields:
        if field.name in payload:
            ordered[field.name] = payload[field.name]
    for key in sorted(set(payload) - declared):
        ordered[key] = payload[key]
    return ordered


def _load_wire_input(payload: str | bytes | Mapping[str, Any]) -> JsonObject:
    if isinstance(payload, bytes):
        payload = payload.decode("utf-8")
    if isinstance(payload, str):
        decoded = json.loads(payload)
    else:
        decoded = _project_mapping(payload)
    if not isinstance(decoded, dict):
        raise SchemaDecodeError("Wire payload must decode to a JSON object.")
    return decoded


def _make_trust_profile_schema() -> WireSchema:
    return WireSchema(
        name="trust_profile",
        title="JuGeo Trust Profile",
        description="Structured trust surface that preserves tier, scope, and reasons rather than flattening support into a scalar.",
        semantic_boundary="trust",
        trust_boundary="no-silent-promotion",
        allow_extra_fields=True,
        aliases=("trust",),
        root_preprocess=_adapt_trust_profile,
        fields=(
            FieldSpec("tier", value_type="string", enum_values=CANONICAL_TRUST_LABELS, preprocess=_normalize_trust_label, description="Canonical trust label."),
            FieldSpec("support_scope", value_type="string", repeated=True, required=False, default=[], preprocess=_adapt_string_list, description="Support labels or regions that this trust profile covers."),
            FieldSpec("reasons", value_type="string", repeated=True, required=False, default=[], preprocess=_adapt_string_list, description="Explicit reasons, demotions, or promotion justifications."),
        ),
    )


def _make_provenance_step_schema() -> WireSchema:
    return WireSchema(
        name="provenance_step",
        title="JuGeo Provenance Step",
        description="Single append-only provenance step in a lightweight public trace.",
        semantic_boundary="provenance",
        allow_extra_fields=True,
        fields=(
            FieldSpec("actor", value_type="string", description="Agent or subsystem responsible for the step."),
            FieldSpec("action", value_type="string", description="Action taken at this provenance step."),
            FieldSpec("coordinate", value_type="string", description="Semantic coordinate touched by this step."),
            FieldSpec("details", value_type="object", required=False, default={}, description="Additional JSON-compatible detail for the step."),
        ),
    )


def _make_provenance_trace_schema() -> WireSchema:
    return WireSchema(
        name="provenance_trace",
        title="JuGeo Provenance Trace",
        description="Stable lightweight provenance trace used by manifests, reports, and certificates.",
        semantic_boundary="provenance",
        provenance_requirement="preserve-chain-identity",
        aliases=("provenance",),
        root_preprocess=_adapt_provenance_trace,
        fields=(
            FieldSpec("origin", value_type="string", description="Origin or authority root for the trace."),
            FieldSpec("steps", value_type="object", repeated=True, item_ref_schema="provenance_step", required=False, default=[], description="Ordered provenance steps."),
        ),
    )


def _make_diagnostic_message_schema() -> WireSchema:
    return WireSchema(
        name="diagnostic_message",
        title="JuGeo Diagnostic Message",
        description="Single diagnostic message emitted by a public interface or orchestration surface.",
        semantic_boundary="diagnostic",
        fields=(
            FieldSpec("level", value_type="string", enum_values=_DIAGNOSTIC_LEVELS, preprocess=_normalize_symbol, description="Severity label for the diagnostic message."),
            FieldSpec("message", value_type="string", description="Human-readable diagnostic text."),
        ),
    )


def _make_diagnostic_report_schema() -> WireSchema:
    return WireSchema(
        name="diagnostic_report",
        title="JuGeo Diagnostic Report",
        description="Public diagnostic report surface preserving messages, trust, and provenance without inventing closure.",
        semantic_boundary="diagnostic",
        aliases=("diagnostics",),
        root_preprocess=_adapt_diagnostic_report,
        fields=(
            FieldSpec("messages", value_type="object", repeated=True, item_ref_schema="diagnostic_message", required=True, default=[], description="Clausewise or subsystem-level diagnostic messages."),
            FieldSpec("summary", value_type="string", required=False, default="", description="Optional summary text for the report."),
            FieldSpec("generated_at", value_type="string", required=False, nullable=True, default=None, description="Optional ISO-8601 generation timestamp."),
            FieldSpec("trust", required=False, nullable=True, ref_schema="trust_profile", preprocess=_adapt_trust_profile, default=None, description="Optional trust surface explaining the report's authority level."),
            FieldSpec("provenance", required=False, nullable=True, ref_schema="provenance_trace", preprocess=_adapt_provenance_trace, default=None, description="Optional provenance trace explaining how the report was produced."),
        ),
    )


def _make_certificate_schema() -> WireSchema:
    return WireSchema(
        name="certificate",
        title="JuGeo Public Certificate",
        description="Faithful certificate projection that preserves verified claims, residuals, issuer identity, and validity status.",
        semantic_boundary="certificate",
        trust_boundary="certificate-must-not-hide-residuals",
        aliases=("settlement_certificate",),
        fields=(
            FieldSpec("certificate_id", value_type="string", description="Stable identifier for the certificate."),
            FieldSpec("coordinate", value_type="string", description="Coordinate covered by the certificate."),
            FieldSpec("verified", value_type="string", repeated=True, aliases=("verified_propositions",), preprocess=_adapt_string_list, description="Verified propositions attested by the certificate."),
            FieldSpec("trust_level", value_type="string", enum_values=_CERTIFICATE_TRUST_LABELS, preprocess=_normalize_trust_label, description="Public certificate trust label."),
            FieldSpec("evidence_summary", value_type="any", description="Public evidence summary preserved by the certificate."),
            FieldSpec("residuals", value_type="string", repeated=True, aliases=("residual_obligations",), required=False, default=[], preprocess=_adapt_string_list, description="Residual obligations that remain open."),
            FieldSpec("obstructions", value_type="string", repeated=True, required=False, default=[], preprocess=_adapt_string_list, description="Named obstructions or failure summaries that shaped the certificate."),
            FieldSpec("issued_at", value_type="string", required=False, nullable=True, default=None, description="Optional issuance timestamp."),
            FieldSpec("issued_by", value_type="string", aliases=("issuer",), description="Issuing authority or subsystem."),
            FieldSpec("expiry", value_type="string", required=False, nullable=True, default=None, description="Optional expiry timestamp."),
            FieldSpec("valid", value_type="boolean", required=False, default=True, description="Public validity bit reported by the producer."),
        ),
    )


def _make_structured_failure_schema() -> WireSchema:
    return WireSchema(
        name="structured_failure",
        title="JuGeo Structured Failure",
        description="Persistent obstruction-oriented failure payload that preserves scope, trust, provenance, and repair hints.",
        semantic_boundary="failure",
        trust_boundary="failure-must-preserve-challengeability",
        aliases=("failure",),
        fields=(
            FieldSpec("message", value_type="string", description="Human-readable summary of the failure."),
            FieldSpec("scope", value_type="string", preprocess=_normalize_symbol, description="Subsystem scope where the failure occurred."),
            FieldSpec("classification", value_type="string", preprocess=_normalize_symbol, description="Failure classification label."),
            FieldSpec("evidence_family", value_type="string", preprocess=_normalize_symbol, description="Evidence family implicated by the failure."),
            FieldSpec("coordinate", value_type="string", required=False, nullable=True, default=None, description="Affected semantic coordinate, if any."),
            FieldSpec("support_scope", value_type="string", required=False, nullable=True, default=None, description="Support region or scope associated with the failure."),
            FieldSpec("semantic_boundary", value_type="string", required=False, nullable=True, default=None, description="Semantic boundary crossed or violated."),
            FieldSpec("trust_boundary", value_type="string", required=False, nullable=True, default=None, description="Trust boundary relevant to the failure."),
            FieldSpec("obstruction", value_type="object", required=False, nullable=True, default=None, description="Optional obstruction object carried with the failure."),
            FieldSpec("repair_hints", value_type="object", repeated=True, required=False, default=[], description="Repair hint objects or summaries."),
            FieldSpec("affected_obligations", value_type="string", repeated=True, required=False, default=[], preprocess=_adapt_string_list, description="Residual obligations affected by this failure."),
            FieldSpec("provenance", value_type="object", required=False, default={}, description="Provenance payload attached to the failure."),
            FieldSpec("trust", value_type="object", required=False, default={}, description="Trust-accounting context attached to the failure."),
            FieldSpec("metadata", value_type="object", required=False, default={}, description="Extra metadata preserved across boundaries."),
            FieldSpec("exception_type", value_type="string", required=False, nullable=True, default=None, description="Original exception type, if any."),
            FieldSpec("notes", value_type="string", repeated=True, required=False, default=[], preprocess=_adapt_string_list, description="Free-text notes attached to the failure."),
            FieldSpec("traceback_lines", value_type="string", repeated=True, required=False, default=[], preprocess=_adapt_string_list, description="Traceback lines or summaries."),
            FieldSpec("is_coboundary", value_type="boolean", required=False, nullable=True, default=None, description="Whether the obstruction is trivially resolvable."),
            FieldSpec("recoverable", value_type="boolean", required=False, default=False, description="Whether the failure is expected to be recoverable."),
        ),
    )


def _make_judgment_schema() -> WireSchema:
    return WireSchema(
        name="judgment",
        title="JuGeo Judgment Wire Record",
        description="Stable public judgment contract shaped by the theory tuple (c, φ, A, E, O, B, T, Π).",
        semantic_boundary="judgment",
        trust_boundary="preserve-clausewise-trust",
        provenance_requirement="preserve-derivation-route",
        fields=(
            FieldSpec("coordinate", value_type="string", description="Coordinate c where the judgment lives."),
            FieldSpec("proposition", value_type="string", description="Judged proposition φ."),
            FieldSpec("carrier", value_type="any", aliases=("artifact",), required=False, default=None, description="Carrier or artifact A associated with the judgment."),
            FieldSpec("status", value_type="string", required=False, default="residual", preprocess=_normalize_symbol, description="Public judgment status."),
            FieldSpec("evidence", value_type="object", repeated=True, required=False, default=[], description="Evidence bundle projections E."),
            FieldSpec("obligations", value_type="any", repeated=True, required=False, default=[], preprocess=_adapt_string_list, description="Residual obligations O."),
            FieldSpec("obstructions", value_type="object", repeated=True, aliases=("blame",), required=False, default=[], preprocess=_adapt_obstruction_list, description="Obstructions B associated with the judgment."),
            FieldSpec("trust", required=False, nullable=True, ref_schema="trust_profile", preprocess=_adapt_trust_profile, default=None, description="Structured trust profile T."),
            FieldSpec("provenance", required=False, nullable=True, ref_schema="provenance_trace", preprocess=_adapt_provenance_trace, default=None, description="Provenance trace Π."),
            FieldSpec("metadata", value_type="object", required=False, default={}, description="Additional stable metadata for future interfaces."),
        ),
    )


def _make_manifest_schema() -> WireSchema:
    return WireSchema(
        name="manifest",
        title="JuGeo Manifest Snapshot",
        description="Persistent semantic memory surface M = (J, O, E, X, K, η, σ) for reports, replay, and diagnostics.",
        semantic_boundary="manifest",
        trust_boundary="manifest-must-remain-conservative-when-stale",
        provenance_requirement="preserve-traceability-across-reload",
        aliases=("manifest_snapshot",),
        fields=(
            FieldSpec("manifest_id", value_type="string", description="Stable manifest identifier."),
            FieldSpec("created_at", value_type="number", required=False, default=0.0, description="Creation timestamp or epoch-like marker."),
            FieldSpec("judgments", value_type="object", repeated=True, required=False, default=[], description="Persisted judgment family J."),
            FieldSpec("obligations", value_type="object", repeated=True, required=False, default=[], description="Live obligations O."),
            FieldSpec("evidence_archive", value_type="object", repeated=True, required=False, default=[], description="Evidence archive E."),
            FieldSpec("obstructions", value_type="object", repeated=True, required=False, default=[], description="Obstruction archive X."),
            FieldSpec("certificates", value_type="object", repeated=True, item_ref_schema="certificate", required=False, default=[], description="Public certificate family K."),
            FieldSpec("epoch_map", value_type="object", required=False, default={}, description="Epoch map η."),
            FieldSpec("invalidation_graph", value_type="object", required=False, default={}, description="Support-indexed invalidation graph σ."),
        ),
    )


def _make_report_schema() -> WireSchema:
    return WireSchema(
        name="report",
        title="JuGeo Public Report",
        description="Human-facing but machine-honest report surface joining summary text with diagnostics, manifests, trust, and provenance.",
        semantic_boundary="report",
        trust_boundary="report-must-not-overclaim",
        fields=(
            FieldSpec("report_id", value_type="string", description="Stable report identifier."),
            FieldSpec("title", value_type="string", description="Human-readable report title."),
            FieldSpec("status", value_type="string", enum_values=_REPORT_STATUSES, preprocess=_normalize_symbol, description="Overall report status."),
            FieldSpec("summary", value_type="string", description="Human-readable summary that must remain faithful to the semantic state."),
            FieldSpec("items", value_type="object", repeated=True, required=False, default=[], description="Report items or clausewise details."),
            FieldSpec("diagnostics", required=False, nullable=True, ref_schema="diagnostic_report", default=None, description="Nested diagnostic report for this report surface."),
            FieldSpec("manifest", required=False, nullable=True, ref_schema="manifest", default=None, description="Manifest snapshot referenced by the report."),
            FieldSpec("trust", required=False, nullable=True, ref_schema="trust_profile", preprocess=_adapt_trust_profile, default=None, description="Trust profile for the report's claims."),
            FieldSpec("provenance", required=False, nullable=True, ref_schema="provenance_trace", preprocess=_adapt_provenance_trace, default=None, description="Provenance trace explaining report production."),
        ),
    )


def _build_default_registry() -> SchemaRegistry:
    registry = SchemaRegistry()
    for schema in (
        _make_trust_profile_schema(),
        _make_provenance_step_schema(),
        _make_provenance_trace_schema(),
        _make_diagnostic_message_schema(),
        _make_diagnostic_report_schema(),
        _make_certificate_schema(),
        _make_structured_failure_schema(),
        _make_judgment_schema(),
        _make_manifest_schema(),
        _make_report_schema(),
    ):
        registry.register(schema)
    return registry


DEFAULT_SCHEMA_REGISTRY = SchemaRegistry.default()


def default_schema_registry() -> SchemaRegistry:
    """Return the process-wide default registry."""

    return DEFAULT_SCHEMA_REGISTRY


def get_wire_schema(name: str, *, registry: SchemaRegistry | None = None) -> WireSchema:
    """Resolve one named schema from a registry."""

    return (registry or DEFAULT_SCHEMA_REGISTRY).resolve(name)


def encode_wire_payload(
    payload: Any,
    schema: str | WireSchema,
    *,
    registry: SchemaRegistry | None = None,
    indent: int | None = None,
    include_descriptor: bool = False,
) -> str:
    """Encode *payload* into a deterministic JuGeo wire envelope."""

    active_registry = registry or DEFAULT_SCHEMA_REGISTRY
    return active_registry.encode(schema, payload, indent=indent, include_descriptor=include_descriptor)


def decode_wire_payload(
    payload: str | bytes | Mapping[str, Any],
    schema: str | WireSchema | None = None,
    *,
    registry: SchemaRegistry | None = None,
) -> JsonObject:
    """Decode a JuGeo wire envelope or raw payload into its normalized form."""

    active_registry = registry or DEFAULT_SCHEMA_REGISTRY
    return active_registry.decode(payload, schema)


def trust_profile_schema() -> JsonObject:
    return DEFAULT_SCHEMA_REGISTRY.resolve("trust_profile").descriptor(DEFAULT_SCHEMA_REGISTRY)


def provenance_trace_schema() -> JsonObject:
    return DEFAULT_SCHEMA_REGISTRY.resolve("provenance_trace").descriptor(DEFAULT_SCHEMA_REGISTRY)


def diagnostic_report_schema() -> JsonObject:
    return DEFAULT_SCHEMA_REGISTRY.resolve("diagnostic_report").descriptor(DEFAULT_SCHEMA_REGISTRY)


def judgment_schema() -> JsonObject:
    return DEFAULT_SCHEMA_REGISTRY.resolve("judgment").descriptor(DEFAULT_SCHEMA_REGISTRY)


def certificate_schema() -> JsonObject:
    return DEFAULT_SCHEMA_REGISTRY.resolve("certificate").descriptor(DEFAULT_SCHEMA_REGISTRY)


def manifest_schema() -> JsonObject:
    return DEFAULT_SCHEMA_REGISTRY.resolve("manifest").descriptor(DEFAULT_SCHEMA_REGISTRY)


def report_schema() -> JsonObject:
    return DEFAULT_SCHEMA_REGISTRY.resolve("report").descriptor(DEFAULT_SCHEMA_REGISTRY)


__all__ = [
    "SCHEMA_VERSION",
    "THEORY_SOURCE",
    "BLUEPRINT_SOURCE",
    "GENERATION_ORDER_SOURCE",
    "SchemaError",
    "SchemaValidationError",
    "SchemaDecodeError",
    "SchemaIssue",
    "ValidationResult",
    "FieldSpec",
    "WireSchema",
    "ObjectSchema",
    "SchemaRegistry",
    "DEFAULT_SCHEMA_REGISTRY",
    "default_schema_registry",
    "get_wire_schema",
    "encode_wire_payload",
    "decode_wire_payload",
    "trust_profile_schema",
    "provenance_trace_schema",
    "diagnostic_report_schema",
    "judgment_schema",
    "certificate_schema",
    "manifest_schema",
    "report_schema",
]
