"""Semantic control manifest — registry and catalog for JuGeo orchestration (theory2.tex Ch44).

This module implements the *manifest layer* of the semantic control subsystem.
The manifest layer sits above the core data model (``models.py``) and provides
three complementary services:

1. **SemanticControlManifest** — a version-stamped catalogue of recognised move
   types and control law types together with the semantic invariants they must
   preserve (Ch44 §44.9).

2. **MoveRegistry** — a run-time registry mapping *move type identifiers* to
   specification dicts from which AdmissibleMove instances can be fabricated on
   demand.  Supports merge, validation, and serialisation.

3. **ControlLawCatalog** — analogous registry for ControlLaw specifications.
   Supports a configurable default law that is used by the orchestrator when no
   explicit law is specified for a control episode.

Module-level factory functions (``build_manifest``, ``build_default_registry``,
``build_default_catalog``) are provided so that a fully-populated subsystem can
be instantiated with a single call during orchestrator initialisation.

References
----------
- theory2.tex Ch44 §44.9 – Manifest and Invariant Calculus
- jugeo.orchestration.semantic_control.models – AdmissibleMove, ControlLaw
"""

from __future__ import annotations

import copy
import enum
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Guarded imports from the sibling models module
# ---------------------------------------------------------------------------

try:
    from jugeo.orchestration.semantic_control.models import (
        AdmissibleMove,
        ControlLaw,
        ControlLawKind,
        MoveKind,
        MODELS_VERSION,
    )
    _MODELS_AVAILABLE = True
except Exception:  # pragma: no cover
    _MODELS_AVAILABLE = False

    class MoveKind(enum.Enum):  # type: ignore[no-redef]
        VERIFY = "verify"
        CONSTRUCT = "construct"
        REPAIR = "repair"
        NEGOTIATE_TREATY = "negotiate_treaty"
        REFINE_COVER = "refine_cover"
        DISCHARGE_OBLIGATION = "discharge_obligation"
        CONSULT_ORACLE = "consult_oracle"
        EXTEND_COVER = "extend_cover"
        LIFT_SECTION = "lift_section"
        BIND_TREATY = "bind_treaty"
        OPEN_CHANNEL = "open_channel"
        CLOSE_CHANNEL = "close_channel"
        PROMOTE_CONTEXT = "promote_context"
        DEMOTE_CONTEXT = "demote_context"
        ASSERT_INVARIANT = "assert_invariant"
        RETRACT_INVARIANT = "retract_invariant"
        CHECKPOINT = "checkpoint"
        ROLLBACK = "rollback"

    class ControlLawKind(enum.Enum):  # type: ignore[no-redef]
        GREEDY = "greedy"
        LOOKAHEAD = "lookahead"
        BALANCED = "balanced"
        ADAPTIVE = "adaptive"
        CUSTOM = "custom"

    class AdmissibleMove:  # type: ignore[no-redef]
        def __init__(self, **kwargs: Any) -> None:
            for k, v in kwargs.items():
                setattr(self, k, v)

    class ControlLaw:  # type: ignore[no-redef]
        def __init__(self, **kwargs: Any) -> None:
            for k, v in kwargs.items():
                setattr(self, k, v)

    MODELS_VERSION = "1.0.0"  # type: ignore[assignment]

# ---------------------------------------------------------------------------
# Module-level constants  (theory2.tex Ch44 §44.9)
# ---------------------------------------------------------------------------

#: Current manifest schema version.
MANIFEST_VERSION: str = "1.0.0"

#: Default chapter reference tag embedded in every manifest.
DEFAULT_CHAPTER_REF: str = "Ch44"

#: Minimum number of move types a valid manifest must declare.
MANIFEST_MIN_MOVE_TYPES: int = 4

#: Minimum number of law types a valid manifest must declare.
MANIFEST_MIN_LAW_TYPES: int = 2

#: Required keys in a move type specification dict.
MOVE_SPEC_REQUIRED_KEYS: frozenset[str] = frozenset(
    {"move_type_id", "kind", "description", "default_cost", "default_priority"}
)

#: Required keys in a law type specification dict.
LAW_SPEC_REQUIRED_KEYS: frozenset[str] = frozenset(
    {"law_type_id", "kind", "description", "default_parameters"}
)

#: Well-known move type IDs registered in every default manifest.
STANDARD_MOVE_TYPE_IDS: tuple[str, ...] = (
    "extend_cover.default",
    "lift_section.default",
    "bind_treaty.default",
    "discharge_obligation.default",
    "open_channel.default",
    "close_channel.default",
    "promote_context.default",
    "demote_context.default",
    "assert_invariant.default",
    "retract_invariant.default",
    "checkpoint.default",
    "rollback.default",
)

#: Well-known law type IDs registered in every default manifest.
STANDARD_LAW_TYPE_IDS: tuple[str, ...] = (
    "greedy.default",
    "lookahead.default",
    "balanced.default",
    "adaptive.default",
)

__all__ = [
    "SemanticControlManifest",
    "MoveRegistry",
    "ControlLawCatalog",
    "build_manifest",
    "validate_manifest",
    "build_default_registry",
    "build_default_catalog",
    "_default_move_types",
    "_default_law_types",
    "_default_invariants",
    "MANIFEST_VERSION",
    "STANDARD_MOVE_TYPE_IDS",
    "STANDARD_LAW_TYPE_IDS",
]


