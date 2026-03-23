from __future__ import annotations

r"""Package manifest for ``jugeo.python_runtime.callable_surfaces`` (theory2.tex Ch16).

Overview
--------
This module provides the canonical package manifest for the **callable_surfaces**
sub-package of ``jugeo.python_runtime``.  It encodes the theory-to-implementation
mapping for theory2.tex Chapter 16: *Python Callable Surfaces, Method Binding, and
the Descriptor Protocol*.

JuGeo (Judgment Geometry) treats every Python callable as a typed **section** of a
structured sheaf over the semantic site.  Chapter 16 establishes five analysis
capabilities that together let the runtime track, verify, and judge the interface
contract of any Python callable across the full MRO:

* **Callable-surface analysis** (§16.2) — extract and verify the typed interface of
  any callable as a :class:`~jugeo.python_runtime.callable_surfaces.models.CallableSurface`
  object.  The surface records parameter kinds, annotations, async/generator status,
  and decorator stack as an immutable, hash-stable value.
* **Method binding** (§16.3) — model the transformation from an unbound function to
  a bound method as a coordinate morphism in the semantic site.  Binding is tracked
  as a :class:`~jugeo.python_runtime.callable_surfaces.models.MethodBinding` and
  carries provenance back to the originating class coordinate.
* **Descriptor lookup** (§16.4) — encode the data/non-data descriptor precedence
  ordering (Python data model §3.3.2) as a sheaf restriction map.  A
  :class:`~jugeo.python_runtime.callable_surfaces.models.DescriptorRecord` carries
  the ``__get__``/``__set__``/``__delete__`` flags and the resulting lookup priority.
* **Class construction** (§16.5) — represent the MRO, metaclass, slot configuration,
  and ``__new__``/``__init__`` presence as a
  :class:`~jugeo.python_runtime.callable_surfaces.models.ClassConstruction` value.
* **Signature inspection** (§16.6) — perform full type-annotation resolution
  (following ``from __future__ import annotations`` semantics) and build
  :class:`~jugeo.python_runtime.callable_surfaces.models.SignatureRecord` objects
  for downstream theorem-schema generation.

Manifest responsibilities
--------------------------

:data:`PACKAGE_NAME`
    The canonical package identifier ``"callable_surfaces"``.

:data:`PACKAGE_VERSION`
    Semantic version string for this manifest revision.

:class:`Capability`
    Enumeration of the five analysis capabilities exported by this package.
    Each variant corresponds to a §16.xx sub-chapter in theory2.tex and is
    used by the :class:`PackageManifest` to gate capability-level queries.

:class:`ComponentRegistration`
    Immutable record associating a named implementation component with a
    :class:`Capability`, a coordinate prefix (used to scope judgments), an
    enabled flag, and extensible metadata.  Satisfies the sheaf-section
    axiom: two registrations with the same ``coordinate_prefix`` must agree
    on ``capability``.

:class:`PackageManifest`
    Mutable root manifest object.  Exposes registration, lookup, capability
    enumeration, serialization, validation, and factory helpers.  The manifest
    plays the role of the *global sections functor* in the Ch16 sheaf model:
    it collects all component registrations into a single coherent structure
    and checks local compatibility conditions.

:data:`MANIFEST`
    Module-level singleton instance, pre-populated by :func:`build_manifest`.

Copilot integration
--------------------
All copilot-assisted code generation within this sub-package is governed by
the trust algebra defined in theory2.tex Ch.2.  Generated stubs enter at
``TrustLevel.ORACLE_PROPOSED`` (level 2) and must be promoted explicitly
through CI verification before they carry ``SOLVER_DISCHARGED`` (level 4) or
higher trust.  The :attr:`ComponentRegistration.metadata` field carries a
``"copilot_assisted"`` boolean key whenever a component was initially scaffolded
with copilot assistance; this preserves the audit trail required by §16.9.

Theory alignment
-----------------
Section §16.1 of theory2.tex ("Ch16 Package Overview") is the primary reference.
Sections §16.2–§16.6 enumerate the five typed callable constructions; §16.7–§16.10
cover algorithms, integration, theorems, and the package API surface.

Examples
--------
Typical usage from tests or CI scripts::

    from jugeo.python_runtime.callable_surfaces.manifest import (
        MANIFEST, Capability, is_capability_enabled, list_capabilities,
    )

    # Check all five capabilities are present
    caps = MANIFEST.enabled_capabilities()
    assert Capability.METHOD_BINDING in caps

    # Inspect a specific registration
    reg = MANIFEST.lookup("method_binding")
    assert reg is not None
    print(reg.description)

    # Validate the manifest (should return no errors)
    errors = MANIFEST.validate()
    assert errors == []
"""

import hashlib
import json
import logging
import time
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any

from jugeo.geometry.site import CoordinateKind

# ---------------------------------------------------------------------------
# Module-level logger
# ---------------------------------------------------------------------------

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Package constants
# ---------------------------------------------------------------------------

