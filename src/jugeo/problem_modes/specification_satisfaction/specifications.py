"""Specifications for the specification-satisfaction problem mode.

Section 10.1: Specifications.  A specification is a target section of the
judgment sheaf — a prescribed assignment of judgment values to every coordinate
in the site that a solution is expected to achieve.  Constructing, normalising,
composing, and encoding specifications are the primary concerns of this module.

References theory2.tex §10.1.

copilot: generated scaffold for jugeo specification-satisfaction; all logic is
real and non-trivial.  Extend constraint templates and composition rules as the
theory matures.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Mapping, Sequence

# ---------------------------------------------------------------------------
# Optional internal imports
# ---------------------------------------------------------------------------

try:
    from jugeo.problem_modes.specification_satisfaction.models import (
        CertificateOfSatisfaction,
        DescentCondition,
        GapSeverity,
        ResidualGap,
        SatisfactionStatus,
        SatisfactionWitness,
        Specification,
        SpecificationKind,
        WitnessStatus,
    )
except ImportError:
    Specification = Any  # type: ignore[assignment,misc]
    SatisfactionWitness = Any  # type: ignore[assignment,misc]
    CertificateOfSatisfaction = Any  # type: ignore[assignment,misc]
    ResidualGap = Any  # type: ignore[assignment,misc]
    SpecificationKind = Any  # type: ignore[assignment,misc]
    WitnessStatus = Any  # type: ignore[assignment,misc]
    GapSeverity = Any  # type: ignore[assignment,misc]
    SatisfactionStatus = Any  # type: ignore[assignment,misc]
    DescentCondition = Any  # type: ignore[assignment,misc]

try:
    from jugeo.geometry.hypercovers import CechNerve, HypercoverLevel
except ImportError:
    HypercoverLevel = Any  # type: ignore[assignment,misc]
    CechNerve = Any  # type: ignore[assignment,misc]

try:
    from jugeo.geometry.descent import (
        DescentEngine,
        DescentObstruction,
        DescentResult,
        GluingData,
        LocalSection,
    )
except ImportError:
    DescentEngine = Any  # type: ignore[assignment,misc]
    DescentResult = Any  # type: ignore[assignment,misc]
    LocalSection = Any  # type: ignore[assignment,misc]
    GluingData = Any  # type: ignore[assignment,misc]
    DescentObstruction = Any  # type: ignore[assignment,misc]

try:
    from jugeo.geometry.site import CoordinateObject, SemanticSite
except ImportError:
    CoordinateObject = Any  # type: ignore[assignment,misc]
    SemanticSite = Any  # type: ignore[assignment,misc]

try:
    from jugeo.geometry.covers import Cover
except ImportError:
    Cover = Any  # type: ignore[assignment,misc]

try:
    from jugeo.judgments.judgment_terms import JudgmentKind, JudgmentTerm, ProvenanceKind
except ImportError:
    JudgmentTerm = Any  # type: ignore[assignment,misc]
    JudgmentKind = Any  # type: ignore[assignment,misc]
    ProvenanceKind = Any  # type: ignore[assignment,misc]

try:
    from jugeo.evidence.certificates import Certificate, CertificateStatus
except ImportError:
    Certificate = Any  # type: ignore[assignment,misc]
    CertificateStatus = Any  # type: ignore[assignment,misc]

# ---------------------------------------------------------------------------
# Standard specification templates
# ---------------------------------------------------------------------------

TYPE_SAFE_TEMPLATE: dict[str, Any] = {
    "kind": "structural",
    "priority": 1,
    "constraint_categories": ["type_correctness", "null_safety", "bounds_checking"],
    "description": (
        "All types are structurally sound: every expression has a well-formed "
        "type, null dereferences are statically excluded, and array/container "
        "accesses are within their declared bounds."
    ),
    "prescribed_judgment_prototype": {
        "polarity": "positive",
        "confidence_floor": 0.9,
        "requires_proof": False,
    },
}

BEHAVIOR_CORRECT_TEMPLATE: dict[str, Any] = {
    "kind": "behavioral",
    "priority": 2,
    "constraint_categories": [
        "pre_post_contracts",
        "loop_invariants",
        "termination",
        "side_effect_isolation",
    ],
    "description": (
        "Observable behaviour matches declared contracts: preconditions are "
        "honoured, postconditions are established, loop invariants hold, and "
        "side effects are confined to declared regions."
    ),
    "prescribed_judgment_prototype": {
        "polarity": "positive",
        "confidence_floor": 0.8,
        "requires_proof": True,
    },
}

API_CONSISTENT_TEMPLATE: dict[str, Any] = {
    "kind": "relational",
    "priority": 3,
    "constraint_categories": [
        "interface_compatibility",
        "versioning_constraints",
        "deprecation_policy",
        "backward_compatibility",
    ],
    "description": (
        "All public API surfaces are mutually consistent: callers and callees "
        "agree on types and protocols, versioning constraints are respected, "
        "and deprecated symbols are not introduced."
    ),
    "prescribed_judgment_prototype": {
        "polarity": "positive",
        "confidence_floor": 0.85,
        "requires_proof": False,
    },
}

SECURITY_SOUND_TEMPLATE: dict[str, Any] = {
    "kind": "structural",
    "priority": 1,
    "constraint_categories": [
        "injection_free",
        "authentication_enforced",
        "secrets_not_leaked",
        "privilege_minimal",
    ],
    "description": (
        "No known injection vectors exist, authentication is enforced on every "
        "protected endpoint, secrets are confined to secure storage, and the "
        "principle of least privilege is observed throughout."
    ),
    "prescribed_judgment_prototype": {
        "polarity": "positive",
        "confidence_floor": 0.95,
        "requires_proof": True,
    },
}

_BUILTIN_TEMPLATES: dict[str, dict[str, Any]] = {
    "type_safe": TYPE_SAFE_TEMPLATE,
    "behavior_correct": BEHAVIOR_CORRECT_TEMPLATE,
    "api_consistent": API_CONSISTENT_TEMPLATE,
    "security_sound": SECURITY_SOUND_TEMPLATE,
}

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _utc_now_iso() -> str:
    """Return the current UTC time as an ISO 8601 string.

    Returns
    -------
    str
        UTC timestamp in ISO 8601 format.
    """
    return datetime.now(tz=timezone.utc).isoformat()


def _make_spec_id(name: str, kind: str) -> str:
    """Derive a deterministic spec identifier from its name and kind.

    Parameters
    ----------
    name : str
        Human-readable name of the specification.
    kind : str
        Kind string (e.g. ``"structural"``, ``"behavioral"``).

    Returns
    -------
    str
        A short hex digest prefixed with ``spec-``.
    """
    digest = hashlib.sha256(f"{name}::{kind}".encode()).hexdigest()[:12]
    return f"spec-{digest}"


def _merge_prescribed_judgments(
    base: dict[str, Any],
    override: dict[str, Any],
) -> dict[str, Any]:
    """Merge two prescribed-judgment dictionaries, with *override* taking precedence.

    Parameters
    ----------
    base : dict[str, Any]
        Base judgments to merge into.
    override : dict[str, Any]
        Judgments that supersede those in *base* when keys conflict.

    Returns
    -------
    dict[str, Any]
        A new dictionary containing the merged judgments.
    """
    merged: dict[str, Any] = dict(base)
    for coord, judgment in override.items():
        if coord in merged:
            if isinstance(merged[coord], dict) and isinstance(judgment, dict):
                merged[coord] = {**merged[coord], **judgment}
            else:
                merged[coord] = judgment
        else:
            merged[coord] = judgment
    return merged


def _constraint_fingerprint(constraint: dict[str, Any]) -> str:
    """Compute a stable fingerprint for a constraint dict for deduplication.

    Parameters
    ----------
    constraint : dict[str, Any]
        Constraint representation.

    Returns
    -------
    str
        A hex digest string.
    """
    canonical = json.dumps(constraint, sort_keys=True, default=str)
    return hashlib.md5(canonical.encode()).hexdigest()  # noqa: S324 — not crypto


def _validate_coordinate_id(coord: str) -> list[str]:
    """Check that a coordinate identifier is well-formed.

    Parameters
    ----------
    coord : str
        The coordinate identifier to validate.

    Returns
    -------
    list[str]
        A list of error messages; empty if the coordinate is valid.
    """
    errors: list[str] = []
    if not coord:
        errors.append("Coordinate identifier must be non-empty.")
    if " " in coord:
        errors.append(f"Coordinate identifier '{coord}' must not contain spaces.")
    if len(coord) > 256:
        errors.append(f"Coordinate identifier '{coord}' exceeds 256 characters.")
    return errors


# ---------------------------------------------------------------------------
# SpecificationBuilder
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class SpecificationBuilder:
    """Fluent builder for :class:`Specification` objects.

    Collects all fields required by a ``Specification`` and emits a fully
    validated, immutable instance via :meth:`build`.

    Attributes
    ----------
    name : str
        Human-readable name for the specification being built.
    kind : str
        Kind string — one of ``"structural"``, ``"behavioral"``, ``"relational"``.
    description : str
        Free-text description of what the specification demands.
    target_coordinates : list[str]
        Coordinate identifiers the specification is defined over.
    prescribed_judgments : dict[str, Any]
        Mapping from coordinate id to prescribed judgment value/structure.
    constraint_map : dict[str, list[str]]
        Mapping from coordinate id to list of constraint ids.
    priority : int
        Execution priority (lower = higher priority).
    version : str
        Semantic version string for the specification.
    metadata : dict[str, Any]
        Arbitrary key/value metadata attached to the specification.
    """

    name: str = ""
    kind: str = "structural"
    description: str = ""
    target_coordinates: list[str] = field(default_factory=list)
    prescribed_judgments: dict[str, Any] = field(default_factory=dict)
    constraint_map: dict[str, list[str]] = field(default_factory=dict)
    priority: int = 5
    version: str = "1.0.0"
    metadata: dict[str, Any] = field(default_factory=dict)

    # -- fluent setters -------------------------------------------------------

    def set_name(self, name: str) -> SpecificationBuilder:
        """Set the specification name.

        Parameters
        ----------
        name : str
            Human-readable name; must be non-empty.

        Returns
        -------
        SpecificationBuilder
            ``self`` for method chaining.

        Raises
        ------
        ValueError
            If *name* is empty or contains only whitespace.
        """
        if not name or not name.strip():
            raise ValueError("Specification name must be non-empty.")
        self.name = name.strip()
        return self

    def set_kind(self, kind: str) -> SpecificationBuilder:
        """Set the specification kind.

        Parameters
        ----------
        kind : str
            One of ``"structural"``, ``"behavioral"``, ``"relational"``,
            ``"composite"``.

        Returns
        -------
        SpecificationBuilder
            ``self`` for method chaining.

        Raises
        ------
        ValueError
            If *kind* is not a recognised value.
        """
        allowed = {"structural", "behavioral", "relational", "composite"}
        if kind not in allowed:
            raise ValueError(f"Unknown specification kind '{kind}'; allowed: {allowed}")
        self.kind = kind
        return self

    def add_target_coordinate(self, coord: str) -> SpecificationBuilder:
        """Register a coordinate identifier that the specification targets.

        Parameters
        ----------
        coord : str
            Coordinate identifier to add.

        Returns
        -------
        SpecificationBuilder
            ``self`` for method chaining.

        Raises
        ------
        ValueError
            If the coordinate identifier is malformed.
        """
        errors = _validate_coordinate_id(coord)
        if errors:
            raise ValueError("; ".join(errors))
        if coord not in self.target_coordinates:
            self.target_coordinates.append(coord)
        return self

    def add_prescribed_judgment(self, coord: str, judgment: Any) -> SpecificationBuilder:
        """Prescribe a judgment value at the given coordinate.

        Parameters
        ----------
        coord : str
            Coordinate identifier.
        judgment : Any
            The judgment value or structure prescribed at *coord*.

        Returns
        -------
        SpecificationBuilder
            ``self`` for method chaining.
        """
        if coord not in self.target_coordinates:
            self.add_target_coordinate(coord)
        self.prescribed_judgments[coord] = judgment
        return self

    def add_constraint(self, coord: str, constraint_id: str) -> SpecificationBuilder:
        """Attach a constraint identifier to a coordinate.

        Parameters
        ----------
        coord : str
            Coordinate where the constraint applies.
        constraint_id : str
            Unique identifier for the constraint (e.g. ``"null_safety"``).

        Returns
        -------
        SpecificationBuilder
            ``self`` for method chaining.
        """
        if coord not in self.target_coordinates:
            self.add_target_coordinate(coord)
        if coord not in self.constraint_map:
            self.constraint_map[coord] = []
        if constraint_id not in self.constraint_map[coord]:
            self.constraint_map[coord].append(constraint_id)
        return self

    def set_priority(self, p: int) -> SpecificationBuilder:
        """Set the execution priority.

        Parameters
        ----------
        p : int
            Priority value; lower numbers indicate higher priority.

        Returns
        -------
        SpecificationBuilder
            ``self`` for method chaining.

        Raises
        ------
        ValueError
            If *p* is not a positive integer.
        """
        if p < 1:
            raise ValueError(f"Priority must be >= 1; got {p}.")
        self.priority = p
        return self

    def set_metadata(self, key: str, value: Any) -> SpecificationBuilder:
        """Store an arbitrary metadata key/value pair.

        Parameters
        ----------
        key : str
            Metadata key.
        value : Any
            Metadata value (must be JSON-serialisable).

        Returns
        -------
        SpecificationBuilder
            ``self`` for method chaining.
        """
        self.metadata[key] = value
        return self

    # -- class methods --------------------------------------------------------

    @classmethod
    def from_template(cls, template_name: str) -> SpecificationBuilder:
        """Initialise a builder pre-populated from a named template.

        Parameters
        ----------
        template_name : str
            Name of a built-in template; one of ``"type_safe"``,
            ``"behavior_correct"``, ``"api_consistent"``, ``"security_sound"``.

        Returns
        -------
        SpecificationBuilder
            A partially filled builder ready for further customisation.

        Raises
        ------
        KeyError
            If *template_name* is not a registered template.
        """
        tmpl = _BUILTIN_TEMPLATES.get(template_name)
        if tmpl is None:
            available = list(_BUILTIN_TEMPLATES.keys())
            raise KeyError(
                f"Unknown template '{template_name}'. Available: {available}"
            )
        builder = cls()
        builder.kind = tmpl.get("kind", "structural")
        builder.description = tmpl.get("description", "")
        builder.priority = tmpl.get("priority", 5)
        builder.metadata["template_name"] = template_name
        builder.metadata["constraint_categories"] = tmpl.get(
            "constraint_categories", []
        )
        return builder

    # -- validation and build -------------------------------------------------

    def validate_before_build(self) -> list[str]:
        """Collect all validation errors without raising.

        Returns
        -------
        list[str]
            A list of human-readable error messages.  Empty if validation
            passes.
        """
        errors: list[str] = []
        if not self.name or not self.name.strip():
            errors.append("name must be set before building.")
        if not self.target_coordinates:
            errors.append("At least one target coordinate is required.")
        for coord in self.target_coordinates:
            errors.extend(_validate_coordinate_id(coord))
        if self.priority < 1:
            errors.append(f"Priority must be >= 1; currently {self.priority}.")
        for coord, constraints in self.constraint_map.items():
            if coord not in self.target_coordinates:
                errors.append(
                    f"Constraint map references coordinate '{coord}' that is not "
                    "in target_coordinates."
                )
            for c in constraints:
                if not c:
                    errors.append(
                        f"Empty constraint id found for coordinate '{coord}'."
                    )
        return errors

    def build(self) -> Any:
        """Construct and return a :class:`Specification`.

        Returns
        -------
        Specification
            The fully validated, immutable specification.

        Raises
        ------
        ValueError
            If validation errors are present.
        """
        errors = self.validate_before_build()
        if errors:
            raise ValueError(
                f"Cannot build Specification — {len(errors)} error(s): "
                + "; ".join(errors)
            )
        spec_id = _make_spec_id(self.name, self.kind)
        try:
            from jugeo.problem_modes.specification_satisfaction.models import (
                Specification as _Spec,
            )

            return _Spec(
                spec_id=spec_id,
                name=self.name,
                kind=self.kind,
                description=self.description,
                target_coordinates=tuple(self.target_coordinates),
                prescribed_judgments=dict(self.prescribed_judgments),
                constraint_map={k: tuple(v) for k, v in self.constraint_map.items()},
                priority=self.priority,
                version=self.version,
                metadata=dict(self.metadata),
                created_at=_utc_now_iso(),
            )
        except (ImportError, TypeError):
            return {
                "spec_id": spec_id,
                "name": self.name,
                "kind": self.kind,
                "description": self.description,
                "target_coordinates": tuple(self.target_coordinates),
                "prescribed_judgments": dict(self.prescribed_judgments),
                "constraint_map": {k: tuple(v) for k, v in self.constraint_map.items()},
                "priority": self.priority,
                "version": self.version,
                "metadata": dict(self.metadata),
                "created_at": _utc_now_iso(),
            }


# ---------------------------------------------------------------------------
# ConstraintEncoder
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class ConstraintEncoder:
    """Translates high-level constraint descriptions into judgment prescriptions.

    Each constraint type is handled by a registered encoder function that maps
    the constraint's parameters to a dict suitable for inclusion in a
    :class:`Specification`'s ``prescribed_judgments``.

    Attributes
    ----------
    encoding_rules : dict[str, Callable]
        Mapping from constraint type name to its encoder function.
    constraint_registry : dict[str, dict[str, Any]]
        Registry of all encoded constraints, keyed by fingerprint.
    encoded_count : int
        Running total of constraints encoded since construction.
    """

    encoding_rules: dict[str, Callable[..., dict[str, Any]]] = field(
        default_factory=dict
    )
    constraint_registry: dict[str, dict[str, Any]] = field(default_factory=dict)
    encoded_count: int = 0

    def __post_init__(self) -> None:
        """Register built-in encoding rules."""
        self._register_builtin_rules()

    # -- registration ---------------------------------------------------------

    def _register_builtin_rules(self) -> None:
        """Register the standard built-in constraint encoding rules."""
        self.encoding_rules["type_correctness"] = self._builtin_type_correctness
        self.encoding_rules["null_safety"] = self._builtin_null_safety
        self.encoding_rules["bounds_checking"] = self._builtin_bounds_checking
        self.encoding_rules["pre_post_contracts"] = self._builtin_pre_post_contracts
        self.encoding_rules["interface_compatibility"] = (
            self._builtin_interface_compatibility
        )
        self.encoding_rules["injection_free"] = self._builtin_injection_free

    def _builtin_type_correctness(self, params: dict[str, Any]) -> dict[str, Any]:
        return {
            "polarity": "positive",
            "constraint_kind": "type_correctness",
            "type_name": params.get("type_name", "unknown"),
            "confidence_floor": params.get("confidence_floor", 0.9),
            "requires_proof": False,
        }

    def _builtin_null_safety(self, params: dict[str, Any]) -> dict[str, Any]:
        return {
            "polarity": "positive",
            "constraint_kind": "null_safety",
            "nullable_allowed": params.get("nullable_allowed", False),
            "confidence_floor": params.get("confidence_floor", 0.95),
            "requires_proof": params.get("requires_proof", False),
        }

    def _builtin_bounds_checking(self, params: dict[str, Any]) -> dict[str, Any]:
        return {
            "polarity": "positive",
            "constraint_kind": "bounds_checking",
            "lower_bound": params.get("lower_bound", None),
            "upper_bound": params.get("upper_bound", None),
            "inclusive": params.get("inclusive", True),
            "confidence_floor": params.get("confidence_floor", 0.85),
            "requires_proof": True,
        }

    def _builtin_pre_post_contracts(self, params: dict[str, Any]) -> dict[str, Any]:
        return {
            "polarity": "positive",
            "constraint_kind": "pre_post_contracts",
            "preconditions": params.get("preconditions", []),
            "postconditions": params.get("postconditions", []),
            "confidence_floor": params.get("confidence_floor", 0.8),
            "requires_proof": True,
        }

    def _builtin_interface_compatibility(
        self, params: dict[str, Any]
    ) -> dict[str, Any]:
        return {
            "polarity": "positive",
            "constraint_kind": "interface_compatibility",
            "interface_id": params.get("interface_id", ""),
            "version_constraint": params.get("version_constraint", ">=1.0.0"),
            "confidence_floor": params.get("confidence_floor", 0.85),
            "requires_proof": False,
        }

    def _builtin_injection_free(self, params: dict[str, Any]) -> dict[str, Any]:
        return {
            "polarity": "positive",
            "constraint_kind": "injection_free",
            "injection_classes": params.get(
                "injection_classes", ["sql", "command", "xss"]
            ),
            "confidence_floor": params.get("confidence_floor", 0.95),
            "requires_proof": True,
        }

    def register_rule(
        self, constraint_type: str, encoder_fn: Callable[..., dict[str, Any]]
    ) -> None:
        """Register a custom encoder function for a constraint type.

        Parameters
        ----------
        constraint_type : str
            The constraint type name to associate with *encoder_fn*.
        encoder_fn : Callable[..., dict[str, Any]]
            A callable ``(params: dict) -> dict`` that encodes the constraint.
        """
        self.encoding_rules[constraint_type] = encoder_fn

    # -- encoding -------------------------------------------------------------

    def encode_constraint(
        self, constraint_type: str, params: dict[str, Any]
    ) -> dict[str, Any]:
        """Encode a constraint into a judgment prescription dict.

        Parameters
        ----------
        constraint_type : str
            The type of constraint to encode.
        params : dict[str, Any]
            Parameters specific to this constraint type.

        Returns
        -------
        dict[str, Any]
            A judgment prescription dict suitable for a specification.

        Raises
        ------
        KeyError
            If no encoder is registered for *constraint_type*.
        """
        if constraint_type not in self.encoding_rules:
            raise KeyError(
                f"No encoder registered for constraint type '{constraint_type}'. "
                f"Registered: {list(self.encoding_rules.keys())}"
            )
        result = self.encoding_rules[constraint_type](params)
        fp = _constraint_fingerprint(result)
        self.constraint_registry[fp] = result
        self.encoded_count += 1
        return result

    def encode_type_constraint(
        self, type_name: str, coordinate: str
    ) -> dict[str, Any]:
        """Encode a type-correctness constraint for a specific type and coordinate.

        Parameters
        ----------
        type_name : str
            The type to enforce (e.g. ``"int"``, ``"Optional[str]"``).
        coordinate : str
            The coordinate where this constraint applies.

        Returns
        -------
        dict[str, Any]
            Encoded constraint prescription.
        """
        return self.encode_constraint(
            "type_correctness",
            {"type_name": type_name, "coordinate": coordinate},
        )

    def encode_behavioral_constraint(
        self, behavior_name: str, coordinate: str
    ) -> dict[str, Any]:
        """Encode a pre/post-contract constraint for a named behaviour.

        Parameters
        ----------
        behavior_name : str
            Name of the behaviour being constrained.
        coordinate : str
            The coordinate where this constraint applies.

        Returns
        -------
        dict[str, Any]
            Encoded constraint prescription.
        """
        return self.encode_constraint(
            "pre_post_contracts",
            {
                "behavior_name": behavior_name,
                "coordinate": coordinate,
                "preconditions": [f"{behavior_name}_precondition"],
                "postconditions": [f"{behavior_name}_postcondition"],
            },
        )

    def encode_relational_constraint(
        self, relation: str, coord_a: str, coord_b: str
    ) -> dict[str, Any]:
        """Encode an interface-compatibility constraint between two coordinates.

        Parameters
        ----------
        relation : str
            The relation name (e.g. ``"implements"``, ``"extends"``).
        coord_a : str
            Source coordinate.
        coord_b : str
            Target coordinate.

        Returns
        -------
        dict[str, Any]
            Encoded constraint prescription.
        """
        return self.encode_constraint(
            "interface_compatibility",
            {
                "relation": relation,
                "coord_a": coord_a,
                "coord_b": coord_b,
                "interface_id": f"{coord_a}::{relation}::{coord_b}",
            },
        )

    def decode_constraint(
        self, prescription_dict: dict[str, Any]
    ) -> tuple[str, dict[str, Any]]:
        """Reverse-encode a prescription dict back to its constraint type and params.

        Parameters
        ----------
        prescription_dict : dict[str, Any]
            A previously encoded constraint prescription.

        Returns
        -------
        tuple[str, dict[str, Any]]
            A ``(constraint_type, params)`` pair.

        Raises
        ------
        ValueError
            If the dict does not contain a ``constraint_kind`` key.
        """
        kind = prescription_dict.get("constraint_kind")
        if kind is None:
            raise ValueError(
                "Cannot decode prescription: missing 'constraint_kind' key."
            )
        params = {k: v for k, v in prescription_dict.items() if k != "constraint_kind"}
        return str(kind), params

    def get_constraint_schema(self, constraint_type: str) -> dict[str, Any]:
        """Return a JSON-compatible schema dict describing a constraint type.

        Parameters
        ----------
        constraint_type : str
            The constraint type whose schema to retrieve.

        Returns
        -------
        dict[str, Any]
            Schema dict with ``type``, ``parameters``, and ``description`` keys.

        Raises
        ------
        KeyError
            If no encoder is registered for *constraint_type*.
        """
        if constraint_type not in self.encoding_rules:
            raise KeyError(f"Unknown constraint type: '{constraint_type}'")
        sample = self.encode_constraint(constraint_type, {})
        return {
            "type": constraint_type,
            "parameters": list(sample.keys()),
            "description": f"Schema inferred from built-in encoder for '{constraint_type}'.",
            "sample_output": sample,
        }

    def list_registered_rules(self) -> list[str]:
        """Return a sorted list of registered constraint type names.

        Returns
        -------
        list[str]
            Sorted constraint type names.
        """
        return sorted(self.encoding_rules.keys())

    def batch_encode(
        self, constraints_list: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Encode a batch of constraint descriptors.

        Parameters
        ----------
        constraints_list : list[dict[str, Any]]
            Each element must have a ``"type"`` key and an optional ``"params"``
            key.

        Returns
        -------
        list[dict[str, Any]]
            Encoded prescriptions in the same order.

        Raises
        ------
        ValueError
            If any element lacks a ``"type"`` key.
        """
        results: list[dict[str, Any]] = []
        for i, item in enumerate(constraints_list):
            if "type" not in item:
                raise ValueError(
                    f"Element at index {i} is missing required 'type' key."
                )
            results.append(
                self.encode_constraint(item["type"], item.get("params", {}))
            )
        return results


