"""Package manifest for the ``heap_aliasing`` sub-package.

This module defines the :class:`PackageManifest` registry that tracks every
analysis component offered by ``heap_aliasing``.  The design follows the
sheaf-theoretic framework described in **theory2.tex Ch17**: each component is
a *section* whose support is a subset of the heap coordinate space, and the
manifest acts as the global section that glues all local sections together.

Copilot integration note
------------------------
This file was scaffolded with GitHub Copilot assistance to ensure that the
manifest structure stays in sync with the broader jugeo copilot integration
pipeline.  Any new component registrations should be added both here and in
the corresponding copilot skill descriptor.

Background (theory2.tex Ch17)
------------------------------
Heap aliasing analysis is grounded in the observation that two Python
references ``x`` and ``y`` alias each other iff they share the same *identity
coordinate* — the singleton coordinate ``{id(x)}``.  The components registered
in this manifest each implement a different facet of that analysis:

* **HeapAnalysis**: enumerates heap objects and assigns identity coordinates.
* **AliasDetection**: computes alias partitions (equivalence classes of
  references sharing an identity coordinate).
* **IdentityTracking**: monitors ``id()`` lifetimes across garbage-collection
  cycles.
* **MutationValidation**: enforces the *descent check* (sheaf condition) for
  every field write.
* **AliasingJudgment**: produces :class:`~jugeo.judgments.judgment_terms.Judgment`
  objects that encode aliasing facts with evidence bundles.

Usage example
-------------
>>> from jugeo.python_runtime.heap_aliasing.manifest import MANIFEST, Capability
>>> MANIFEST.version
'0.1.0'
>>> regs = MANIFEST.lookup_by_capability(Capability.ALIAS_DETECTION)
>>> len(regs)
1
>>> MANIFEST.summary()  # doctest: +ELLIPSIS
'Package heap_aliasing v0.1.0 ...'
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
import uuid
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Package-level constants
# ---------------------------------------------------------------------------

PACKAGE_VERSION: str = "0.1.0"
PACKAGE_NAME: str = "heap_aliasing"

#: Coordinate namespace prefix shared by all components in this package.
HEAP_COORD_PREFIX: str = "heap"

#: Coordinate prefix specifically for identity-coordinate components.
IDENTITY_COORD_PREFIX: str = "heap.identity"

#: Coordinate prefix for alias-partition components.
ALIAS_COORD_PREFIX: str = "heap.alias"

#: Coordinate prefix for mutation-event components.
MUTATION_COORD_PREFIX: str = "heap.mutation"

#: Coordinate prefix for judgment-output components.
JUDGMENT_COORD_PREFIX: str = "heap.judgment"

#: Human-readable description of the package.
PACKAGE_DESCRIPTION: str = (
    "Sheaf-theoretic heap aliasing analysis for the Python runtime.  "
    "Implements identity-coordinate tracking, alias partition detection, "
    "mutation validation, and aliasing judgment production as described in "
    "theory2.tex Ch17."
)

#: Minimum number of registrations required for the manifest to be valid.
MIN_REGISTRATIONS: int = 1

#: Maximum length for a component description string.
MAX_DESCRIPTION_LEN: int = 512

# ---------------------------------------------------------------------------
# Capability enum
# ---------------------------------------------------------------------------


class Capability(str, Enum):
    """Enumeration of analysis capabilities exposed by ``heap_aliasing``.

    Each value names a distinct analytical service that one or more
    :class:`ComponentRegistration` objects can provide.

    Theory context (theory2.tex Ch17)
    ----------------------------------
    Capabilities map to the layers of the alias-analysis stack:

    * :attr:`HEAP_ANALYSIS` — object enumeration (section construction).
    * :attr:`ALIAS_DETECTION` — partition computation (gluing condition).
    * :attr:`IDENTITY_TRACKING` — coordinate lifetime management.
    * :attr:`MUTATION_VALIDATION` — descent / sheaf condition check.
    * :attr:`ALIASING_JUDGMENT` — evidence-backed judgment production.

    Examples
    --------
    >>> Capability.ALIAS_DETECTION.description()
    'Detects aliasing relationships between heap references by comparing identity coordinates.'
    >>> Capability.HEAP_ANALYSIS.priority()
    10
    >>> Capability.MUTATION_VALIDATION.is_analytical()
    True
    """

    HEAP_ANALYSIS = "heap_analysis"
    ALIAS_DETECTION = "alias_detection"
    IDENTITY_TRACKING = "identity_tracking"
    MUTATION_VALIDATION = "mutation_validation"
    ALIASING_JUDGMENT = "aliasing_judgment"

    # ------------------------------------------------------------------
    # Instance methods
    # ------------------------------------------------------------------

    def description(self) -> str:
        """Return a human-readable description of this capability.

        Returns
        -------
        str
            A sentence describing what this capability does.

        Examples
        --------
        >>> Capability.HEAP_ANALYSIS.description()
        'Enumerates heap objects and assigns identity coordinates to each allocation.'
        """
        _descriptions: dict[str, str] = {
            "heap_analysis": (
                "Enumerates heap objects and assigns identity coordinates to each allocation."
            ),
            "alias_detection": (
                "Detects aliasing relationships between heap references by comparing"
                " identity coordinates."
            ),
            "identity_tracking": (
                "Monitors identity coordinate lifetimes across garbage-collection cycles,"
                " detecting id() reuse."
            ),
            "mutation_validation": (
                "Validates field mutations against the sheaf descent condition, ensuring"
                " global consistency after a local write."
            ),
            "aliasing_judgment": (
                "Produces structured Judgment objects encoding aliasing facts with full"
                " evidence bundles and trust annotations."
            ),
        }
        return _descriptions.get(self.value, f"Capability: {self.value}")

    def is_analytical(self) -> bool:
        """Return whether this capability performs data analysis (vs. bookkeeping).

        Analytical capabilities produce new information (alias sets, judgments)
        while non-analytical capabilities manage metadata (tracking, registration).

        Returns
        -------
        bool
            ``True`` for capabilities that derive new facts; ``False`` for purely
            administrative capabilities.

        Examples
        --------
        >>> Capability.ALIAS_DETECTION.is_analytical()
        True
        >>> Capability.IDENTITY_TRACKING.is_analytical()
        False
        """
        return self in {
            Capability.HEAP_ANALYSIS,
            Capability.ALIAS_DETECTION,
            Capability.MUTATION_VALIDATION,
            Capability.ALIASING_JUDGMENT,
        }

    def priority(self) -> int:
        """Return the execution-order priority for this capability (lower = earlier).

        Components with lower priority values should be initialised first because
        later stages may depend on their outputs.  For example, :attr:`HEAP_ANALYSIS`
        must run before :attr:`ALIAS_DETECTION` can compare identity coordinates.

        Returns
        -------
        int
            Integer priority in the range [1, 100].

        Examples
        --------
        >>> Capability.HEAP_ANALYSIS.priority()
        10
        >>> Capability.ALIASING_JUDGMENT.priority()
        50
        """
        _priorities: dict[str, int] = {
            "heap_analysis": 10,
            "identity_tracking": 15,
            "alias_detection": 25,
            "mutation_validation": 35,
            "aliasing_judgment": 50,
        }
        return _priorities.get(self.value, 99)


# ---------------------------------------------------------------------------
# ComponentRegistration dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ComponentRegistration:
    """Immutable registration record for a single analysis component.

    A *registration* is the *local section* of the manifest sheaf.  It
    describes a component's name, capability, coordinate prefix, and
    operational status.  Together all registrations form the global section
    (the :class:`PackageManifest`).

    Fields
    ------
    name : str
        Unique identifier for the component within this package.
    capability : Capability
        The analytical capability this component provides.
    description : str
        Human-readable description, at most :data:`MAX_DESCRIPTION_LEN` chars.
    coordinate_prefix : str
        The coordinate namespace this component operates within, e.g.
        ``"heap.alias"``.
    enabled : bool
        Whether the component is active.  Disabled components are registered
        but not invoked during analysis passes.
    metadata : dict[str, Any]
        Arbitrary key/value metadata for tooling integration.

    Examples
    --------
    >>> from jugeo.python_runtime.heap_aliasing.manifest import (
    ...     ComponentRegistration, Capability, ALIAS_COORD_PREFIX
    ... )
    >>> reg = ComponentRegistration(
    ...     name="alias_detector_v1",
    ...     capability=Capability.ALIAS_DETECTION,
    ...     description="Union-find alias detector.",
    ...     coordinate_prefix=ALIAS_COORD_PREFIX,
    ...     enabled=True,
    ...     metadata={"version": "1.0"},
    ... )
    >>> reg.is_active()
    True
    >>> reg.to_dict = reg.serialize()
    >>> ComponentRegistration.parse(reg.serialize()).name
    'alias_detector_v1'
    """

    name: str
    capability: Capability
    description: str
    coordinate_prefix: str
    enabled: bool
    metadata: dict[str, Any]

    # ------------------------------------------------------------------
    # Query helpers
    # ------------------------------------------------------------------

    def is_active(self) -> bool:
        """Return whether this registration is currently active.

        A registration is active iff ``enabled`` is ``True``.

        Returns
        -------
        bool
            ``True`` when this component will be invoked during analysis passes.

        Examples
        --------
        >>> reg = ComponentRegistration(
        ...     name="x", capability=Capability.HEAP_ANALYSIS,
        ...     description="", coordinate_prefix="heap",
        ...     enabled=False, metadata={},
        ... )
        >>> reg.is_active()
        False
        """
        return self.enabled

    def with_enabled(self, enabled: bool) -> ComponentRegistration:
        """Return a new registration with the ``enabled`` flag changed.

        Because :class:`ComponentRegistration` is frozen, we use
        :func:`~dataclasses.replace` to produce the mutated copy.

        Parameters
        ----------
        enabled : bool
            The desired enabled state for the new registration.

        Returns
        -------
        ComponentRegistration
            A new instance identical to ``self`` except for ``enabled``.

        Examples
        --------
        >>> reg = ComponentRegistration(
        ...     name="x", capability=Capability.HEAP_ANALYSIS,
        ...     description="", coordinate_prefix="heap",
        ...     enabled=True, metadata={},
        ... )
        >>> reg.with_enabled(False).enabled
        False
        """
        return replace(self, enabled=enabled)

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def serialize(self) -> dict[str, Any]:
        """Serialise this registration to a JSON-compatible dictionary.

        Returns
        -------
        dict[str, Any]
            A plain dictionary suitable for ``json.dumps()``.

        Examples
        --------
        >>> reg = ComponentRegistration(
        ...     name="x", capability=Capability.ALIAS_DETECTION,
        ...     description="test", coordinate_prefix="heap.alias",
        ...     enabled=True, metadata={"k": "v"},
        ... )
        >>> d = reg.serialize()
        >>> d["name"]
        'x'
        >>> d["capability"]
        'alias_detection'
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
    def parse(cls, data: dict[str, Any]) -> ComponentRegistration:
        """Deserialise a registration from a plain dictionary.

        Parameters
        ----------
        data : dict[str, Any]
            Dictionary previously produced by :meth:`serialize`.

        Returns
        -------
        ComponentRegistration
            The reconstructed registration object.

        Raises
        ------
        KeyError
            If a required field is absent from ``data``.
        ValueError
            If the ``capability`` value is not a valid :class:`Capability`.

        Examples
        --------
        >>> d = {
        ...     "name": "x", "capability": "alias_detection",
        ...     "description": "test", "coordinate_prefix": "heap.alias",
        ...     "enabled": True, "metadata": {},
        ... }
        >>> ComponentRegistration.parse(d).name
        'x'
        """
        return cls(
            name=data["name"],
            capability=Capability(data["capability"]),
            description=data.get("description", ""),
            coordinate_prefix=data.get("coordinate_prefix", HEAP_COORD_PREFIX),
            enabled=bool(data.get("enabled", True)),
            metadata=dict(data.get("metadata", {})),
        )