# ===========================================================================
# SemanticControlManifest  (theory2.tex Ch44 §44.9.1)
# ===========================================================================


@dataclass(slots=True)
class SemanticControlManifest:
    """Version-stamped catalogue of recognised move types, law types and invariants.

    The manifest is the authoritative source of truth for what the semantic
    control subsystem may do.  Every move type the orchestrator can produce must
    appear in the manifest; unregistered move types are rejected by the
    admissibility checker.

    Parameters
    ----------
    manifest_id:
        Globally-unique identifier for this manifest instance.
    version:
        Semantic version string (``MANIFEST_VERSION`` by default).
    chapter_ref:
        Reference tag linking this manifest to the relevant theory chapter.
    created_at:
        POSIX creation timestamp.
    updated_at:
        POSIX last-update timestamp.
    description:
        Human-readable summary of this manifest's scope and purpose.
    move_types:
        List of move type specification dicts.  Each dict must contain at
        least the keys in ``MOVE_SPEC_REQUIRED_KEYS``.
    law_types:
        List of law type specification dicts.  Each dict must contain at
        least the keys in ``LAW_SPEC_REQUIRED_KEYS``.
    invariants:
        List of semantic invariant description strings that every admissible
        trajectory must preserve (Ch44 §44.9 Def 44.12).
    metadata:
        Arbitrary annotations.
    """

    manifest_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    version: str = MANIFEST_VERSION
    chapter_ref: str = DEFAULT_CHAPTER_REF
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    description: str = "Default JuGeo semantic control manifest"
    move_types: list[dict[str, Any]] = field(default_factory=list)
    law_types: list[dict[str, Any]] = field(default_factory=list)
    invariants: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # Mutation helpers
    # ------------------------------------------------------------------

    def add_move_type(self, spec: dict[str, Any]) -> None:
        """Append a move type specification to this manifest.

        Parameters
        ----------
        spec:
            Move type spec dict; must include all keys in
            ``MOVE_SPEC_REQUIRED_KEYS``.

        Raises
        ------
        ValueError
            If *spec* is missing required keys or the move_type_id is already
            registered.
        """
        missing = MOVE_SPEC_REQUIRED_KEYS - spec.keys()
        if missing:
            raise ValueError(
                f"Move type spec missing required keys: {sorted(missing)}"
            )
        existing_ids = {mt["move_type_id"] for mt in self.move_types}
        if spec["move_type_id"] in existing_ids:
            raise ValueError(
                f"Move type '{spec['move_type_id']}' is already registered in manifest"
            )
        self.move_types.append(dict(spec))
        self.updated_at = time.time()
        logger.debug("Manifest %s: registered move type '%s'", self.manifest_id, spec["move_type_id"])

    def add_law_type(self, spec: dict[str, Any]) -> None:
        """Append a law type specification to this manifest.

        Parameters
        ----------
        spec:
            Law type spec dict; must include all keys in
            ``LAW_SPEC_REQUIRED_KEYS``.

        Raises
        ------
        ValueError
            If *spec* is missing required keys or the law_type_id is already
            registered.
        """
        missing = LAW_SPEC_REQUIRED_KEYS - spec.keys()
        if missing:
            raise ValueError(
                f"Law type spec missing required keys: {sorted(missing)}"
            )
        existing_ids = {lt["law_type_id"] for lt in self.law_types}
        if spec["law_type_id"] in existing_ids:
            raise ValueError(
                f"Law type '{spec['law_type_id']}' is already registered in manifest"
            )
        self.law_types.append(dict(spec))
        self.updated_at = time.time()
        logger.debug("Manifest %s: registered law type '%s'", self.manifest_id, spec["law_type_id"])

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def validate(self) -> list[str]:
        """Return a list of validation error messages.

        An empty list means the manifest is structurally sound.

        Checks include:
        - Minimum move type count.
        - Minimum law type count.
        - Required keys in every move type spec.
        - Required keys in every law type spec.
        - Non-empty invariants list.
        - Non-empty description.

        Returns
        -------
        list[str]
            Error messages; empty when the manifest is valid.
        """
        errors: list[str] = []

        if len(self.move_types) < MANIFEST_MIN_MOVE_TYPES:
            errors.append(
                f"Manifest must declare at least {MANIFEST_MIN_MOVE_TYPES} move types; "
                f"found {len(self.move_types)}"
            )
        if len(self.law_types) < MANIFEST_MIN_LAW_TYPES:
            errors.append(
                f"Manifest must declare at least {MANIFEST_MIN_LAW_TYPES} law types; "
                f"found {len(self.law_types)}"
            )
        for i, spec in enumerate(self.move_types):
            missing = MOVE_SPEC_REQUIRED_KEYS - spec.keys()
            if missing:
                errors.append(
                    f"move_types[{i}] missing required keys: {sorted(missing)}"
                )
        for i, spec in enumerate(self.law_types):
            missing = LAW_SPEC_REQUIRED_KEYS - spec.keys()
            if missing:
                errors.append(
                    f"law_types[{i}] missing required keys: {sorted(missing)}"
                )
        if not self.invariants:
            errors.append("Manifest must declare at least one semantic invariant")
        if not self.description.strip():
            errors.append("Manifest description must be non-empty")
        # Check for duplicate move type IDs.
        seen_move_ids: set[str] = set()
        for spec in self.move_types:
            mid = spec.get("move_type_id", "")
            if mid in seen_move_ids:
                errors.append(f"Duplicate move_type_id: '{mid}'")
            seen_move_ids.add(mid)
        # Check for duplicate law type IDs.
        seen_law_ids: set[str] = set()
        for spec in self.law_types:
            lid = spec.get("law_type_id", "")
            if lid in seen_law_ids:
                errors.append(f"Duplicate law_type_id: '{lid}'")
            seen_law_ids.add(lid)

        return errors

    def is_valid(self) -> bool:
        """Return True when ``validate()`` produces no errors.

        Returns
        -------
        bool
        """
        return not self.validate()

    # ------------------------------------------------------------------
    # Serialisation / display
    # ------------------------------------------------------------------

    def summary(self) -> str:
        """Return a concise multi-line summary string.

        Returns
        -------
        str
        """
        lines = [
            f"SemanticControlManifest v{self.version}",
            f"  id          : {self.manifest_id}",
            f"  chapter_ref : {self.chapter_ref}",
            f"  description : {self.description}",
            f"  move_types  : {len(self.move_types)}",
            f"  law_types   : {len(self.law_types)}",
            f"  invariants  : {len(self.invariants)}",
            f"  valid       : {self.is_valid()}",
        ]
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dictionary.

        Returns
        -------
        dict
        """
        return {
            "manifest_id": self.manifest_id,
            "version": self.version,
            "chapter_ref": self.chapter_ref,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "description": self.description,
            "move_types": [dict(mt) for mt in self.move_types],
            "law_types": [dict(lt) for lt in self.law_types],
            "invariants": list(self.invariants),
            "metadata": dict(self.metadata),
            "is_valid": self.is_valid(),
            "_models_version": MODELS_VERSION,
        }

    def __repr__(self) -> str:  # noqa: D105
        return (
            f"SemanticControlManifest("
            f"v={self.version}, "
            f"moves={len(self.move_types)}, "
            f"laws={len(self.law_types)}, "
            f"valid={self.is_valid()})"
        )


# ===========================================================================
# MoveRegistry  (theory2.tex Ch44 §44.9.2)
# ===========================================================================


@dataclass(slots=True)
class MoveRegistry:
    """Run-time registry mapping move type IDs to specification dicts.

    The registry provides a factory interface: callers can ``register`` a
    specification and later ``instantiate`` fully-typed AdmissibleMove objects
    from it.

    Specifications follow the same schema as the manifest's move_types entries;
    see ``MOVE_SPEC_REQUIRED_KEYS`` for the mandatory fields.

    Parameters
    ----------
    entries:
        Mapping from move_type_id to specification dict.
    version:
        Registry format version; defaults to ``MANIFEST_VERSION``.
    """

    entries: dict[str, dict[str, Any]] = field(default_factory=dict)
    version: str = MANIFEST_VERSION

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register(self, move_type_id: str, spec: dict[str, Any]) -> None:
        """Register a move type specification under *move_type_id*.

        If an entry with the same ID already exists it is silently overwritten.

        Parameters
        ----------
        move_type_id:
            The canonical identifier for this move type.
        spec:
            Specification dict.  Will have ``move_type_id`` injected if absent.

        Raises
        ------
        ValueError
            If *spec* is missing required keys.
        """
        errors = self.validate_spec(spec)
        if errors:
            raise ValueError(
                f"Invalid move spec for '{move_type_id}': {errors}"
            )
        stored = dict(spec)
        stored["move_type_id"] = move_type_id
        self.entries[move_type_id] = stored
        logger.debug("MoveRegistry: registered '%s'", move_type_id)

    # ------------------------------------------------------------------
    # Lookup & instantiation
    # ------------------------------------------------------------------

    def lookup(self, move_type_id: str) -> dict[str, Any] | None:
        """Return the specification dict for *move_type_id*, or None if absent.

        Parameters
        ----------
        move_type_id:
            The type ID to look up.

        Returns
        -------
        dict | None
        """
        return copy.deepcopy(self.entries.get(move_type_id))

    def instantiate(self, move_type_id: str, **kwargs: Any) -> AdmissibleMove:
        """Create an AdmissibleMove from a registered specification.

        Keyword arguments override the specification defaults, allowing callers
        to customise preconditions, postconditions, cost, etc. per-instance.

        Parameters
        ----------
        move_type_id:
            The registered type ID to instantiate.
        **kwargs:
            Field overrides passed directly to AdmissibleMove.

        Returns
        -------
        AdmissibleMove

        Raises
        ------
        KeyError
            If *move_type_id* is not registered.
        """
        spec = self.entries.get(move_type_id)
        if spec is None:
            raise KeyError(
                f"Move type '{move_type_id}' is not registered in MoveRegistry"
            )

        # Resolve the MoveKind from the spec's "kind" string.
        kind_value = spec.get("kind", next(iter(MoveKind)).value if hasattr(next(iter(MoveKind)), "value") else "refine_cover")
        kind: Any = next(iter(MoveKind))
        for member in MoveKind:
            if member.value == kind_value or member.name == kind_value:
                kind = member
                break

        move_kwargs: dict[str, Any] = {
            "move_id": str(uuid.uuid4()),
            "kind": kind,
            "preconditions": list(spec.get("default_preconditions", [])),
            "postconditions": list(spec.get("default_postconditions", [])),
            "cost": float(spec.get("default_cost", 1.0)),
            "priority": float(spec.get("default_priority", 0.5)),
            "expected_gain": float(spec.get("default_expected_gain", 0.0)),
            "trust_requirement": str(spec.get("default_trust_requirement", "PROVISIONAL")),
            "metadata": {
                "move_type_id": move_type_id,
                "registry_version": self.version,
                **spec.get("default_metadata", {}),
            },
        }
        # Apply caller overrides.
        move_kwargs.update(kwargs)

        if _MODELS_AVAILABLE:
            return AdmissibleMove(**move_kwargs)
        else:
            obj = AdmissibleMove.__new__(AdmissibleMove)
            for k, v in move_kwargs.items():
                object.__setattr__(obj, k, v)
            return obj

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    def list_types(self) -> list[str]:
        """Return the sorted list of registered move type IDs.

        Returns
        -------
        list[str]
        """
        return sorted(self.entries)

    def validate_spec(self, spec: dict[str, Any]) -> list[str]:
        """Return validation errors for a move type specification dict.

        Parameters
        ----------
        spec:
            The specification to validate.

        Returns
        -------
        list[str]
            Error messages; empty list means the spec is valid.
        """
        errors: list[str] = []
        for key in ("kind", "description", "default_cost", "default_priority"):
            if key not in spec:
                errors.append(f"Move spec missing required key: '{key}'")
        cost = spec.get("default_cost")
        if cost is not None and not isinstance(cost, (int, float)):
            errors.append(f"default_cost must be numeric, got {type(cost).__name__}")
        priority = spec.get("default_priority")
        if priority is not None and not isinstance(priority, (int, float)):
            errors.append(f"default_priority must be numeric, got {type(priority).__name__}")
        elif priority is not None and not (0.0 <= float(priority) <= 1.0):
            errors.append(f"default_priority must be in [0.0, 1.0], got {priority}")
        return errors

    # ------------------------------------------------------------------
    # Merge
    # ------------------------------------------------------------------

    def merge(self, other: MoveRegistry) -> MoveRegistry:
        """Return a new MoveRegistry combining entries from self and *other*.

        Entries in *other* overwrite entries in ``self`` when IDs collide.

        Parameters
        ----------
        other:
            The registry to merge in.

        Returns
        -------
        MoveRegistry
            A new registry; neither ``self`` nor *other* is mutated.
        """
        merged_entries: dict[str, dict[str, Any]] = {}
        merged_entries.update(copy.deepcopy(self.entries))
        merged_entries.update(copy.deepcopy(other.entries))
        return MoveRegistry(entries=merged_entries, version=self.version)

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dictionary.

        Returns
        -------
        dict
        """
        return {
            "version": self.version,
            "entries": {k: dict(v) for k, v in self.entries.items()},
            "count": len(self.entries),
        }

    def __repr__(self) -> str:  # noqa: D105
        return f"MoveRegistry(count={len(self.entries)}, version={self.version!r})"