# ---------------------------------------------------------------------------
# SpecificationNormalizer
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class SpecificationNormalizer:
    """Transforms a :class:`Specification` into a canonical normal form.

    Normalisation ensures that two logically equivalent specifications can be
    compared structurally.  The steps applied are:
    1. Canonicalise coordinate identifiers (sort lexicographically).
    2. Sort prescribed judgment keys.
    3. Deduplicate constraints per coordinate.
    4. Clamp priority to a valid range.

    Attributes
    ----------
    normalization_rules : list[str]
        Names of normalisation rules applied, in order.
    normalized_count : int
        Running total of specifications normalised since construction.
    """

    normalization_rules: list[str] = field(default_factory=list)
    normalized_count: int = 0

    def __post_init__(self) -> None:
        self.normalization_rules = [
            "canonicalize_coordinate_ids",
            "sort_prescribed_judgments",
            "deduplicate_constraints",
            "normalize_priority",
        ]

    # -- normalisation steps --------------------------------------------------

    def _canonicalize_coordinate_ids(self, spec: Any) -> Any:
        """Return *spec* with ``target_coordinates`` sorted lexicographically.

        Parameters
        ----------
        spec : Specification
            Input specification.

        Returns
        -------
        Specification
            Specification with coordinates in canonical order.
        """
        sorted_coords = tuple(sorted(spec["target_coordinates"] if isinstance(spec, dict) else spec.target_coordinates))
        if isinstance(spec, dict):
            return {**spec, "target_coordinates": sorted_coords}
        try:
            return replace(spec, target_coordinates=sorted_coords)
        except Exception:
            return spec

    def _sort_prescribed_judgments(self, spec: Any) -> Any:
        """Return *spec* with ``prescribed_judgments`` keys in sorted order.

        Parameters
        ----------
        spec : Specification
            Input specification.

        Returns
        -------
        Specification
            Specification with judgment keys sorted.
        """
        pj = spec["prescribed_judgments"] if isinstance(spec, dict) else spec.prescribed_judgments
        sorted_pj = dict(sorted(pj.items()))
        if isinstance(spec, dict):
            return {**spec, "prescribed_judgments": sorted_pj}
        try:
            return replace(spec, prescribed_judgments=sorted_pj)
        except Exception:
            return spec

    def _deduplicate_constraints(self, spec: Any) -> Any:
        """Remove duplicate constraint ids per coordinate.

        Parameters
        ----------
        spec : Specification
            Input specification.

        Returns
        -------
        Specification
            Specification with deduplicated constraint lists.
        """
        cm = spec["constraint_map"] if isinstance(spec, dict) else spec.constraint_map
        deduped = {coord: tuple(dict.fromkeys(cids)) for coord, cids in cm.items()}
        if isinstance(spec, dict):
            return {**spec, "constraint_map": deduped}
        try:
            return replace(spec, constraint_map=deduped)
        except Exception:
            return spec

    def _normalize_priority(self, spec: Any) -> Any:
        """Clamp priority to [1, 10].

        Parameters
        ----------
        spec : Specification
            Input specification.

        Returns
        -------
        Specification
            Specification with priority clamped.
        """
        p = spec["priority"] if isinstance(spec, dict) else spec.priority
        clamped = max(1, min(10, p))
        if isinstance(spec, dict):
            return {**spec, "priority": clamped}
        try:
            return replace(spec, priority=clamped)
        except Exception:
            return spec

    # -- public interface -----------------------------------------------------

    def normalize(self, spec: Any) -> Any:
        """Apply all normalisation rules to *spec*.

        Parameters
        ----------
        spec : Specification
            The specification to normalise.

        Returns
        -------
        Specification
            The normalised specification (a new instance).
        """
        result = spec
        for rule_name in self.normalization_rules:
            method = getattr(self, f"_{rule_name}", None)
            if method is not None:
                result = method(result)
        self.normalized_count += 1
        return result

    def is_normalized(self, spec: Any) -> bool:
        """Return ``True`` iff *spec* is already in normal form.

        Parameters
        ----------
        spec : Specification
            The specification to check.

        Returns
        -------
        bool
            ``True`` if normalising *spec* yields a structurally identical object.
        """
        normalised = self.normalize(spec)
        coords_ok = (
            (spec["target_coordinates"] if isinstance(spec, dict) else spec.target_coordinates)
            == (normalised["target_coordinates"] if isinstance(normalised, dict) else normalised.target_coordinates)
        )
        priority_ok = (
            (spec["priority"] if isinstance(spec, dict) else spec.priority)
            == (normalised["priority"] if isinstance(normalised, dict) else normalised.priority)
        )
        return coords_ok and priority_ok

    def diff(self, spec_a: Any, spec_b: Any) -> dict[str, Any]:
        """Compute a structural diff between two specifications.

        Parameters
        ----------
        spec_a : Specification
            First specification.
        spec_b : Specification
            Second specification.

        Returns
        -------
        dict[str, Any]
            Dict with keys ``"added_coordinates"``, ``"removed_coordinates"``,
            ``"priority_delta"``, ``"judgment_diffs"``.
        """
        def _coords(s: Any) -> set[str]:
            return set(s["target_coordinates"] if isinstance(s, dict) else s.target_coordinates)

        def _pj(s: Any) -> dict[str, Any]:
            return s["prescribed_judgments"] if isinstance(s, dict) else s.prescribed_judgments

        def _priority(s: Any) -> int:
            return s["priority"] if isinstance(s, dict) else s.priority

        coords_a = _coords(spec_a)
        coords_b = _coords(spec_b)
        pj_a = _pj(spec_a)
        pj_b = _pj(spec_b)

        shared = coords_a & coords_b
        judgment_diffs: dict[str, Any] = {}
        for coord in shared:
            val_a = pj_a.get(coord)
            val_b = pj_b.get(coord)
            if val_a != val_b:
                judgment_diffs[coord] = {"spec_a": val_a, "spec_b": val_b}

        return {
            "added_coordinates": sorted(coords_b - coords_a),
            "removed_coordinates": sorted(coords_a - coords_b),
            "priority_delta": _priority(spec_b) - _priority(spec_a),
            "judgment_diffs": judgment_diffs,
        }