PACKAGE_VERSION: str = "0.1.0"
"""Semantic version of the callable_surfaces manifest.

Follows ``MAJOR.MINOR.PATCH`` conventions.  The manifest version is separate
from any individual component version; it advances whenever the capability
set, registration schema, or coordinate-prefix conventions change.
"""

PACKAGE_NAME: str = "callable_surfaces"
"""Canonical package identifier used in coordinate prefixes and manifest keys.

All component coordinate prefixes are expected to start with this string
followed by a dot separator, e.g. ``"callable_surfaces.analysis"``.
"""

_CREATION_TIMESTAMP: str = "2024-01-01T00:00:00Z"
"""Fixed creation timestamp for deterministic manifest hashing in tests."""

_COORDINATE_ROOT: str = "python_runtime.callable_surfaces"
"""Root coordinate path shared by all components in this package."""


# ---------------------------------------------------------------------------
# Capability enum
# ---------------------------------------------------------------------------


class Capability(str, Enum):
    """The five analysis capabilities exported by the callable_surfaces package.

    Each variant maps one-to-one with a §16.xx sub-chapter in theory2.tex and
    corresponds to a distinct set of data models in ``models.py``.

    Theory references
    -----------------
    * :attr:`CALLABLE_SURFACE_ANALYSIS` ↔ theory2.tex §16.2
    * :attr:`METHOD_BINDING`             ↔ theory2.tex §16.3
    * :attr:`DESCRIPTOR_LOOKUP`          ↔ theory2.tex §16.4
    * :attr:`CLASS_CONSTRUCTION`         ↔ theory2.tex §16.5
    * :attr:`SIGNATURE_INSPECTION`       ↔ theory2.tex §16.6
    """

    CALLABLE_SURFACE_ANALYSIS = "callable_surface_analysis"
    METHOD_BINDING = "method_binding"
    DESCRIPTOR_LOOKUP = "descriptor_lookup"
    CLASS_CONSTRUCTION = "class_construction"
    SIGNATURE_INSPECTION = "signature_inspection"

    # ------------------------------------------------------------------
    # Helper methods
    # ------------------------------------------------------------------

    def theory_section(self) -> str:
        """Return the primary theory2.tex section reference for this capability.

        Returns
        -------
        str
            A section string of the form ``"§16.N"``.

        Examples
        --------
        >>> Capability.METHOD_BINDING.theory_section()
        '§16.3'
        """
        _sections: dict[str, str] = {
            "callable_surface_analysis": "§16.2",
            "method_binding": "§16.3",
            "descriptor_lookup": "§16.4",
            "class_construction": "§16.5",
            "signature_inspection": "§16.6",
        }
        return _sections[self.value]

    def description(self) -> str:
        """Return a one-sentence description of this capability.

        Returns
        -------
        str
            Human-readable description suitable for CLI output or manifests.
        """
        _desc: dict[str, str] = {
            "callable_surface_analysis": (
                "Extract and verify the typed interface of any Python callable as an "
                "immutable CallableSurface value object."
            ),
            "method_binding": (
                "Model the unbound-to-bound method transformation as a coordinate "
                "morphism in the semantic site."
            ),
            "descriptor_lookup": (
                "Encode the data/non-data descriptor precedence ordering as a sheaf "
                "restriction map with explicit lookup priorities."
            ),
            "class_construction": (
                "Represent MRO, metaclass, slot configuration, and __new__/__init__ "
                "presence as an immutable ClassConstruction value."
            ),
            "signature_inspection": (
                "Perform full type-annotation resolution and emit SignatureRecord "
                "objects for downstream theorem-schema generation."
            ),
        }
        return _desc[self.value]

    def coordinate_kind(self) -> CoordinateKind:
        """Return the :class:`~jugeo.geometry.site.CoordinateKind` associated with
        this capability's primary data model.

        Returns
        -------
        CoordinateKind
            The semantic site coordinate kind for the capability's domain.
        """
        _kinds: dict[str, CoordinateKind] = {
            "callable_surface_analysis": CoordinateKind.FUNCTION,
            "method_binding": CoordinateKind.FUNCTION,
            "descriptor_lookup": CoordinateKind.INTERFACE,
            "class_construction": CoordinateKind.MODULE,
            "signature_inspection": CoordinateKind.FUNCTION,
        }
        return _kinds[self.value]

    def summary_line(self) -> str:
        """Return a compact one-line summary combining the variant name and section.

        Returns
        -------
        str
            A string of the form ``"CALLABLE_SURFACE_ANALYSIS (§16.2)"``.
        """
        return f"{self.name} ({self.theory_section()})"