# ===========================================================================
# ControlLawCatalog  (theory2.tex Ch44 §44.9.3)
# ===========================================================================


@dataclass(slots=True)
class ControlLawCatalog:
    """Registry of ControlLaw specifications with an optional default.

    The catalog stores serialisable specification dicts and can instantiate
    ControlLaw objects on demand.  A default law can be designated for use by
    the orchestrator when no explicit law is requested.

    Parameters
    ----------
    entries:
        Mapping from law_id to law specification dict.
    default_law_id:
        The law_id of the default law, or None if no default is set.
    """

    catalog_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    entries: dict[str, dict[str, Any]] = field(default_factory=dict)
    default_law_id: str | None = None

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register(self, law_id: str, spec: dict[str, Any]) -> None:
        """Register a control law specification under *law_id*.

        Existing entries with the same ID are overwritten.

        Parameters
        ----------
        law_id:
            The canonical identifier for this law.
        spec:
            Specification dict.  Must include keys: ``kind``, ``description``,
            ``default_parameters``.

        Raises
        ------
        ValueError
            If *spec* is missing required keys.
        """
        missing = set((LAW_SPEC_REQUIRED_KEYS - {"law_type_id"}) - set(spec.keys()))
        # law_type_id is optional inside the spec since we carry law_id externally
        for key in ("kind", "description", "default_parameters"):
            if key not in spec:
                missing.add(key)
        if missing:
            raise ValueError(
                f"Law spec for '{law_id}' missing required keys: {sorted(missing)}"
            )
        stored = dict(spec)
        stored["law_type_id"] = law_id
        self.entries[law_id] = stored
        logger.debug("ControlLawCatalog: registered '%s'", law_id)

    # ------------------------------------------------------------------
    # Lookup & instantiation
    # ------------------------------------------------------------------

    def get(self, law_id: str) -> dict[str, Any] | None:
        """Return the specification dict for *law_id*, or None if absent.

        Parameters
        ----------
        law_id:
            The law ID to look up.

        Returns
        -------
        dict | None
        """
        spec = self.entries.get(law_id)
        return copy.deepcopy(spec) if spec is not None else None

    def instantiate(self, law_id: str) -> ControlLaw:
        """Create a ControlLaw from a registered specification.

        Parameters
        ----------
        law_id:
            The registered law ID to instantiate.

        Returns
        -------
        ControlLaw

        Raises
        ------
        KeyError
            If *law_id* is not registered.
        """
        spec = self.entries.get(law_id)
        if spec is None:
            raise KeyError(
                f"Law '{law_id}' is not registered in ControlLawCatalog"
            )

        # Resolve ControlLawKind from the "kind" string.
        kind_value = spec.get("kind", ControlLawKind.GREEDY.value)
        kind: Any = ControlLawKind.GREEDY
        for member in ControlLawKind:
            if member.value == kind_value or member.name == kind_value:
                kind = member
                break

        law_kwargs: dict[str, Any] = {
            "law_id": str(uuid.uuid4()),
            "name": spec.get("name", law_id),
            "kind": kind,
            "parameters": copy.deepcopy(spec.get("default_parameters", {})),
        }

        if _MODELS_AVAILABLE:
            return ControlLaw(**law_kwargs)
        else:
            obj = ControlLaw.__new__(ControlLaw)
            for k, v in law_kwargs.items():
                object.__setattr__(obj, k, v)
            return obj

    # ------------------------------------------------------------------
    # Default management
    # ------------------------------------------------------------------

    def set_default(self, law_id: str) -> None:
        """Designate *law_id* as the default control law.

        Parameters
        ----------
        law_id:
            Must already be registered.

        Raises
        ------
        KeyError
            If *law_id* is not registered.
        """
        if law_id not in self.entries:
            raise KeyError(
                f"Cannot set default: law '{law_id}' is not registered"
            )
        self.default_law_id = law_id
        logger.debug("ControlLawCatalog: default law set to '%s'", law_id)

    def get_default(self) -> ControlLaw | None:
        """Return a ControlLaw instance from the default specification.

        Returns
        -------
        ControlLaw | None
            None when no default has been set.
        """
        if self.default_law_id is None:
            return None
        return self.instantiate(self.default_law_id)

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    def list_laws(self) -> list[str]:
        """Return the sorted list of registered law IDs.

        Returns
        -------
        list[str]
        """
        return sorted(self.entries)

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dictionary.

        Returns
        -------
        dict
        """
        return {
            "catalog_id": self.catalog_id,
            "laws": {k: dict(v) for k, v in self.entries.items()},
            "default": self.default_law_id,
            "entries": {k: dict(v) for k, v in self.entries.items()},
            "default_law_id": self.default_law_id,
            "count": len(self.entries),
        }

    def __repr__(self) -> str:  # noqa: D105
        return (
            f"ControlLawCatalog("
            f"count={len(self.entries)}, "
            f"default={self.default_law_id!r})"
        )


# ===========================================================================
# Internal default data builders  (theory2.tex Ch44 §44.9.4)
# ===========================================================================


def _default_move_types() -> list[dict[str, Any]]:
    """Return the list of standard move type specifications.

    Each dict satisfies ``MOVE_SPEC_REQUIRED_KEYS`` and can be passed directly
    to ``SemanticControlManifest.add_move_type`` or ``MoveRegistry.register``.

    Returns
    -------
    list[dict]
    """
    return [
        {
            "move_type_id": "verify.default",
            "kind": "verify",
            "description": (
                "Verify existing local sections against their semantic contract.  "
                "Corresponds to the controller VERIFY move."
            ),
            "default_cost": 1.0,
            "default_priority": 0.6,
            "default_expected_gain": 0.05,
            "default_preconditions": [],
            "default_postconditions": [],
            "default_trust_requirement": "PROVISIONAL",
            "default_metadata": {"chapter_ref": "controller"},
        },
        {
            "move_type_id": "construct.default",
            "kind": "construct",
            "description": (
                "Construct a new semantic section to improve cover realisation.  "
                "Corresponds to the controller CONSTRUCT move."
            ),
            "default_cost": 2.0,
            "default_priority": 0.8,
            "default_expected_gain": 0.18,
            "default_preconditions": [],
            "default_postconditions": [],
            "default_trust_requirement": "PROVISIONAL",
            "default_metadata": {"chapter_ref": "controller"},
        },
        {
            "move_type_id": "repair.default",
            "kind": "repair",
            "description": (
                "Repair an inconsistent semantic site or discharge a broken local "
                "condition.  Corresponds to the controller REPAIR move."
            ),
            "default_cost": 2.5,
            "default_priority": 0.7,
            "default_expected_gain": 0.12,
            "default_preconditions": [],
            "default_postconditions": [],
            "default_trust_requirement": "TRUSTED",
            "default_metadata": {"chapter_ref": "controller"},
        },
        {
            "move_type_id": "negotiate_treaty.default",
            "kind": "negotiate_treaty",
            "description": (
                "Negotiate or refresh treaty compatibility across neighbouring "
                "covers.  Corresponds to the controller NEGOTIATE_TREATY move."
            ),
            "default_cost": 2.0,
            "default_priority": 0.6,
            "default_expected_gain": 0.10,
            "default_preconditions": [],
            "default_postconditions": [],
            "default_trust_requirement": "TRUSTED",
            "default_metadata": {"chapter_ref": "controller"},
        },
        {
            "move_type_id": "refine_cover.default",
            "kind": "refine_cover",
            "description": (
                "Refine the active cover to reduce obstruction and improve local "
                "resolution.  Corresponds to the controller REFINE_COVER move."
            ),
            "default_cost": 2.2,
            "default_priority": 0.5,
            "default_expected_gain": 0.10,
            "default_preconditions": [],
            "default_postconditions": [],
            "default_trust_requirement": "PROVISIONAL",
            "default_metadata": {"chapter_ref": "controller"},
        },
        {
            "move_type_id": "consult_oracle.default",
            "kind": "consult_oracle",
            "description": (
                "Consult an oracle or external assistant for semantic guidance.  "
                "Corresponds to the controller CONSULT_ORACLE move."
            ),
            "default_cost": 3.0,
            "default_priority": 0.4,
            "default_expected_gain": 0.08,
            "default_preconditions": [],
            "default_postconditions": [],
            "default_trust_requirement": "PROVISIONAL",
            "default_metadata": {"chapter_ref": "controller"},
        },
        {
            "move_type_id": "extend_cover.default",
            "kind": "extend_cover",
            "description": (
                "Extend the active cover set by adding new cover elements.  "
                "Corresponds to Ch44 §44.5 Move EXTEND_COVER."
            ),
            "default_cost": 1.0,
            "default_priority": 0.7,
            "default_expected_gain": 0.15,
            "default_preconditions": [],
            "default_postconditions": [],
            "default_trust_requirement": "PROVISIONAL",
            "default_metadata": {"chapter_ref": "Ch44 §44.5"},
        },
        {
            "move_type_id": "lift_section.default",
            "kind": "lift_section",
            "description": (
                "Promote a document or proof section into the active scope.  "
                "Corresponds to Ch44 §44.5 Move LIFT_SECTION."
            ),
            "default_cost": 1.5,
            "default_priority": 0.6,
            "default_expected_gain": 0.20,
            "default_preconditions": [],
            "default_postconditions": [],
            "default_trust_requirement": "PROVISIONAL",
            "default_metadata": {"chapter_ref": "Ch44 §44.5"},
        },
        {
            "move_type_id": "bind_treaty.default",
            "kind": "bind_treaty",
            "description": (
                "Bind an external commitment treaty into the orchestrator's "
                "active treaty set.  Corresponds to Ch44 §44.5 Move BIND_TREATY."
            ),
            "default_cost": 2.0,
            "default_priority": 0.5,
            "default_expected_gain": 0.10,
            "default_preconditions": [],
            "default_postconditions": [],
            "default_trust_requirement": "TRUSTED",
            "default_metadata": {"chapter_ref": "Ch44 §44.5"},
        },
        {
            "move_type_id": "discharge_obligation.default",
            "kind": "discharge_obligation",
            "description": (
                "Discharge one or more pending obligations from the obligation "
                "set.  Converged states must have an empty obligation set.  "
                "Corresponds to Ch44 §44.5 Move DISCHARGE_OBLIGATION."
            ),
            "default_cost": 0.5,
            "default_priority": 0.9,
            "default_expected_gain": 0.25,
            "default_preconditions": [],
            "default_postconditions": [],
            "default_trust_requirement": "PROVISIONAL",
            "default_metadata": {"chapter_ref": "Ch44 §44.5"},
        },
        {
            "move_type_id": "open_channel.default",
            "kind": "open_channel",
            "description": (
                "Open a new inter-agent communication channel.  "
                "Corresponds to Ch44 §44.5 Move OPEN_CHANNEL."
            ),
            "default_cost": 1.0,
            "default_priority": 0.4,
            "default_expected_gain": 0.05,
            "default_preconditions": [],
            "default_postconditions": [],
            "default_trust_requirement": "PROVISIONAL",
            "default_metadata": {"chapter_ref": "Ch44 §44.5"},
        },
        {
            "move_type_id": "close_channel.default",
            "kind": "close_channel",
            "description": (
                "Close an active inter-agent communication channel.  "
                "Corresponds to Ch44 §44.5 Move CLOSE_CHANNEL."
            ),
            "default_cost": 0.5,
            "default_priority": 0.3,
            "default_expected_gain": 0.02,
            "default_preconditions": [],
            "default_postconditions": [],
            "default_trust_requirement": "PROVISIONAL",
            "default_metadata": {"chapter_ref": "Ch44 §44.5"},
        },
        {
            "move_type_id": "promote_context.default",
            "kind": "promote_context",
            "description": (
                "Promote a context frame to a higher prominence tier in the "
                "active context stack.  "
                "Corresponds to Ch44 §44.5 Move PROMOTE_CONTEXT."
            ),
            "default_cost": 0.8,
            "default_priority": 0.5,
            "default_expected_gain": 0.08,
            "default_preconditions": [],
            "default_postconditions": [],
            "default_trust_requirement": "PROVISIONAL",
            "default_metadata": {"chapter_ref": "Ch44 §44.5"},
        },
        {
            "move_type_id": "demote_context.default",
            "kind": "demote_context",
            "description": (
                "Demote a context frame to a lower prominence tier.  "
                "Corresponds to Ch44 §44.5 Move DEMOTE_CONTEXT."
            ),
            "default_cost": 0.8,
            "default_priority": 0.4,
            "default_expected_gain": 0.03,
            "default_preconditions": [],
            "default_postconditions": [],
            "default_trust_requirement": "PROVISIONAL",
            "default_metadata": {"chapter_ref": "Ch44 §44.5"},
        },
        {
            "move_type_id": "assert_invariant.default",
            "kind": "assert_invariant",
            "description": (
                "Assert a new semantic invariant that must be preserved by all "
                "subsequent moves in the trajectory.  "
                "Corresponds to Ch44 §44.5 Move ASSERT_INVARIANT."
            ),
            "default_cost": 1.0,
            "default_priority": 0.6,
            "default_expected_gain": 0.12,
            "default_preconditions": [],
            "default_postconditions": [],
            "default_trust_requirement": "TRUSTED",
            "default_metadata": {"chapter_ref": "Ch44 §44.5"},
        },
        {
            "move_type_id": "retract_invariant.default",
            "kind": "retract_invariant",
            "description": (
                "Retract a previously asserted invariant.  Use with care: "
                "retraction may invalidate earlier convergence certificates.  "
                "Corresponds to Ch44 §44.5 Move RETRACT_INVARIANT."
            ),
            "default_cost": 1.5,
            "default_priority": 0.2,
            "default_expected_gain": 0.00,
            "default_preconditions": [],
            "default_postconditions": [],
            "default_trust_requirement": "AUTHORITATIVE",
            "default_metadata": {"chapter_ref": "Ch44 §44.5"},
        },
        {
            "move_type_id": "checkpoint.default",
            "kind": "checkpoint",
            "description": (
                "Record a convergence checkpoint.  Does not alter the state but "
                "triggers certificate issuance if the current state satisfies the "
                "convergence criterion.  "
                "Corresponds to Ch44 §44.5 Move CHECKPOINT."
            ),
            "default_cost": 0.1,
            "default_priority": 0.5,
            "default_expected_gain": 0.00,
            "default_preconditions": [],
            "default_postconditions": [],
            "default_trust_requirement": "PROVISIONAL",
            "default_metadata": {"chapter_ref": "Ch44 §44.5"},
        },
        {
            "move_type_id": "rollback.default",
            "kind": "rollback",
            "description": (
                "Roll back the trajectory to a prior snapshot.  Used for "
                "recovery from diverged states.  "
                "Corresponds to Ch44 §44.5 Move ROLLBACK."
            ),
            "default_cost": 3.0,
            "default_priority": 0.1,
            "default_expected_gain": 0.00,
            "default_preconditions": [],
            "default_postconditions": [],
            "default_trust_requirement": "AUTHORITATIVE",
            "default_metadata": {"chapter_ref": "Ch44 §44.5"},
        },
    ]


def _default_law_types() -> list[dict[str, Any]]:
    """Return the list of standard control law type specifications.

    Each dict satisfies ``LAW_SPEC_REQUIRED_KEYS`` and can be passed directly
    to ``SemanticControlManifest.add_law_type`` or ``ControlLawCatalog.register``.

    Returns
    -------
    list[dict]
    """
    return [
        {
            "law_type_id": "greedy.default",
            "kind": "greedy",
            "name": "greedy",
            "description": (
                "Greedy control law: always selects the move with the highest "
                "net_value().  Guaranteed to make local progress but susceptible "
                "to local optima.  Theory2.tex Ch44 §44.6.1."
            ),
            "default_parameters": {},
        },
        {
            "law_type_id": "lookahead.default",
            "kind": "lookahead",
            "name": "lookahead",
            "description": (
                "Lookahead control law: evaluates k-step rollouts from each "
                "candidate root move and returns the root of the highest-scoring "
                "branch.  Theory2.tex Ch44 §44.6.2."
            ),
            "default_parameters": {"depth": 2, "beam_width": 3},
        },
        {
            "law_type_id": "balanced.default",
            "kind": "balanced",
            "name": "balanced",
            "description": (
                "Balanced control law: mixes immediate net_value with long-run "
                "coverage gain using a configurable alpha coefficient.  "
                "Theory2.tex Ch44 §44.6.3."
            ),
            "default_parameters": {"alpha": 0.5},
        },
        {
            "law_type_id": "adaptive.default",
            "kind": "adaptive",
            "name": "adaptive",
            "description": (
                "Adaptive control law: online bandit variant of the balanced "
                "law.  Updates alpha from a running reward signal.  "
                "Theory2.tex Ch44 §44.6.4."
            ),
            "default_parameters": {"alpha": 0.5, "lr": 0.01},
        },
    ]


def _default_invariants() -> list[str]:
    """Return the list of semantic invariants every trajectory must preserve.

    These invariants are encoded as human-readable strings for display and audit
    purposes; machine-enforceable versions are implemented in the state admission
    checker.

    Returns
    -------
    list[str]
    """
    return [
        (
            "INV-1 (Monotone Coverage): The coverage_ratio of successive states must "
            "be non-decreasing on the happy path (Ch44 §44.9 Def 44.12 I1)."
        ),
        (
            "INV-2 (Obligation Finiteness): The obligation_ids set is finite and "
            "bounded by OBLIGATION_STALL_THRESHOLD at every step "
            "(Ch44 §44.9 Def 44.12 I2)."
        ),
        (
            "INV-3 (Treaty Consistency): Every treaty_id referenced in obligation "
            "preconditions must be present in treaty_ids at the time the obligation "
            "is introduced (Ch44 §44.9 Def 44.12 I3)."
        ),
        (
            "INV-4 (Budget Non-Negativity): All numeric entries in the budget dict "
            "must remain non-negative throughout the trajectory "
            "(Ch44 §44.9 Def 44.12 I4)."
        ),
        (
            "INV-5 (Channel Acyclicity): The channel graph induced by channel_ids "
            "must be acyclic at each state snapshot "
            "(Ch44 §44.9 Def 44.12 I5)."
        ),
        (
            "INV-6 (Trust Monotonicity): Move trust requirements may only escalate "
            "along the trust tier lattice; no move may lower the effective trust "
            "tier of an already-trusted component "
            "(Ch44 §44.9 Def 44.12 I6)."
        ),
        (
            "INV-7 (Certificate Freshness): A ConvergenceCertificate must be "
            "re-issued if the state changes after issuance; stale certificates must "
            "not be used for downstream proof obligations "
            "(Ch44 §44.9 Def 44.12 I7)."
        ),
    ]


# ===========================================================================
# Public factory functions
# ===========================================================================


def build_manifest() -> SemanticControlManifest:
    """Build a fully-populated default SemanticControlManifest.

    Populates all standard move types (one per MoveKind), all standard law
    types, and the full set of semantic invariants described in theory2.tex
    Ch44 §44.9.

    Returns
    -------
    SemanticControlManifest
        A valid manifest ready for use by the orchestrator.
    """
    manifest = SemanticControlManifest(
        description=(
            "Default JuGeo semantic control manifest.  Registers all standard "
            "admissible move types and control law types defined in theory2.tex "
            "Ch44.  Invariants INV-1 through INV-7 are declared."
        ),
        chapter_ref=DEFAULT_CHAPTER_REF,
        metadata={
            "generator": "jugeo.orchestration.semantic_control.manifest.build_manifest",
            "models_version": MODELS_VERSION,
        },
    )

    for move_spec in _default_move_types():
        manifest.add_move_type(move_spec)

    for law_spec in _default_law_types():
        manifest.add_law_type(law_spec)

    for invariant in _default_invariants():
        manifest.invariants.append(invariant)

    logger.info(
        "Built SemanticControlManifest: %d move types, %d law types, %d invariants",
        len(manifest.move_types),
        len(manifest.law_types),
        len(manifest.invariants),
    )
    return manifest


def validate_manifest(
    manifest: SemanticControlManifest,
) -> tuple[bool, list[str]]:
    """Validate *manifest* and return (ok, errors).

    Parameters
    ----------
    manifest:
        The manifest to validate.

    Returns
    -------
    tuple[bool, list[str]]
        A pair ``(ok, errors)`` where *ok* is True when *errors* is empty.
    """
    errors = manifest.validate()
    return not errors, errors


def build_default_registry() -> MoveRegistry:
    """Build a MoveRegistry pre-populated with all standard move types.

    Returns
    -------
    MoveRegistry
    """
    registry = MoveRegistry(version=MANIFEST_VERSION)
    for spec in _default_move_types():
        move_type_id = spec["move_type_id"]
        try:
            registry.register(move_type_id, spec)
        except ValueError as exc:
            logger.warning(
                "Skipping move type '%s' during default registry build: %s",
                move_type_id,
                exc,
            )
    logger.info(
        "Built default MoveRegistry with %d entries", len(registry.entries)
    )
    return registry


def build_default_catalog() -> ControlLawCatalog:
    """Build a ControlLawCatalog pre-populated with all standard law types.

    Sets the greedy law as the default.

    Returns
    -------
    ControlLawCatalog
    """
    catalog = ControlLawCatalog()
    for spec in _default_law_types():
        law_id = spec["law_type_id"]
        try:
            catalog.register(law_id, spec)
        except ValueError as exc:
            logger.warning(
                "Skipping law type '%s' during default catalog build: %s",
                law_id,
                exc,
            )
    # Set greedy as default.
    if "greedy.default" in catalog.entries:
        catalog.set_default("greedy.default")

    logger.info(
        "Built default ControlLawCatalog with %d entries (default=%s)",
        len(catalog.entries),
        catalog.default_law_id,
    )
    return catalog