# ---------------------------------------------------------------------------
# PackageManifest dataclass
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class PackageManifest:
    """Mutable registry of all :class:`ComponentRegistration` objects in the package.

    The manifest is the *global section* of the registration sheaf.  It
    aggregates all local component registrations and provides look-up,
    activation/deactivation, and validation services.

    Theory context (theory2.tex Ch17)
    ----------------------------------
    In sheaf language, the manifest is the gluing of all local sections
    (individual component registrations) over the full heap coordinate space.
    The :meth:`validate` method checks that this gluing is consistent — i.e.,
    that no two components claim the same name (no collision in the cover).

    Fields
    ------
    registrations : list[ComponentRegistration]
        Ordered list of registered components.
    version : str
        Semantic version string for the package, e.g. ``"0.1.0"``.
    name : str
        Package identifier, e.g. ``"heap_aliasing"``.
    description : str
        Human-readable package description.
    created_at : float
        Unix timestamp (``time.time()``) when the manifest was created.
    metadata : dict[str, Any]
        Arbitrary package-level metadata.

    Examples
    --------
    >>> from jugeo.python_runtime.heap_aliasing.manifest import build_manifest
    >>> m = build_manifest()
    >>> m.version
    '0.1.0'
    >>> len(m.registrations) >= 5
    True
    >>> m.lookup("heap_analyzer") is not None
    True
    """

    registrations: list[ComponentRegistration]
    version: str
    name: str
    description: str
    created_at: float
    metadata: dict[str, Any]

    # ------------------------------------------------------------------
    # Registration management
    # ------------------------------------------------------------------

    def register(self, reg: ComponentRegistration) -> None:
        """Add a new component registration to the manifest.

        Parameters
        ----------
        reg : ComponentRegistration
            The registration to add.

        Raises
        ------
        ValueError
            If a registration with the same ``name`` already exists.

        Examples
        --------
        >>> m = PackageManifest([], "0.1.0", "heap_aliasing", "", 0.0, {})
        >>> reg = ComponentRegistration(
        ...     name="my_component", capability=Capability.HEAP_ANALYSIS,
        ...     description="A test component.", coordinate_prefix="heap",
        ...     enabled=True, metadata={},
        ... )
        >>> m.register(reg)
        >>> m.lookup("my_component") is not None
        True
        >>> m.register(reg)  # duplicate raises ValueError
        Traceback (most recent call last):
            ...
        ValueError: Component 'my_component' is already registered.
        """
        if any(r.name == reg.name for r in self.registrations):
            raise ValueError(f"Component {reg.name!r} is already registered.")
        self.registrations.append(reg)
        logger.debug("Registered component %r (capability=%s)", reg.name, reg.capability.value)

    def lookup(self, name: str) -> ComponentRegistration | None:
        """Return the registration with the given name, or ``None``.

        Parameters
        ----------
        name : str
            The component name to search for.

        Returns
        -------
        ComponentRegistration | None
            The matching registration, or ``None`` if not found.

        Examples
        --------
        >>> m = build_manifest()
        >>> m.lookup("heap_analyzer").capability
        <Capability.HEAP_ANALYSIS: 'heap_analysis'>
        >>> m.lookup("nonexistent") is None
        True
        """
        for reg in self.registrations:
            if reg.name == name:
                return reg
        return None

    def lookup_by_capability(self, cap: Capability) -> list[ComponentRegistration]:
        """Return all registrations that provide a given capability.

        Parameters
        ----------
        cap : Capability
            The capability to filter by.

        Returns
        -------
        list[ComponentRegistration]
            Possibly-empty list of matching registrations.

        Examples
        --------
        >>> m = build_manifest()
        >>> regs = m.lookup_by_capability(Capability.ALIAS_DETECTION)
        >>> all(r.capability == Capability.ALIAS_DETECTION for r in regs)
        True
        """
        return [r for r in self.registrations if r.capability == cap]

    def enabled_capabilities(self) -> list[Capability]:
        """Return the set of capabilities that have at least one enabled component.

        Returns
        -------
        list[Capability]
            Unique capabilities provided by enabled registrations, sorted by
            :meth:`Capability.priority`.

        Examples
        --------
        >>> m = build_manifest()
        >>> caps = m.enabled_capabilities()
        >>> Capability.HEAP_ANALYSIS in caps
        True
        """
        seen: set[Capability] = set()
        result: list[Capability] = []
        for reg in self.registrations:
            if reg.enabled and reg.capability not in seen:
                seen.add(reg.capability)
                result.append(reg.capability)
        result.sort(key=lambda c: c.priority())
        return result

    def disable(self, name: str) -> bool:
        """Disable the component with the given name.

        Parameters
        ----------
        name : str
            The component name to disable.

        Returns
        -------
        bool
            ``True`` if the component was found and disabled; ``False`` if no
            component with that name exists.

        Examples
        --------
        >>> m = build_manifest()
        >>> m.disable("heap_analyzer")
        True
        >>> m.lookup("heap_analyzer").enabled
        False
        >>> m.disable("nonexistent")
        False
        """
        for idx, reg in enumerate(self.registrations):
            if reg.name == name:
                self.registrations[idx] = reg.with_enabled(False)
                logger.info("Disabled component %r", name)
                return True
        logger.warning("disable(): component %r not found", name)
        return False

    def enable(self, name: str) -> bool:
        """Enable the component with the given name.

        Parameters
        ----------
        name : str
            The component name to enable.

        Returns
        -------
        bool
            ``True`` if the component was found and enabled; ``False`` if no
            component with that name exists.

        Examples
        --------
        >>> m = build_manifest()
        >>> m.disable("heap_analyzer")
        True
        >>> m.enable("heap_analyzer")
        True
        >>> m.lookup("heap_analyzer").enabled
        True
        """
        for idx, reg in enumerate(self.registrations):
            if reg.name == name:
                self.registrations[idx] = reg.with_enabled(True)
                logger.info("Enabled component %r", name)
                return True
        logger.warning("enable(): component %r not found", name)
        return False

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def serialize(self) -> dict[str, Any]:
        """Serialise the manifest to a JSON-compatible dictionary.

        Returns
        -------
        dict[str, Any]
            A plain dictionary with all fields serialised.

        Examples
        --------
        >>> import json
        >>> m = build_manifest()
        >>> d = m.serialize()
        >>> d["version"]
        '0.1.0'
        >>> json.dumps(d)  # must not raise
        '...'
        """
        return {
            "version": self.version,
            "name": self.name,
            "description": self.description,
            "created_at": self.created_at,
            "metadata": dict(self.metadata),
            "registrations": [r.serialize() for r in self.registrations],
        }

    @classmethod
    def parse(cls, data: dict[str, Any]) -> PackageManifest:
        """Deserialise a manifest from a plain dictionary.

        Parameters
        ----------
        data : dict[str, Any]
            Dictionary previously produced by :meth:`serialize`.

        Returns
        -------
        PackageManifest
            The reconstructed manifest.

        Raises
        ------
        KeyError
            If a required top-level field is absent from ``data``.

        Examples
        --------
        >>> m = build_manifest()
        >>> m2 = PackageManifest.parse(m.serialize())
        >>> m2.version
        '0.1.0'
        >>> len(m2.registrations) == len(m.registrations)
        True
        """
        regs = [ComponentRegistration.parse(r) for r in data.get("registrations", [])]
        return cls(
            registrations=regs,
            version=data.get("version", PACKAGE_VERSION),
            name=data.get("name", PACKAGE_NAME),
            description=data.get("description", ""),
            created_at=float(data.get("created_at", 0.0)),
            metadata=dict(data.get("metadata", {})),
        )

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def validate(self) -> list[str]:
        """Validate the manifest for internal consistency.

        Checks performed
        ----------------
        1. At least :data:`MIN_REGISTRATIONS` components are registered.
        2. All component names are unique.
        3. All description strings are within :data:`MAX_DESCRIPTION_LEN` chars.
        4. The ``version`` field is non-empty.
        5. The ``name`` field is non-empty.
        6. All coordinate prefixes start with ``"heap"``.

        Returns
        -------
        list[str]
            A list of error messages.  An empty list means the manifest is
            valid.

        Examples
        --------
        >>> m = build_manifest()
        >>> m.validate()
        []
        >>> bad = PackageManifest([], "", "", "", 0.0, {})
        >>> errors = bad.validate()
        >>> len(errors) > 0
        True
        """
        errors: list[str] = []

        if len(self.registrations) < MIN_REGISTRATIONS:
            errors.append(
                f"Manifest has {len(self.registrations)} registrations;"
                f" minimum is {MIN_REGISTRATIONS}."
            )

        if not self.version:
            errors.append("Manifest 'version' field is empty.")

        if not self.name:
            errors.append("Manifest 'name' field is empty.")

        # Check name uniqueness
        seen_names: set[str] = set()
        for reg in self.registrations:
            if reg.name in seen_names:
                errors.append(f"Duplicate component name: {reg.name!r}.")
            seen_names.add(reg.name)

        # Check description length
        for reg in self.registrations:
            if len(reg.description) > MAX_DESCRIPTION_LEN:
                errors.append(
                    f"Component {reg.name!r} description exceeds {MAX_DESCRIPTION_LEN} chars."
                )

        # Check coordinate prefix convention
        for reg in self.registrations:
            if not reg.coordinate_prefix.startswith(HEAP_COORD_PREFIX):
                errors.append(
                    f"Component {reg.name!r} coordinate_prefix {reg.coordinate_prefix!r}"
                    f" does not start with {HEAP_COORD_PREFIX!r}."
                )

        return errors

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------

    def summary(self) -> str:
        """Return a concise human-readable summary of the manifest.

        Returns
        -------
        str
            Multi-line string summarising the manifest state.

        Examples
        --------
        >>> m = build_manifest()
        >>> "heap_aliasing" in m.summary()
        True
        """
        enabled_count = sum(1 for r in self.registrations if r.enabled)
        total_count = len(self.registrations)
        caps = ", ".join(c.value for c in self.enabled_capabilities())
        lines = [
            f"Package {self.name} v{self.version}",
            f"  Description : {self.description[:80]}",
            f"  Components  : {enabled_count}/{total_count} enabled",
            f"  Capabilities: {caps or '(none)'}",
        ]
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def _make_registration(
    name: str,
    capability: Capability,
    description: str,
    coordinate_prefix: str,
    *,
    enabled: bool = True,
    metadata: dict[str, Any] | None = None,
) -> ComponentRegistration:
    """Convenience factory for creating :class:`ComponentRegistration` objects.

    Parameters
    ----------
    name : str
        Unique component name.
    capability : Capability
        The capability provided by the component.
    description : str
        Human-readable description.
    coordinate_prefix : str
        Coordinate namespace prefix.
    enabled : bool, optional
        Whether the component starts enabled.  Defaults to ``True``.
    metadata : dict[str, Any] | None, optional
        Metadata dictionary.  Defaults to an empty dict.

    Returns
    -------
    ComponentRegistration
        A fully populated registration object.
    """
    return ComponentRegistration(
        name=name,
        capability=capability,
        description=description,
        coordinate_prefix=coordinate_prefix,
        enabled=enabled,
        metadata=metadata or {},
    )