# ---------------------------------------------------------------------------
# ComponentRegistration
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ComponentRegistration:
    """Immutable record registering a named component with a :class:`Capability`.

    Each :class:`ComponentRegistration` represents one implementation component
    (a stage file, algorithm module, or integration helper) and its association
    with a specific :class:`Capability`.  The ``coordinate_prefix`` field scopes
    all judgments emitted by the component to a sub-tree of the semantic site.

    Theory alignment
    ----------------
    A component registration corresponds to a *local section* in the Ch16 sheaf
    model: it provides data (a component implementation) over a specific open set
    (the ``coordinate_prefix`` sub-tree) of the semantic site.  The
    :class:`PackageManifest` checks the *gluing axiom*: no two registrations may
    claim the same ``coordinate_prefix`` for different capabilities.

    Parameters
    ----------
    name:
        Unique identifier for this component, e.g. ``"callable_surface_analysis"``.
        Must be non-empty and contain only alphanumeric characters and underscores.
    capability:
        The :class:`Capability` this component implements.
    description:
        One-paragraph description of what the component does and which
        theory2.tex sections it covers.
    coordinate_prefix:
        Dotted coordinate path that scopes all judgments emitted by this
        component, e.g. ``"callable_surfaces.analysis"``.  Must start with
        :data:`_COORDINATE_ROOT`.
    enabled:
        Whether this component is active in the current deployment.  Disabled
        components are registered but excluded from
        :meth:`PackageManifest.enabled_capabilities`.
    metadata:
        Free-form key/value store for component-specific annotations.  The
        keys ``"copilot_assisted"``, ``"theory_confidence"``, and
        ``"open_todos"`` carry special meaning to the CI validation pipeline.

    Examples
    --------
    >>> from jugeo.python_runtime.callable_surfaces.manifest import (
    ...     ComponentRegistration, Capability,
    ... )
    >>> reg = ComponentRegistration(
    ...     name="callable_surface_analysis",
    ...     capability=Capability.CALLABLE_SURFACE_ANALYSIS,
    ...     description="Extracts CallableSurface values from live callables.",
    ...     coordinate_prefix="python_runtime.callable_surfaces.analysis",
    ...     enabled=True,
    ...     metadata={"theory_confidence": 0.9, "copilot_assisted": True},
    ... )
    >>> reg.is_active()
    True
    >>> reg.serialize()["name"]
    'callable_surface_analysis'
    """

    name: str
    capability: Capability
    description: str
    coordinate_prefix: str
    enabled: bool
    metadata: dict[str, Any]

    # ------------------------------------------------------------------
    # Immutable helpers
    # ------------------------------------------------------------------

    def is_active(self) -> bool:
        """Return True when this registration is enabled.

        Returns
        -------
        bool
            Equivalent to ``self.enabled``.
        """
        return self.enabled

    def theory_confidence(self) -> float:
        """Return the theory-coverage confidence score stored in metadata.

        Returns
        -------
        float
            The ``"theory_confidence"`` value from :attr:`metadata`, or
            ``0.0`` if the key is absent.  The value is clamped to ``[0.0, 1.0]``.
        """
        raw = self.metadata.get("theory_confidence", 0.0)
        return max(0.0, min(1.0, float(raw)))

    def is_copilot_assisted(self) -> bool:
        """Return True if this component was scaffolded with copilot assistance.

        Returns
        -------
        bool
            The ``"copilot_assisted"`` flag from :attr:`metadata`.
        """
        return bool(self.metadata.get("copilot_assisted", False))

    def open_todos(self) -> list[str]:
        """Return the list of open implementation TODOs from metadata.

        Returns
        -------
        list[str]
            The ``"open_todos"`` list from :attr:`metadata`, or empty list.
        """
        raw = self.metadata.get("open_todos", [])
        return list(raw) if isinstance(raw, (list, tuple)) else []

    def with_enabled(self, enabled: bool) -> "ComponentRegistration":
        """Return a copy of this registration with ``enabled`` changed.

        Parameters
        ----------
        enabled:
            The new value for the :attr:`enabled` flag.

        Returns
        -------
        ComponentRegistration
            A new instance with :attr:`enabled` set to *enabled*.
        """
        return replace(self, enabled=enabled)

    def with_metadata_key(self, key: str, value: Any) -> "ComponentRegistration":
        """Return a copy of this registration with one metadata key updated.

        Parameters
        ----------
        key:
            The metadata key to set.
        value:
            The new value for the key.

        Returns
        -------
        ComponentRegistration
            A new instance with the updated :attr:`metadata` mapping.
        """
        updated = {**self.metadata, key: value}
        return replace(self, metadata=updated)

    # ------------------------------------------------------------------
    # Serialisation / deserialisation
    # ------------------------------------------------------------------

    def serialize(self) -> dict[str, Any]:
        """Convert this registration to a JSON-serialisable dictionary.

        The dictionary produced here is the canonical on-wire format used by
        :meth:`PackageManifest.serialize` and consumed by
        :meth:`ComponentRegistration.parse`.

        Returns
        -------
        dict[str, Any]
            Keys: ``name``, ``capability``, ``description``,
            ``coordinate_prefix``, ``enabled``, ``metadata``.

        Examples
        --------
        >>> reg.serialize()["capability"]
        'callable_surface_analysis'
        """
        return {
            "name": self.name,
            "capability": self.capability.value,
            "description": self.description,
            "coordinate_prefix": self.coordinate_prefix,
            "enabled": self.enabled,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def parse(cls, data: dict[str, Any]) -> "ComponentRegistration":
        """Reconstruct a :class:`ComponentRegistration` from a serialised dict.

        Parameters
        ----------
        data:
            A dictionary as produced by :meth:`serialize`.

        Returns
        -------
        ComponentRegistration
            The reconstructed instance.

        Raises
        ------
        KeyError
            If any required key is missing from *data*.
        ValueError
            If ``capability`` is not a valid :class:`Capability` value.

        Examples
        --------
        >>> reg2 = ComponentRegistration.parse(reg.serialize())
        >>> reg2 == reg
        True
        """
        return cls(
            name=data["name"],
            capability=Capability(data["capability"]),
            description=data.get("description", ""),
            coordinate_prefix=data.get("coordinate_prefix", ""),
            enabled=bool(data.get("enabled", True)),
            metadata=dict(data.get("metadata", {})),
        )

    def fingerprint(self) -> str:
        """Return a deterministic hex fingerprint of this registration's content.

        The fingerprint is derived from the JSON serialisation of all fields.
        It can be used for cache invalidation or change detection.

        Returns
        -------
        str
            A 16-character hex string (truncated SHA-256 digest).
        """
        payload = json.dumps(self.serialize(), sort_keys=True).encode()
        return hashlib.sha256(payload).hexdigest()[:16]


# ---------------------------------------------------------------------------
# PackageManifest
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class PackageManifest:
    """Mutable root manifest for the callable_surfaces package.

    The :class:`PackageManifest` aggregates all :class:`ComponentRegistration`
    objects into a single coherent structure.  It plays the role of the *global
    sections functor* in the Ch16 sheaf model: it collects local sections
    (component registrations) and checks that they satisfy the gluing axiom.

    Theory alignment
    ----------------
    * §16.1 — "Package overview and manifest structure."
    * §16.9 — "CI validation and trust promotion pipeline."

    Parameters
    ----------
    registrations:
        Ordered list of :class:`ComponentRegistration` objects.  Insertions
        are tracked in arrival order; lookups by name use a linear scan.
    version:
        Semantic version string, defaults to :data:`PACKAGE_VERSION`.
    name:
        Package identifier, defaults to :data:`PACKAGE_NAME`.

    Examples
    --------
    >>> from jugeo.python_runtime.callable_surfaces.manifest import (
    ...     PackageManifest, ComponentRegistration, Capability,
    ... )
    >>> manifest = PackageManifest()
    >>> reg = ComponentRegistration(
    ...     name="method_binding",
    ...     capability=Capability.METHOD_BINDING,
    ...     description="Tracks bound-method morphisms.",
    ...     coordinate_prefix="python_runtime.callable_surfaces.binding",
    ...     enabled=True,
    ...     metadata={},
    ... )
    >>> manifest.register(reg)
    >>> manifest.lookup("method_binding") is not None
    True
    >>> manifest.enabled_capabilities()
    [<Capability.METHOD_BINDING: 'method_binding'>]
    """

    registrations: list[ComponentRegistration] = field(default_factory=list)
    version: str = field(default=PACKAGE_VERSION)
    name: str = field(default=PACKAGE_NAME)

    # ------------------------------------------------------------------
    # Mutation helpers
    # ------------------------------------------------------------------

    def register(self, reg: ComponentRegistration) -> None:
        """Add a :class:`ComponentRegistration` to this manifest.

        If a registration with the same :attr:`~ComponentRegistration.name`
        already exists it is **replaced** in-place (preserving insertion order
        up to the original position).

        Parameters
        ----------
        reg:
            The registration to add or replace.

        Returns
        -------
        None

        Examples
        --------
        >>> manifest.register(reg)
        >>> len(manifest.registrations)
        1
        """
        for i, existing in enumerate(self.registrations):
            if existing.name == reg.name:
                self.registrations[i] = reg
                logger.debug("Replaced registration %r at position %d.", reg.name, i)
                return
        self.registrations.append(reg)
        logger.debug("Registered new component %r (capability=%s).", reg.name, reg.capability.value)

    def lookup(self, name: str) -> ComponentRegistration | None:
        """Look up a registration by its component name.

        Parameters
        ----------
        name:
            The :attr:`~ComponentRegistration.name` to search for.

        Returns
        -------
        ComponentRegistration | None
            The matching registration, or ``None`` if not found.

        Examples
        --------
        >>> reg = manifest.lookup("method_binding")
        >>> reg.capability
        <Capability.METHOD_BINDING: 'method_binding'>
        """
        for reg in self.registrations:
            if reg.name == name:
                return reg
        return None

    def lookup_by_capability(self, cap: Capability) -> list[ComponentRegistration]:
        """Return all registrations that implement a given :class:`Capability`.

        Parameters
        ----------
        cap:
            The capability to filter by.

        Returns
        -------
        list[ComponentRegistration]
            All matching registrations (enabled or disabled).

        Examples
        --------
        >>> regs = manifest.lookup_by_capability(Capability.DESCRIPTOR_LOOKUP)
        >>> all(r.capability == Capability.DESCRIPTOR_LOOKUP for r in regs)
        True
        """
        return [r for r in self.registrations if r.capability == cap]

    def enabled_capabilities(self) -> list[Capability]:
        """Return the deduplicated list of capabilities with at least one enabled component.

        Returns
        -------
        list[Capability]
            Capabilities that have at least one enabled registration, in the
            order they first appear in :attr:`registrations`.

        Examples
        --------
        >>> caps = manifest.enabled_capabilities()
        >>> Capability.CALLABLE_SURFACE_ANALYSIS in caps
        True
        """
        seen: set[Capability] = set()
        result: list[Capability] = []
        for reg in self.registrations:
            if reg.enabled and reg.capability not in seen:
                seen.add(reg.capability)
                result.append(reg.capability)
        return result

    def is_complete(self) -> bool:
        """Return True when all five :class:`Capability` values have an enabled registration.

        Returns
        -------
        bool
            ``True`` iff :meth:`enabled_capabilities` contains all five variants.

        Examples
        --------
        >>> manifest.is_complete()  # False until all five are registered
        False
        """
        enabled = set(self.enabled_capabilities())
        return enabled == set(Capability)

    # ------------------------------------------------------------------
    # Serialisation / deserialisation
    # ------------------------------------------------------------------

    def serialize(self) -> dict[str, Any]:
        """Convert this manifest to a JSON-serialisable dictionary.

        Returns
        -------
        dict[str, Any]
            Keys: ``name``, ``version``, ``registrations``,
            ``enabled_capabilities``, ``is_complete``.

        Examples
        --------
        >>> data = manifest.serialize()
        >>> data["version"]
        '0.1.0'
        """
        return {
            "name": self.name,
            "version": self.version,
            "registrations": [r.serialize() for r in self.registrations],
            "enabled_capabilities": [c.value for c in self.enabled_capabilities()],
            "is_complete": self.is_complete(),
        }

    @classmethod
    def parse(cls, data: dict[str, Any]) -> "PackageManifest":
        """Reconstruct a :class:`PackageManifest` from a serialised dictionary.

        Parameters
        ----------
        data:
            A dictionary as produced by :meth:`serialize`.

        Returns
        -------
        PackageManifest
            The reconstructed manifest with all registrations re-parsed.

        Raises
        ------
        KeyError
            If any required key is missing from a nested registration dict.
        ValueError
            If an unrecognised capability value is encountered.

        Examples
        --------
        >>> manifest2 = PackageManifest.parse(manifest.serialize())
        >>> manifest2.version == manifest.version
        True
        """
        registrations = [
            ComponentRegistration.parse(r)
            for r in data.get("registrations", [])
        ]
        return cls(
            registrations=registrations,
            version=data.get("version", PACKAGE_VERSION),
            name=data.get("name", PACKAGE_NAME),
        )

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def validate(self) -> list[str]:
        """Run consistency checks and return a list of error strings.

        Checks performed:

        1. Every registration has a non-empty ``name``.
        2. Every ``coordinate_prefix`` starts with :data:`_COORDINATE_ROOT`.
        3. No two registrations share the same ``name``.
        4. No two enabled registrations share the same ``coordinate_prefix``
           with different capabilities (gluing-axiom violation).
        5. ``version`` matches :data:`PACKAGE_VERSION`.

        Returns
        -------
        list[str]
            Empty list if the manifest is valid; otherwise a list of
            human-readable error strings.

        Examples
        --------
        >>> errors = manifest.validate()
        >>> errors  # [] when manifest is correctly built
        []
        """
        errors: list[str] = []
        seen_names: set[str] = set()
        prefix_cap: dict[str, Capability] = {}

        for i, reg in enumerate(self.registrations):
            if not reg.name:
                errors.append(f"Registration[{i}]: name must not be empty.")

            if reg.name in seen_names:
                errors.append(
                    f"Registration[{i}]: duplicate name {reg.name!r}."
                )
            else:
                seen_names.add(reg.name)

            if not reg.coordinate_prefix.startswith(_COORDINATE_ROOT):
                errors.append(
                    f"Registration {reg.name!r}: coordinate_prefix "
                    f"{reg.coordinate_prefix!r} must start with "
                    f"{_COORDINATE_ROOT!r}."
                )

            if reg.enabled:
                prev_cap = prefix_cap.get(reg.coordinate_prefix)
                if prev_cap is not None and prev_cap != reg.capability:
                    errors.append(
                        f"Registration {reg.name!r}: coordinate_prefix "
                        f"{reg.coordinate_prefix!r} already claimed by "
                        f"capability {prev_cap.value!r} (gluing-axiom violation)."
                    )
                else:
                    prefix_cap[reg.coordinate_prefix] = reg.capability

        if self.version != PACKAGE_VERSION:
            errors.append(
                f"Manifest version {self.version!r} does not match "
                f"PACKAGE_VERSION {PACKAGE_VERSION!r}."
            )

        return errors

    # ------------------------------------------------------------------
    # Diagnostics
    # ------------------------------------------------------------------

    def summary(self) -> str:
        """Return a multi-line human-readable summary of this manifest.

        Returns
        -------
        str
            Formatted summary with version, component count, capability list,
            and validation status.
        """
        caps = self.enabled_capabilities()
        errors = self.validate()
        status = "valid" if not errors else f"{len(errors)} error(s)"
        lines: list[str] = [
            f"PackageManifest: {self.name} v{self.version}",
            f"  Components : {len(self.registrations)} registered",
            f"  Capabilities: {', '.join(c.value for c in caps)}",
            f"  Complete   : {self.is_complete()}",
            f"  Validation : {status}",
        ]
        if errors:
            for err in errors:
                lines.append(f"    ✗ {err}")
        return "\n".join(lines)

    def component_names(self) -> list[str]:
        """Return the names of all registered components in insertion order.

        Returns
        -------
        list[str]
            Names as strings.
        """
        return [r.name for r in self.registrations]

    def enabled_registrations(self) -> list[ComponentRegistration]:
        """Return only the enabled registrations.

        Returns
        -------
        list[ComponentRegistration]
            Registrations where :attr:`~ComponentRegistration.enabled` is ``True``.
        """
        return [r for r in self.registrations if r.enabled]

    def to_json(self, indent: int = 2) -> str:
        """Serialise this manifest to a pretty-printed JSON string.

        Parameters
        ----------
        indent:
            JSON indentation level, default 2.

        Returns
        -------
        str
            A valid JSON document.
        """
        return json.dumps(self.serialize(), indent=indent)


# ---------------------------------------------------------------------------
# Factory: build_manifest
# ---------------------------------------------------------------------------


def build_manifest() -> PackageManifest:
    """Construct a fully populated :class:`PackageManifest` for callable_surfaces.

    This function creates the five canonical :class:`ComponentRegistration`
    objects—one per :class:`Capability`—and assembles them into a validated
    manifest.  It is called once at module load time to populate the
    :data:`MANIFEST` singleton.

    Each registration is annotated with:

    * ``"theory_confidence"`` — estimated coverage confidence on ``[0.0, 1.0]``.
    * ``"copilot_assisted"`` — whether the component was scaffolded by copilot.
    * ``"open_todos"`` — list of known implementation gaps.
    * ``"since_version"`` — package version at which the component was added.
    * ``"theory_section"`` — primary theory2.tex section reference.

    Returns
    -------
    PackageManifest
        A complete, validated manifest ready for use as :data:`MANIFEST`.

    Raises
    ------
    RuntimeError
        If the constructed manifest fails :meth:`PackageManifest.validate`.

    Examples
    --------
    >>> from jugeo.python_runtime.callable_surfaces.manifest import build_manifest
    >>> m = build_manifest()
    >>> m.is_complete()
    True
    >>> m.validate()
    []
    """
    manifest = PackageManifest(version=PACKAGE_VERSION, name=PACKAGE_NAME)

    manifest.register(
        ComponentRegistration(
            name="callable_surface_analysis",
            capability=Capability.CALLABLE_SURFACE_ANALYSIS,
            description=(
                "Implements theory2.tex §16.2: extraction of typed callable surfaces. "
                "Converts any Python callable (function, lambda, bound method, built-in) "
                "into an immutable CallableSurface value that records parameter kinds, "
                "annotation strings, async/generator flags, and the decorator stack. "
                "The surface serves as the primary evidence carrier for behavioral "
                "judgments in downstream theorem-schema generation."
            ),
            coordinate_prefix=f"{_COORDINATE_ROOT}.analysis",
            enabled=True,
            metadata={
                "theory_confidence": 0.88,
                "copilot_assisted": True,
                "open_todos": [
                    "Handle typing.overload stacks (§16.2.4)",
                    "Resolve PEP-695 type-parameter syntax",
                ],
                "since_version": "0.1.0",
                "theory_section": "§16.2",
                "primary_model": "CallableSurface",
                "coordinate_kind": CoordinateKind.FUNCTION.value,
            },
        )
    )

    manifest.register(
        ComponentRegistration(
            name="method_binding",
            capability=Capability.METHOD_BINDING,
            description=(
                "Implements theory2.tex §16.3: method-binding morphisms. "
                "Models the Python descriptor __get__ invocation that converts an "
                "unbound function into a bound method as a coordinate morphism in "
                "the semantic site.  A MethodBinding record carries the instance "
                "and class coordinates together with the morphism kind and bound "
                "argument names, enabling downstream solvers to reason about "
                "the effective arity and self/cls injection."
            ),
            coordinate_prefix=f"{_COORDINATE_ROOT}.binding",
            enabled=True,
            metadata={
                "theory_confidence": 0.85,
                "copilot_assisted": True,
                "open_todos": [
                    "Handle __init_subclass__ binding edge cases (§16.3.5)",
                ],
                "since_version": "0.1.0",
                "theory_section": "§16.3",
                "primary_model": "MethodBinding",
                "coordinate_kind": CoordinateKind.FUNCTION.value,
            },
        )
    )

    manifest.register(
        ComponentRegistration(
            name="descriptor_lookup",
            capability=Capability.DESCRIPTOR_LOOKUP,
            description=(
                "Implements theory2.tex §16.4: descriptor-protocol precedence. "
                "Encodes the three-tier lookup ordering from Python data model §3.3.2 "
                "(data descriptors > instance __dict__ > non-data descriptors) as an "
                "explicit sheaf restriction map.  Each DescriptorRecord carries "
                "has_get/has_set/has_delete flags and a computed lookup_priority, "
                "making the ordering transparent to the judgment algebra."
            ),
            coordinate_prefix=f"{_COORDINATE_ROOT}.descriptor",
            enabled=True,
            metadata={
                "theory_confidence": 0.90,
                "copilot_assisted": True,
                "open_todos": [
                    "Model __set_name__ interaction (§16.4.3)",
                    "Track slot descriptor ordering with __slots__",
                ],
                "since_version": "0.1.0",
                "theory_section": "§16.4",
                "primary_model": "DescriptorRecord",
                "coordinate_kind": CoordinateKind.INTERFACE.value,
            },
        )
    )

    manifest.register(
        ComponentRegistration(
            name="class_construction",
            capability=Capability.CLASS_CONSTRUCTION,
            description=(
                "Implements theory2.tex §16.5: class construction and MRO. "
                "Captures the full construction state of a Python class as an "
                "immutable ClassConstruction value: base class tuple, C3-linearised "
                "MRO, metaclass name, slot configuration, and __new__/__init__ "
                "presence flags.  This record feeds both the descriptor-lookup "
                "component (for inherited descriptors) and the signature-inspection "
                "component (for inherited __init__ signatures)."
            ),
            coordinate_prefix=f"{_COORDINATE_ROOT}.class_construction",
            enabled=True,
            metadata={
                "theory_confidence": 0.87,
                "copilot_assisted": True,
                "open_todos": [
                    "Handle virtual subclass registration via abc.ABCMeta (§16.5.4)",
                    "Model dataclass-generated __init__ vs explicit __init__",
                ],
                "since_version": "0.1.0",
                "theory_section": "§16.5",
                "primary_model": "ClassConstruction",
                "coordinate_kind": CoordinateKind.MODULE.value,
            },
        )
    )

    manifest.register(
        ComponentRegistration(
            name="signature_inspection",
            capability=Capability.SIGNATURE_INSPECTION,
            description=(
                "Implements theory2.tex §16.6: full signature resolution. "
                "Builds SignatureRecord objects by combining CallableSurface "
                "parameter data with PEP-563/PEP-649 annotation evaluation.  "
                "Resolution errors are captured as tuple[str, ...] rather than "
                "raised, allowing partial signatures to carry evidence to downstream "
                "theorem-schema encoders.  The is_complete flag signals whether all "
                "annotation strings resolved without error."
            ),
            coordinate_prefix=f"{_COORDINATE_ROOT}.signature",
            enabled=True,
            metadata={
                "theory_confidence": 0.82,
                "copilot_assisted": True,
                "open_todos": [
                    "Integrate PEP-649 lazy annotation evaluation (§16.6.5)",
                    "Handle TypeVar bounds in resolved_return",
                    "Resolve ParamSpec and TypeVarTuple (§16.6.6)",
                ],
                "since_version": "0.1.0",
                "theory_section": "§16.6",
                "primary_model": "SignatureRecord",
                "coordinate_kind": CoordinateKind.FUNCTION.value,
            },
        )
    )

    errors = manifest.validate()
    if errors:
        msg = "build_manifest produced an invalid manifest:\n" + "\n".join(
            f"  {e}" for e in errors
        )
        raise RuntimeError(msg)

    logger.info(
        "callable_surfaces manifest built: %d components, complete=%s.",
        len(manifest.registrations),
        manifest.is_complete(),
    )
    return manifest


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

MANIFEST: PackageManifest = build_manifest()
"""Module-level singleton manifest, pre-populated by :func:`build_manifest`.

This is the canonical source of truth for the callable_surfaces package
component registry at runtime.  Downstream packages should import this
singleton rather than constructing their own manifest.

Examples
--------
>>> from jugeo.python_runtime.callable_surfaces.manifest import MANIFEST
>>> MANIFEST.is_complete()
True
"""


# ---------------------------------------------------------------------------
# Public helper functions
# ---------------------------------------------------------------------------


def get_component_info(name: str) -> ComponentRegistration | None:
    """Look up a component registration by name in the module-level manifest.

    This is a convenience wrapper around ``MANIFEST.lookup(name)`` for use in
    environments where importing the full manifest object is not desired.

    Parameters
    ----------
    name:
        The component name to look up, e.g. ``"method_binding"``.

    Returns
    -------
    ComponentRegistration | None
        The matching registration from :data:`MANIFEST`, or ``None`` if no
        component with that name is registered.

    Examples
    --------
    >>> from jugeo.python_runtime.callable_surfaces.manifest import get_component_info
    >>> info = get_component_info("descriptor_lookup")
    >>> info.capability.value
    'descriptor_lookup'
    >>> get_component_info("nonexistent") is None
    True
    """
    return MANIFEST.lookup(name)


def list_capabilities() -> list[Capability]:
    """Return all enabled capabilities from the module-level manifest.

    This is a convenience wrapper around ``MANIFEST.enabled_capabilities()``.

    Returns
    -------
    list[Capability]
        Enabled capabilities in insertion order.  On a fully built manifest
        this returns all five :class:`Capability` variants.

    Examples
    --------
    >>> from jugeo.python_runtime.callable_surfaces.manifest import list_capabilities
    >>> caps = list_capabilities()
    >>> len(caps)
    5
    """
    return MANIFEST.enabled_capabilities()


def is_capability_enabled(cap: Capability) -> bool:
    """Check whether a specific capability has at least one enabled registration.

    Parameters
    ----------
    cap:
        The :class:`Capability` to query.

    Returns
    -------
    bool
        ``True`` if *cap* appears in :meth:`PackageManifest.enabled_capabilities`
        for the module-level :data:`MANIFEST`.

    Examples
    --------
    >>> from jugeo.python_runtime.callable_surfaces.manifest import (
    ...     is_capability_enabled, Capability,
    ... )
    >>> is_capability_enabled(Capability.SIGNATURE_INSPECTION)
    True
    >>> is_capability_enabled(Capability.CLASS_CONSTRUCTION)
    True
    """
    return cap in MANIFEST.enabled_capabilities()


def describe_capability(cap: Capability) -> str:
    """Return a formatted multi-line description for a capability.

    Combines the capability's built-in description with the registered
    component information from :data:`MANIFEST`.

    Parameters
    ----------
    cap:
        The :class:`Capability` to describe.

    Returns
    -------
    str
        A formatted string with the capability name, theory section, description,
        and a list of registered components that implement it.

    Examples
    --------
    >>> from jugeo.python_runtime.callable_surfaces.manifest import (
    ...     describe_capability, Capability,
    ... )
    >>> print(describe_capability(Capability.METHOD_BINDING))  # doctest: +SKIP
    METHOD_BINDING (§16.3)
      ...
    """
    components = MANIFEST.lookup_by_capability(cap)
    enabled_count = sum(1 for c in components if c.enabled)
    lines: list[str] = [
        f"{cap.name} ({cap.theory_section()})",
        f"  Description : {cap.description()}",
        f"  Components  : {len(components)} registered, {enabled_count} enabled",
    ]
    for reg in components:
        status = "✓" if reg.enabled else "✗"
        conf = f"{reg.theory_confidence():.0%}"
        lines.append(
            f"    {status} {reg.name} (confidence={conf}, "
            f"prefix={reg.coordinate_prefix!r})"
        )
    return "\n".join(lines)


def validate_manifest() -> list[str]:
    """Run validation on the module-level :data:`MANIFEST` and return errors.

    This is a convenience wrapper around ``MANIFEST.validate()``.

    Returns
    -------
    list[str]
        Empty list if valid; otherwise a list of human-readable error strings.

    Examples
    --------
    >>> from jugeo.python_runtime.callable_surfaces.manifest import validate_manifest
    >>> validate_manifest()
    []
    """
    errors = MANIFEST.validate()
    if errors:
        logger.warning(
            "callable_surfaces manifest validation found %d error(s).", len(errors)
        )
    return errors


def component_count(enabled_only: bool = False) -> int:
    """Return the number of registered components in the module-level manifest.

    Parameters
    ----------
    enabled_only:
        If ``True``, count only enabled registrations.  Default ``False``
        counts all registrations.

    Returns
    -------
    int
        The component count.

    Examples
    --------
    >>> from jugeo.python_runtime.callable_surfaces.manifest import component_count
    >>> component_count()
    5
    >>> component_count(enabled_only=True)
    5
    """
    if enabled_only:
        return len(MANIFEST.enabled_registrations())
    return len(MANIFEST.registrations)


def manifest_fingerprint() -> str:
    """Return a deterministic hex fingerprint of the module-level manifest.

    The fingerprint is derived from the SHA-256 hash of the manifest's JSON
    serialisation (keys sorted).  It can be used to detect manifest drift
    between deployments.

    Returns
    -------
    str
        A 16-character hex string.

    Examples
    --------
    >>> from jugeo.python_runtime.callable_surfaces.manifest import manifest_fingerprint
    >>> fp = manifest_fingerprint()
    >>> len(fp)
    16
    """
    payload = json.dumps(MANIFEST.serialize(), sort_keys=True).encode()
    return hashlib.sha256(payload).hexdigest()[:16]


# ---------------------------------------------------------------------------
# __all__
# ---------------------------------------------------------------------------

__all__ = [
    # Constants
    "PACKAGE_VERSION",
    "PACKAGE_NAME",
    # Enumerations
    "Capability",
    # Data models
    "ComponentRegistration",
    "PackageManifest",
    # Singleton
    "MANIFEST",
    # Factory
    "build_manifest",
    # Helper functions
    "get_component_info",
    "list_capabilities",
    "is_capability_enabled",
    "describe_capability",
    "validate_manifest",
    "component_count",
    "manifest_fingerprint",
]