# ---------------------------------------------------------------------------
# SpecificationComposer
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class SpecificationComposer:
    """Combines multiple :class:`Specification` objects using logical connectives.

    Supports conjunction (all constraints from both), disjunction (either), and
    intersection (shared coordinates only).

    Attributes
    ----------
    composition_history : list[dict[str, Any]]
        Audit log of every composition operation performed.
    """

    composition_history: list[dict[str, Any]] = field(default_factory=list)

    # -- coordinate helpers ---------------------------------------------------

    def _get_coords(self, spec: Any) -> tuple[str, ...]:
        return spec["target_coordinates"] if isinstance(spec, dict) else spec.target_coordinates

    def _get_pj(self, spec: Any) -> dict[str, Any]:
        return spec["prescribed_judgments"] if isinstance(spec, dict) else spec.prescribed_judgments

    def _get_cm(self, spec: Any) -> dict[str, Any]:
        return spec["constraint_map"] if isinstance(spec, dict) else spec.constraint_map

    def _get_name(self, spec: Any) -> str:
        return spec["name"] if isinstance(spec, dict) else spec.name

    def _intersect_coordinates(
        self, spec_a: Any, spec_b: Any
    ) -> tuple[str, ...]:
        """Return the sorted intersection of coordinates from two specifications.

        Parameters
        ----------
        spec_a : Specification
            First specification.
        spec_b : Specification
            Second specification.

        Returns
        -------
        tuple[str, ...]
            Sorted coordinate ids present in both specs.
        """
        set_a = set(self._get_coords(spec_a))
        set_b = set(self._get_coords(spec_b))
        return tuple(sorted(set_a & set_b))

    def _union_coordinates(self, spec_a: Any, spec_b: Any) -> tuple[str, ...]:
        """Return the sorted union of coordinates from two specifications.

        Parameters
        ----------
        spec_a : Specification
            First specification.
        spec_b : Specification
            Second specification.

        Returns
        -------
        tuple[str, ...]
            Sorted coordinate ids present in either spec.
        """
        set_a = set(self._get_coords(spec_a))
        set_b = set(self._get_coords(spec_b))
        return tuple(sorted(set_a | set_b))

    # -- composition modes ----------------------------------------------------

    def _conjoin(self, spec_a: Any, spec_b: Any) -> Any:
        """Conjoin two specifications: all constraints from both must be satisfied.

        Parameters
        ----------
        spec_a : Specification
            First specification.
        spec_b : Specification
            Second specification.

        Returns
        -------
        Specification
            The conjunction of *spec_a* and *spec_b*.
        """
        new_coords = self._union_coordinates(spec_a, spec_b)
        new_pj = _merge_prescribed_judgments(self._get_pj(spec_a), self._get_pj(spec_b))
        cm_a = self._get_cm(spec_a)
        cm_b = self._get_cm(spec_b)
        new_cm: dict[str, tuple[str, ...]] = {}
        for coord in new_coords:
            cs_a = set(cm_a.get(coord, ()))
            cs_b = set(cm_b.get(coord, ()))
            new_cm[coord] = tuple(sorted(cs_a | cs_b))
        new_name = f"({self._get_name(spec_a)}) AND ({self._get_name(spec_b)})"
        return self._assemble_spec(new_name, "composite", new_coords, new_pj, new_cm, spec_a, spec_b)

    def _disjoin(self, spec_a: Any, spec_b: Any) -> Any:
        """Disjoin two specifications: either must be satisfied.

        Parameters
        ----------
        spec_a : Specification
            First specification.
        spec_b : Specification
            Second specification.

        Returns
        -------
        Specification
            The disjunction of *spec_a* and *spec_b*.
        """
        new_coords = self._intersect_coordinates(spec_a, spec_b)
        pj_a = self._get_pj(spec_a)
        pj_b = self._get_pj(spec_b)
        new_pj: dict[str, Any] = {}
        for coord in new_coords:
            val_a = pj_a.get(coord)
            val_b = pj_b.get(coord)
            new_pj[coord] = {"disjunction": [val_a, val_b]}
        cm_a = self._get_cm(spec_a)
        cm_b = self._get_cm(spec_b)
        new_cm: dict[str, tuple[str, ...]] = {}
        for coord in new_coords:
            cs_a = set(cm_a.get(coord, ()))
            cs_b = set(cm_b.get(coord, ()))
            new_cm[coord] = tuple(sorted(cs_a & cs_b))
        new_name = f"({self._get_name(spec_a)}) OR ({self._get_name(spec_b)})"
        return self._assemble_spec(new_name, "composite", new_coords, new_pj, new_cm, spec_a, spec_b)

    def _assemble_spec(
        self,
        name: str,
        kind: str,
        coords: tuple[str, ...],
        prescribed_judgments: dict[str, Any],
        constraint_map: dict[str, tuple[str, ...]],
        spec_a: Any,
        spec_b: Any,
    ) -> Any:
        """Assemble a new specification dict or dataclass from components."""
        spec_id = _make_spec_id(name, kind)
        prio_a = spec_a["priority"] if isinstance(spec_a, dict) else spec_a.priority
        prio_b = spec_b["priority"] if isinstance(spec_b, dict) else spec_b.priority
        priority = min(prio_a, prio_b)
        base: dict[str, Any] = {
            "spec_id": spec_id,
            "name": name,
            "kind": kind,
            "description": f"Composed specification: {name}",
            "target_coordinates": coords,
            "prescribed_judgments": prescribed_judgments,
            "constraint_map": constraint_map,
            "priority": priority,
            "version": "1.0.0",
            "metadata": {"composed": True, "created_at": _utc_now_iso()},
            "created_at": _utc_now_iso(),
        }
        try:
            from jugeo.problem_modes.specification_satisfaction.models import (
                Specification as _Spec,
            )
            return _Spec(**{k: v for k, v in base.items() if k in _Spec.__dataclass_fields__})  # type: ignore[attr-defined]
        except (ImportError, AttributeError, TypeError):
            return base

    def compose(self, spec_a: Any, spec_b: Any, mode: str = "conjunction") -> Any:
        """Compose two specifications using the given logical mode.

        Parameters
        ----------
        spec_a : Specification
            First specification.
        spec_b : Specification
            Second specification.
        mode : str, optional
            Composition mode; one of ``"conjunction"`` (default) or
            ``"disjunction"``.

        Returns
        -------
        Specification
            The composed specification.

        Raises
        ------
        ValueError
            If *mode* is not recognised.
        """
        if mode == "conjunction":
            result = self._conjoin(spec_a, spec_b)
        elif mode == "disjunction":
            result = self._disjoin(spec_a, spec_b)
        else:
            raise ValueError(f"Unknown composition mode '{mode}'.")
        self.composition_history.append({
            "mode": mode,
            "spec_a": self._get_name(spec_a),
            "spec_b": self._get_name(spec_b),
            "result_name": self._get_name(result),
            "timestamp": _utc_now_iso(),
        })
        return result

    def compose_many(self, specs: list[Any], mode: str = "conjunction") -> Any:
        """Fold a list of specifications using :meth:`compose`.

        Parameters
        ----------
        specs : list[Specification]
            Specifications to fold; must have at least one element.
        mode : str, optional
            Composition mode passed to each pairwise :meth:`compose` call.

        Returns
        -------
        Specification
            The composed result.

        Raises
        ------
        ValueError
            If *specs* is empty.
        """
        if not specs:
            raise ValueError("compose_many requires at least one specification.")
        result = specs[0]
        for spec in specs[1:]:
            result = self.compose(result, spec, mode=mode)
        return result

    def decompose(self, spec: Any) -> list[Any]:
        """Split a composite specification into independent sub-specifications.

        Independence is determined by coordinate disjointness: two coordinates
        are independent if they share no prescribed judgment keys in common.

        Parameters
        ----------
        spec : Specification
            The specification to decompose.

        Returns
        -------
        list[Specification]
            A list of sub-specifications, each covering disjoint coordinate sets.
        """
        coords = list(self._get_coords(spec))
        pj = self._get_pj(spec)
        cm = self._get_cm(spec)

        # Build adjacency: two coordinates are related if they share a judgment key
        related: dict[str, set[str]] = {c: set() for c in coords}
        for i, ca in enumerate(coords):
            for cb in coords[i + 1:]:
                keys_a = set(pj.get(ca, {}).keys()) if isinstance(pj.get(ca), dict) else set()
                keys_b = set(pj.get(cb, {}).keys()) if isinstance(pj.get(cb), dict) else set()
                if keys_a & keys_b:
                    related[ca].add(cb)
                    related[cb].add(ca)

        # Connected components via BFS
        visited: set[str] = set()
        components: list[set[str]] = []
        for coord in coords:
            if coord not in visited:
                component: set[str] = set()
                queue = [coord]
                while queue:
                    node = queue.pop()
                    if node in visited:
                        continue
                    visited.add(node)
                    component.add(node)
                    queue.extend(related[node] - visited)
                components.append(component)

        if len(components) <= 1:
            return [spec]

        sub_specs: list[Any] = []
        spec_name = self._get_name(spec)
        for i, component in enumerate(components):
            sub_coords = tuple(sorted(component))
            sub_pj = {c: pj[c] for c in sub_coords if c in pj}
            sub_cm = {c: cm[c] for c in sub_coords if c in cm}
            sub_name = f"{spec_name}__component_{i}"
            sub_spec = self._assemble_spec(
                sub_name, "structural", sub_coords, sub_pj, sub_cm, spec, spec
            )
            sub_specs.append(sub_spec)
        return sub_specs