def _compute_manifest_fingerprint(manifest: PackageManifest) -> str:
    """Compute a SHA-256 fingerprint of the serialised manifest.

    This is used for cache-invalidation and change-detection in the copilot
    integration pipeline.

    Parameters
    ----------
    manifest : PackageManifest
        The manifest to fingerprint.

    Returns
    -------
    str
        A 16-character hex string (first 16 chars of SHA-256).

    Examples
    --------
    >>> m = build_manifest()
    >>> fp = _compute_manifest_fingerprint(m)
    >>> len(fp) == 16
    True
    """
    raw = json.dumps(manifest.serialize(), sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _registration_table(manifest: PackageManifest) -> str:
    """Format a fixed-width table of all registrations for logging/display.

    Parameters
    ----------
    manifest : PackageManifest
        The manifest whose registrations to tabulate.

    Returns
    -------
    str
        A multi-line ASCII table.
    """
    header = f"{'NAME':<30} {'CAPABILITY':<22} {'ENABLED':<8} {'PREFIX'}"
    separator = "-" * 80
    rows = [header, separator]
    for reg in manifest.registrations:
        rows.append(
            f"{reg.name:<30} {reg.capability.value:<22} {str(reg.enabled):<8}"
            f" {reg.coordinate_prefix}"
        )
    return "\n".join(rows)


# ---------------------------------------------------------------------------
# build_manifest factory
# ---------------------------------------------------------------------------


def build_manifest() -> PackageManifest:
    """Create and populate the canonical :data:`MANIFEST` singleton.

    This factory registers all five core components of the ``heap_aliasing``
    package and returns a fully validated :class:`PackageManifest`.

    The registrations follow the dependency order implied by
    :meth:`Capability.priority`: heap analysis runs first, alias detection
    second, mutation validation third, and judgment production last.

    Returns
    -------
    PackageManifest
        A populated, valid manifest for the ``heap_aliasing`` package.

    Examples
    --------
    >>> m = build_manifest()
    >>> m.validate()
    []
    >>> len(m.registrations)
    5

    Notes
    -----
    - This function is called exactly once at module import time to produce
      the :data:`MANIFEST` singleton.
    - Copilot integration: the manifest fingerprint is logged at DEBUG level
      so the CI pipeline can detect unexpected changes.
    """
    manifest = PackageManifest(
        registrations=[],
        version=PACKAGE_VERSION,
        name=PACKAGE_NAME,
        description=PACKAGE_DESCRIPTION,
        created_at=time.time(),
        metadata={
            "theory_chapter": "theory2.tex Ch17",
            "copilot_skill": "heap_aliasing",
            "schema_version": "1",
        },
    )

    # --- Component 1: Heap Analyzer -----------------------------------------
    manifest.register(
        _make_registration(
            name="heap_analyzer",
            capability=Capability.HEAP_ANALYSIS,
            description=(
                "Enumerates live Python objects via gc.get_objects(), constructs an"
                " IdentityCoordinate for each allocation, and writes HeapObject sections"
                " into the coordinate space.  Runs as the first pass in every analysis cycle."
            ),
            coordinate_prefix=HEAP_COORD_PREFIX,
            metadata={
                "author": "jugeo-team",
                "theory_section": "Ch17 §1",
                "uses_gc": True,
            },
        )
    )

    # --- Component 2: Alias Detector ----------------------------------------
    manifest.register(
        _make_registration(
            name="alias_detector",
            capability=Capability.ALIAS_DETECTION,
            description=(
                "Builds alias partitions (union-find equivalence classes) from the heap"
                " object map.  Two references alias iff their IdentityCoordinates match;"
                " this implements the shared-support criterion from theory2.tex Ch17 §2."
            ),
            coordinate_prefix=ALIAS_COORD_PREFIX,
            metadata={
                "author": "jugeo-team",
                "theory_section": "Ch17 §2",
                "algorithm": "union-find",
            },
        )
    )

    # --- Component 3: Identity Tracker --------------------------------------
    manifest.register(
        _make_registration(
            name="identity_tracker",
            capability=Capability.IDENTITY_TRACKING,
            description=(
                "Registers gc callbacks to detect when an identity coordinate (id()) is"
                " recycled after garbage collection, invalidating any cached alias facts"
                " that reference the old object."
            ),
            coordinate_prefix=IDENTITY_COORD_PREFIX,
            metadata={
                "author": "jugeo-team",
                "theory_section": "Ch17 §3",
                "uses_gc_callbacks": True,
            },
        )
    )

    # --- Component 4: Mutation Validator ------------------------------------
    manifest.register(
        _make_registration(
            name="mutation_validator",
            capability=Capability.MUTATION_VALIDATION,
            description=(
                "Intercepts __setattr__ / __setitem__ calls and applies the descent check"
                " (sheaf condition): the new section value at the mutated field must be"
                " consistent with all alias observers, i.e., every reference in the alias"
                " partition must see the same updated value."
            ),
            coordinate_prefix=MUTATION_COORD_PREFIX,
            metadata={
                "author": "jugeo-team",
                "theory_section": "Ch17 §4",
                "uses_sys_settrace": False,
                "hook_mechanism": "__setattr__",
            },
        )
    )

    # --- Component 5: Aliasing Judgment Producer ----------------------------
    manifest.register(
        _make_registration(
            name="aliasing_judgment_producer",
            capability=Capability.ALIASING_JUDGMENT,
            description=(
                "Wraps aliasing facts detected by the alias_detector into structured"
                " Judgment objects with EvidenceBundle, TrustAnnotation, and Provenance"
                " fields.  Outputs are consumed by the broader jugeo judgment pipeline."
            ),
            coordinate_prefix=JUDGMENT_COORD_PREFIX,
            metadata={
                "author": "jugeo-team",
                "theory_section": "Ch17 §5",
                "output_kind": "Judgment",
            },
        )
    )

    # Log fingerprint for copilot integration CI checks.
    fp = _compute_manifest_fingerprint(manifest)
    logger.debug("heap_aliasing manifest built (fingerprint=%s)", fp)

    errors = manifest.validate()
    if errors:
        # Log but don't raise — callers may be in import context.
        for err in errors:
            logger.error("Manifest validation error: %s", err)

    return manifest


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

#: The canonical package manifest singleton.  Import and use this object
#: rather than calling :func:`build_manifest` directly.
MANIFEST: PackageManifest = build_manifest()

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    "PACKAGE_VERSION",
    "PACKAGE_NAME",
    "HEAP_COORD_PREFIX",
    "IDENTITY_COORD_PREFIX",
    "ALIAS_COORD_PREFIX",
    "MUTATION_COORD_PREFIX",
    "JUDGMENT_COORD_PREFIX",
    "PACKAGE_DESCRIPTION",
    "MIN_REGISTRATIONS",
    "MAX_DESCRIPTION_LEN",
    "Capability",
    "ComponentRegistration",
    "PackageManifest",
    "build_manifest",
    "MANIFEST",
]