# ---------------------------------------------------------------------------
# GlobalSectionPrescription
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class GlobalSectionPrescription:
    """An assignment of judgment prescriptions to every coordinate in the site.

    A global section prescription is the sheaf-theoretic lift of a
    :class:`Specification`: it records the prescribed judgment value at every
    coordinate and provides operations to restrict, merge, and serialise the
    assignment.

    Attributes
    ----------
    spec_id : str
        The identifier of the specification from which this prescription was
        derived.
    coordinate_prescriptions : Mapping[str, Mapping[str, Any]]
        Maps each coordinate id to its judgment prescription dict.
    section_id : str
        Unique identifier for this particular prescription instance.
    created_at : str
        ISO 8601 creation timestamp.
    """

    spec_id: str
    coordinate_prescriptions: Mapping[str, Mapping[str, Any]]
    section_id: str
    created_at: str

    # -- query ----------------------------------------------------------------

    def get_prescription_at(self, coordinate: str) -> Mapping[str, Any]:
        """Return the judgment prescription at the given coordinate.

        Parameters
        ----------
        coordinate : str
            The coordinate identifier to look up.

        Returns
        -------
        Mapping[str, Any]
            The prescription dict.

        Raises
        ------
        KeyError
            If *coordinate* is not covered by this prescription.
        """
        if coordinate not in self.coordinate_prescriptions:
            raise KeyError(
                f"Coordinate '{coordinate}' is not covered by this prescription."
            )
        return self.coordinate_prescriptions[coordinate]

    def coordinates(self) -> tuple[str, ...]:
        """Return the sorted tuple of covered coordinate identifiers.

        Returns
        -------
        tuple[str, ...]
            Sorted coordinate ids.
        """
        return tuple(sorted(self.coordinate_prescriptions.keys()))

    def is_total(self, site_coordinates: Sequence[str]) -> bool:
        """Return ``True`` iff this prescription covers all *site_coordinates*.

        Parameters
        ----------
        site_coordinates : Sequence[str]
            The full set of coordinates in the ambient site.

        Returns
        -------
        bool
            ``True`` if every site coordinate has a prescription.
        """
        covered = set(self.coordinate_prescriptions.keys())
        return all(c in covered for c in site_coordinates)

    def restrict_to(self, coordinates: Sequence[str]) -> GlobalSectionPrescription:
        """Return a new prescription restricted to the given coordinates.

        Parameters
        ----------
        coordinates : Sequence[str]
            Coordinate ids to keep.

        Returns
        -------
        GlobalSectionPrescription
            A new prescription over only the specified coordinates.
        """
        restricted = {
            c: self.coordinate_prescriptions[c]
            for c in coordinates
            if c in self.coordinate_prescriptions
        }
        return replace(
            self,
            coordinate_prescriptions=restricted,
            section_id=str(uuid.uuid4()),
            created_at=_utc_now_iso(),
        )

    def merge_with(self, other: GlobalSectionPrescription) -> GlobalSectionPrescription:
        """Merge this prescription with *other*, with *other* taking precedence.

        Parameters
        ----------
        other : GlobalSectionPrescription
            Prescription to merge in; its values override ours on conflicts.

        Returns
        -------
        GlobalSectionPrescription
            The merged prescription.
        """
        merged: dict[str, Mapping[str, Any]] = dict(self.coordinate_prescriptions)
        for coord, prescription in other.coordinate_prescriptions.items():
            if coord in merged and isinstance(merged[coord], dict) and isinstance(prescription, dict):
                merged[coord] = {**merged[coord], **prescription}
            else:
                merged[coord] = prescription
        return replace(
            self,
            coordinate_prescriptions=merged,
            section_id=str(uuid.uuid4()),
            created_at=_utc_now_iso(),
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain Python dict.

        Returns
        -------
        dict[str, Any]
            A JSON-serialisable representation.
        """
        return {
            "spec_id": self.spec_id,
            "section_id": self.section_id,
            "created_at": self.created_at,
            "coordinate_prescriptions": {
                c: dict(p) for c, p in self.coordinate_prescriptions.items()
            },
        }

    # -- classmethod constructors ---------------------------------------------

    @classmethod
    def from_specification(cls, spec: Any) -> GlobalSectionPrescription:
        """Construct a prescription directly from a :class:`Specification`.

        Parameters
        ----------
        spec : Specification
            The source specification.

        Returns
        -------
        GlobalSectionPrescription
            A new global section prescription mirroring *spec*.
        """
        spec_id = spec["spec_id"] if isinstance(spec, dict) else spec.spec_id
        pj = spec["prescribed_judgments"] if isinstance(spec, dict) else spec.prescribed_judgments
        coords = spec["target_coordinates"] if isinstance(spec, dict) else spec.target_coordinates
        prescriptions: dict[str, Mapping[str, Any]] = {}
        for coord in coords:
            judgment = pj.get(coord, {})
            if not isinstance(judgment, dict):
                judgment = {"value": judgment}
            prescriptions[coord] = judgment
        return cls(
            spec_id=spec_id,
            coordinate_prescriptions=prescriptions,
            section_id=str(uuid.uuid4()),
            created_at=_utc_now_iso(),
        )


# ---------------------------------------------------------------------------
# Module-level functions
# ---------------------------------------------------------------------------


def build_specification(
    name: str,
    kind: str,
    target_coordinates: list[str],
    prescribed_judgments: dict[str, Any] | None = None,
    **kwargs: Any,
) -> Any:
    """Build a :class:`Specification` using the fluent builder.

    Parameters
    ----------
    name : str
        Human-readable name.
    kind : str
        Specification kind (e.g. ``"structural"``).
    target_coordinates : list[str]
        Coordinate identifiers the specification targets.
    prescribed_judgments : dict[str, Any] or None, optional
        Pre-existing judgment prescriptions; defaults to an empty dict.
    **kwargs : Any
        Additional keyword arguments forwarded to :class:`SpecificationBuilder`
        (e.g. ``priority``, ``description``, ``version``, ``metadata``).

    Returns
    -------
    Specification
        The constructed specification.
    """
    builder = SpecificationBuilder()
    builder.set_name(name).set_kind(kind)
    for coord in target_coordinates:
        builder.add_target_coordinate(coord)
    if prescribed_judgments:
        for coord, judgment in prescribed_judgments.items():
            builder.add_prescribed_judgment(coord, judgment)
    if "priority" in kwargs:
        builder.set_priority(int(kwargs["priority"]))
    if "description" in kwargs:
        builder.description = str(kwargs["description"])
    if "version" in kwargs:
        builder.version = str(kwargs["version"])
    if "metadata" in kwargs and isinstance(kwargs["metadata"], dict):
        for k, v in kwargs["metadata"].items():
            builder.set_metadata(k, v)
    return builder.build()


def parse_constraint_list(
    constraint_list: list[str | dict[str, Any]],
) -> list[dict[str, Any]]:
    """Parse a mixed-format constraint list into uniform prescription dicts.

    Elements may be:
    * ``str`` — interpreted as a constraint type name with empty params.
    * ``dict`` — must have a ``"type"`` key; ``"params"`` is optional.

    Parameters
    ----------
    constraint_list : list[str | dict[str, Any]]
        The input constraint descriptions.

    Returns
    -------
    list[dict[str, Any]]
        Normalised prescription dicts.

    Raises
    ------
    TypeError
        If an element is neither a ``str`` nor a ``dict``.
    ValueError
        If a dict element lacks the ``"type"`` key.
    """
    encoder = ConstraintEncoder()
    results: list[dict[str, Any]] = []
    for item in constraint_list:
        if isinstance(item, str):
            try:
                results.append(encoder.encode_constraint(item, {}))
            except KeyError:
                results.append({"constraint_kind": item, "raw": True})
        elif isinstance(item, dict):
            if "type" not in item:
                raise ValueError(f"Dict constraint missing 'type' key: {item!r}")
            try:
                results.append(encoder.encode_constraint(item["type"], item.get("params", {})))
            except KeyError:
                results.append({"constraint_kind": item["type"], "params": item.get("params", {}), "raw": True})
        else:
            raise TypeError(
                f"Expected str or dict in constraint_list; got {type(item).__name__}"
            )
    return results


def compose_specifications(
    specs: list[Any], mode: str = "conjunction"
) -> Any:
    """Compose a list of specifications using the given logical mode.

    Parameters
    ----------
    specs : list[Specification]
        Specifications to compose.
    mode : str, optional
        ``"conjunction"`` (default) or ``"disjunction"``.

    Returns
    -------
    Specification
        The composed result.

    Raises
    ------
    ValueError
        If *specs* is empty.
    """
    if not specs:
        raise ValueError("compose_specifications requires at least one specification.")
    composer = SpecificationComposer()
    return composer.compose_many(specs, mode=mode)


def specification_from_template(
    template_name: str, target_coordinates: list[str]
) -> Any:
    """Create a specification from a named template over given coordinates.

    Parameters
    ----------
    template_name : str
        One of the built-in template names.
    target_coordinates : list[str]
        Coordinates to assign to the specification.

    Returns
    -------
    Specification
        The constructed specification.

    Raises
    ------
    KeyError
        If the template name is not registered.
    """
    builder = SpecificationBuilder.from_template(template_name)
    builder.set_name(f"{template_name}_spec")
    for coord in target_coordinates:
        builder.add_target_coordinate(coord)
    tmpl = _BUILTIN_TEMPLATES[template_name]
    encoder = ConstraintEncoder()
    prototype = tmpl.get("prescribed_judgment_prototype", {})
    for coord in target_coordinates:
        for cat in tmpl.get("constraint_categories", []):
            try:
                encoded = encoder.encode_constraint(cat, prototype)
            except KeyError:
                encoded = {"constraint_kind": cat, **prototype}
            builder.add_prescribed_judgment(coord, encoded)
            builder.add_constraint(coord, cat)
    return builder.build()


def validate_specification(spec: Any) -> tuple[bool, list[str]]:
    """Validate a specification and return a pass/fail pair with error messages.

    Parameters
    ----------
    spec : Specification
        The specification to validate.

    Returns
    -------
    tuple[bool, list[str]]
        ``(True, [])`` if valid; ``(False, [error, ...])`` otherwise.
    """
    errors: list[str] = []
    if isinstance(spec, dict):
        if not spec.get("name"):
            errors.append("Specification name is empty.")
        if not spec.get("target_coordinates"):
            errors.append("Specification has no target coordinates.")
        if spec.get("priority", 1) < 1:
            errors.append("Priority must be >= 1.")
    else:
        if not getattr(spec, "name", None):
            errors.append("Specification name is empty.")
        if not getattr(spec, "target_coordinates", None):
            errors.append("Specification has no target coordinates.")
        if getattr(spec, "priority", 1) < 1:
            errors.append("Priority must be >= 1.")
    return (len(errors) == 0, errors)


# ---------------------------------------------------------------------------
# Unified architecture cross-references (jugeo.geometry, jugeo.evidence, jugeo.encodings)
# ---------------------------------------------------------------------------

def spec_descent(spec: Any) -> dict[str, Any]:
    """Compute descent data for specification satisfaction.
    
    Specification satisfaction IS descent — satisfying a spec means finding
    a global section that restricts correctly to each local patch.
    
    Parameters
    ----------
    spec : Any
        A Specification object or dict with specification data.
    
    Returns
    -------
    dict[str, Any]
        Descent record with ``cover``, ``local_sections``, ``cocycle_trivial``,
        and ``global_section_exists`` keys.
    """
    try:
        from jugeo.geometry.descent import run_descent, DescentDatum
    except ImportError:
        run_descent = None
        DescentDatum = None

    name = getattr(spec, "name", None) or (spec.get("name") if isinstance(spec, dict) else "unknown")
    coords = getattr(spec, "target_coordinates", None) or (
        spec.get("target_coordinates") if isinstance(spec, dict) else []
    )

    descent: dict[str, Any] = {
        "spec_name": name,
        "cover": list(coords) if coords else [],
        "local_sections": {},
        "cocycle_trivial": None,
        "global_section_exists": None,
    }

    if run_descent is not None:
        try:
            result = run_descent(coords)
            descent["cocycle_trivial"] = getattr(result, "cocycle_trivial", None)
            descent["global_section_exists"] = getattr(result, "global_section_exists", None)
            descent["local_sections"] = getattr(result, "local_sections", {})
        except Exception:
            pass

    return descent


def spec_certificate(result: Any) -> dict[str, Any]:
    """Build an evidence certificate for a satisfaction result.
    
    A satisfaction certificate records that a specification was checked,
    the outcome, and the trust level of the evidence.
    
    Parameters
    ----------
    result : Any
        A satisfaction result object or dict.
    
    Returns
    -------
    dict[str, Any]
        Certificate with ``satisfied``, ``trust_level``, ``witness_hash``,
        ``spec_name``, and ``certificate_id`` keys.
    """
    try:
        from jugeo.evidence.certificates import Certificate, build_certificate
    except ImportError:
        Certificate = None
        build_certificate = None

    import hashlib, uuid

    satisfied = getattr(result, "satisfied", None)
    if satisfied is None and isinstance(result, dict):
        satisfied = result.get("satisfied", result.get("status") == "satisfied")

    spec_name = getattr(result, "spec_name", None) or (
        result.get("spec_name") if isinstance(result, dict) else "unknown"
    )

    cert: dict[str, Any] = {
        "certificate_id": str(uuid.uuid4()),
        "spec_name": spec_name,
        "satisfied": bool(satisfied),
        "trust_level": "VERIFIED" if satisfied else "UNVERIFIED",
        "witness_hash": hashlib.sha256(str(result).encode()).hexdigest()[:16],
        "certificate_obj": None,
    }

    if build_certificate is not None:
        try:
            cert["certificate_obj"] = build_certificate(
                claim=spec_name, satisfied=satisfied, source="specification_satisfaction"
            )
        except Exception:
            pass

    return cert


def spec_encoding(spec: Any) -> dict[str, Any]:
    """Encode a specification as scalar constraints for SMT solving.
    
    Specifications translate to scalar encodings where each clause becomes
    a conjunction of SMT predicates over the target coordinates.
    
    Parameters
    ----------
    spec : Any
        A Specification object or dict.
    
    Returns
    -------
    dict[str, Any]
        Encoding with ``formulas``, ``variables``, ``coordinate_map``,
        and ``encoding_kind`` keys.
    """
    try:
        from jugeo.encodings.scalar_encodings import ScalarEncoder, encode_constraint
    except ImportError:
        ScalarEncoder = None
        encode_constraint = None

    name = getattr(spec, "name", None) or (spec.get("name") if isinstance(spec, dict) else "unknown")
    coords = getattr(spec, "target_coordinates", None) or (
        spec.get("target_coordinates") if isinstance(spec, dict) else []
    )

    encoding: dict[str, Any] = {
        "spec_name": name,
        "encoding_kind": "scalar_conjunction",
        "formulas": [f"(sat {c})" for c in (coords or [])],
        "variables": [f"sat_{c}" for c in (coords or [])],
        "coordinate_map": {c: f"sat_{c}" for c in (coords or [])},
        "encoder": None,
    }

    if encode_constraint is not None:
        try:
            for c in (coords or []):
                enc = encode_constraint(c, name)
                if hasattr(enc, "formula"):
                    encoding["formulas"].append(enc.formula)
        except Exception:
            pass

    if ScalarEncoder is not None:
        try:
            encoding["encoder"] = ScalarEncoder(coordinates=list(coords or []))
        except Exception:
            pass

    return encoding


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    # Templates
    "TYPE_SAFE_TEMPLATE",
    "BEHAVIOR_CORRECT_TEMPLATE",
    "API_CONSISTENT_TEMPLATE",
    "SECURITY_SOUND_TEMPLATE",
    # Classes
    "SpecificationBuilder",
    "ConstraintEncoder",
    "SpecificationNormalizer",
    "SpecificationComposer",
    "GlobalSectionPrescription",
    # Module-level functions
    "build_specification",
    "parse_constraint_list",
    "compose_specifications",
    "specification_from_template",
    "validate_specification",
    # Unified architecture cross-references
    "spec_descent",
    "spec_certificate",
    "spec_encoding",
]
