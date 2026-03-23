r"""Registry, versioning, and public API surface for the JuGeo IR stack.

This module provides the manifest infrastructure described in Chapter 32 of
``theory2.tex`` — *Internal Representations and the IR Stack* — specifically
the package-level catalogue of components, capability flags, and versioned
export descriptors that allow the copilot oracle and downstream consumers to
discover what the IR stack package provides without importing every submodule.

Architecture
------------

A :class:`ManifestRegistry` owns a collection of :class:`IRStackManifest`
objects, each of which describes a concrete version of the IR stack package.
Each manifest records the full set of :class:`ComponentDescriptor` objects
present in that version and the union of :class:`CapabilityFlag` values they
provide.

.. math::

   \mathcal{M} = \bigl(\mathrm{id},\; V,\; \{C_i\}_{i \in I},\; F\bigr)

where :math:`V` is a :class:`PackageVersion`, :math:`C_i` is a
:class:`ComponentDescriptor`, and :math:`F \subseteq \mathrm{CapabilityFlag}`
is the capability set:

.. math::

   F = \bigcup_{i \in I} F_{C_i}

A manifest is *valid* when the dependency closure of every component
:math:`C_i` is also present in the manifest:

.. math::

   \forall i \in I,\; \forall d \in \mathrm{deps}(C_i) :\; d \in \{C_j.\mathrm{name}\}_{j \in I}

Checksums are computed as SHA-256 over the canonical JSON serialisation of the
manifest so that integrity can be verified at load time:

.. math::

   \sigma(\mathcal{M}) = \mathrm{SHA256}\!\left(\mathrm{JSON}(\mathcal{M})\right)

Theory alignment
~~~~~~~~~~~~~~~~

* §32.1 — IR package structure and versioning contract
* §32.2 — Component taxonomy and capability flags
* §32.3 — Manifest registry and default resolution
* §32.4 — API surface and export descriptors
"""

from __future__ import annotations

import collections
import hashlib
import json
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

try:
    from jugeo.solver.z3_session import Z3Session, Z3Formula, Z3Encoder  # type: ignore[import]
except Exception:  # pragma: no cover
    class Z3Session:  # type: ignore[no-redef]
        pass
    class Z3Formula:  # type: ignore[no-redef]
        pass
    class Z3Encoder:  # type: ignore[no-redef]
        pass

try:
    from jugeo.evidence.trust import TrustAlgebra, TrustLevel  # type: ignore[import]
except Exception:  # pragma: no cover
    class TrustAlgebra:  # type: ignore[no-redef]
        pass
    class TrustLevel:  # type: ignore[no-redef]
        pass


# ===================================================================== #
# 1. Enumerations                                                        #
# ===================================================================== #


class PackageVersion(str, Enum):
    """Semantic version tag for a release of the IR stack package.

    Versions are ordered: V1_0 < V1_1 < V2_0 < EXPERIMENTAL.  The
    ``is_stable()`` method distinguishes production versions from
    pre-release work.
    """

    V1_0 = "1.0"
    V1_1 = "1.1"
    V2_0 = "2.0"
    EXPERIMENTAL = "experimental"

    # ------------------------------------------------------------------
    def is_stable(self) -> bool:
        """Return ``True`` if this version is considered production-stable.

        EXPERIMENTAL is never stable; all numeric versions are.

        :returns: ``True`` for V1_0, V1_1, V2_0; ``False`` for EXPERIMENTAL.
        """
        return self != PackageVersion.EXPERIMENTAL

    def successor(self) -> PackageVersion:
        """Return the next version in the release sequence.

        The successor of V2_0 and EXPERIMENTAL is EXPERIMENTAL (there is
        no successor beyond the development branch).

        :returns: The next :class:`PackageVersion` in the chain.
        """
        _chain: dict[str, PackageVersion] = {
            "1.0": PackageVersion.V1_1,
            "1.1": PackageVersion.V2_0,
            "2.0": PackageVersion.EXPERIMENTAL,
            "experimental": PackageVersion.EXPERIMENTAL,
        }
        return _chain[self.value]

    def ordinal(self) -> int:
        """Return a numeric rank used for version comparisons.

        :returns: Integer rank (0 = oldest, 3 = newest/experimental).
        """
        _ranks: dict[str, int] = {
            "1.0": 0,
            "1.1": 1,
            "2.0": 2,
            "experimental": 3,
        }
        return _ranks[self.value]

    def display_label(self) -> str:
        """Return a short label for UI, logs, and manifest summaries.

        :returns: A concise human-readable string, e.g. ``"v1.1"`` or
            ``"exp"``.
        """
        _labels: dict[str, str] = {
            "1.0": "v1.0",
            "1.1": "v1.1",
            "2.0": "v2.0",
            "experimental": "exp",
        }
        return _labels.get(self.value, self.value)


class ComponentStatus(str, Enum):
    """Lifecycle status of a registered IR stack component.

    Components transition through STUB → ACTIVE; deprecated components
    move from ACTIVE → DEPRECATED.  EXPERIMENTAL components may be
    promoted to ACTIVE or removed entirely.
    """

    ACTIVE = "active"
    DEPRECATED = "deprecated"
    EXPERIMENTAL = "experimental"
    STUB = "stub"

    # ------------------------------------------------------------------
    def is_usable(self) -> bool:
        """Return ``True`` when the component may be imported by consumers.

        DEPRECATED components remain usable (with a warning) but STUB
        components are placeholders without callable implementations.

        :returns: ``True`` for ACTIVE, DEPRECATED, EXPERIMENTAL.
        """
        return self in (
            ComponentStatus.ACTIVE,
            ComponentStatus.DEPRECATED,
            ComponentStatus.EXPERIMENTAL,
        )

    def display_label(self) -> str:
        """Return a short label for dashboards and CI reports.

        :returns: A short uppercase string, e.g. ``"ACT"`` or ``"DEPR"``.
        """
        _labels: dict[str, str] = {
            "active": "ACT",
            "deprecated": "DEPR",
            "experimental": "EXP",
            "stub": "STUB",
        }
        return _labels.get(self.value, self.value.upper()[:4])

    def severity_order(self) -> int:
        """Return a numeric severity level for sorting component health.

        Lower numbers are healthier.  STUB is most severe because it
        indicates missing implementation.

        :returns: Integer from 0 (healthy) to 3 (unimplemented).
        """
        _severity: dict[str, int] = {
            "active": 0,
            "experimental": 1,
            "deprecated": 2,
            "stub": 3,
        }
        return _severity.get(self.value, 99)


class CapabilityFlag(str, Enum):
    """Coarse-grained capability tags advertised by IR stack components.

    Capability flags are collected at manifest level so that the copilot
    oracle and downstream consumers can query for required features before
    attempting to use a component.
    """

    AMBIGUITY_TRACKING = "ambiguity_tracking"
    NORMAL_FORMS = "normal_forms"
    LOWERING_PASSES = "lowering_passes"
    Z3_INTEGRATION = "z3_integration"
    COPILOT_ASSIST = "copilot_assist"
    CACHE_ENABLED = "cache_enabled"

    # ------------------------------------------------------------------
    def requires_z3(self) -> bool:
        """Return ``True`` when this capability requires a live Z3 session.

        Only :attr:`Z3_INTEGRATION` has this requirement; all other
        capabilities are self-contained within the IR stack.

        :returns: ``True`` for Z3_INTEGRATION, ``False`` otherwise.
        """
        return self == CapabilityFlag.Z3_INTEGRATION

    def display_label(self) -> str:
        """Return a short human-readable label suitable for capability matrices.

        :returns: A concise string, e.g. ``"AmbT"`` or ``"Z3"`` .
        """
        _labels: dict[str, str] = {
            "ambiguity_tracking": "AmbT",
            "normal_forms": "NF",
            "lowering_passes": "Low",
            "z3_integration": "Z3",
            "copilot_assist": "Cop",
            "cache_enabled": "Cache",
        }
        return _labels.get(self.value, self.value[:5].upper())

    def is_optional(self) -> bool:
        """Return ``True`` if this capability may be absent in minimal builds.

        CACHE_ENABLED and COPILOT_ASSIST are optional; all other flags
        represent core IR stack functionality.

        :returns: ``True`` for CACHE_ENABLED and COPILOT_ASSIST.
        """
        return self in (CapabilityFlag.CACHE_ENABLED, CapabilityFlag.COPILOT_ASSIST)


# ===================================================================== #
# 2. ComponentDescriptor                                                 #
# ===================================================================== #


@dataclass
class ComponentDescriptor:
    """Structured description of a single IR stack component.

    A :class:`ComponentDescriptor` records the metadata that the manifest
    registry uses to validate, query, and export information about one
    importable module or class within the IR stack package.

    Attributes
    ----------
    name:
        Unique short name used as the registry key, e.g. ``"models"`` or
        ``"ir_nodes"``.
    version:
        The earliest :class:`PackageVersion` in which this component
        appeared.
    status:
        Current :class:`ComponentStatus` indicating usability.
    capabilities:
        List of :class:`CapabilityFlag` values this component provides.
    dependencies:
        List of component names (by ``name`` field) that must be present
        for this component to function correctly.
    description:
        One-sentence prose description used in generated docs and reports.
    """

    name: str
    version: PackageVersion
    status: ComponentStatus
    capabilities: list[CapabilityFlag] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)
    description: str = ""

    # ------------------------------------------------------------------
    def has_capability(self, flag: CapabilityFlag) -> bool:
        """Return ``True`` if *flag* is advertised by this component.

        :param flag: The :class:`CapabilityFlag` to test for.
        :returns: ``True`` when *flag* appears in ``capabilities``.
        """
        return flag in self.capabilities

    def is_compatible_with(self, other: ComponentDescriptor) -> bool:
        """Return ``True`` if this component can coexist with *other*.

        Two components are compatible when neither declares the other as
        conflicting (currently modelled as: no circular dependency where
        *other* names *self* and *self* names *other*).

        :param other: Another :class:`ComponentDescriptor` to check against.
        :returns: ``True`` when no circular dependency exists.
        """
        self_deps_other = other.name in self.dependencies
        other_deps_self = self.name in other.dependencies
        return not (self_deps_other and other_deps_self)

    def to_dict(self) -> dict[str, Any]:
        """Serialise this descriptor to a plain JSON-compatible dictionary.

        :returns: A dictionary with string values for all enum fields.
        """
        return {
            "name": self.name,
            "version": self.version.value,
            "status": self.status.value,
            "capabilities": [c.value for c in self.capabilities],
            "dependencies": list(self.dependencies),
            "description": self.description,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ComponentDescriptor:
        """Reconstruct a :class:`ComponentDescriptor` from a dictionary.

        :param data: A dictionary as produced by :meth:`to_dict`.
        :returns: A reconstructed :class:`ComponentDescriptor`.
        :raises KeyError: If required keys are absent from *data*.
        """
        return cls(
            name=data["name"],
            version=PackageVersion(data["version"]),
            status=ComponentStatus(data["status"]),
            capabilities=[CapabilityFlag(c) for c in data.get("capabilities", [])],
            dependencies=list(data.get("dependencies", [])),
            description=data.get("description", ""),
        )

    def validate(self) -> list[str]:
        """Return a list of validation error strings for this descriptor.

        An empty list means the descriptor is well-formed.  Checks
        performed:

        * ``name`` must be non-empty.
        * ``description`` should be present for ACTIVE components.
        * STUB components should have no capabilities advertised.

        :returns: List of error strings; empty if valid.
        """
        errors: list[str] = []
        if not self.name.strip():
            errors.append("ComponentDescriptor.name must not be empty.")
        if self.status == ComponentStatus.ACTIVE and not self.description.strip():
            errors.append(
                f"Component '{self.name}' is ACTIVE but has no description."
            )
        if self.status == ComponentStatus.STUB and self.capabilities:
            cap_names = [c.value for c in self.capabilities]
            errors.append(
                f"Component '{self.name}' is a STUB but advertises "
                f"capabilities: {cap_names}."
            )
        return errors


# ===================================================================== #
# 3. IRStackManifest                                                     #
# ===================================================================== #


@dataclass
class IRStackManifest:
    """Top-level manifest object for a versioned IR stack package.

    An :class:`IRStackManifest` aggregates all :class:`ComponentDescriptor`
    objects in a particular release and exposes query helpers for the
    capability matrix, dependency validation, and cross-manifest merging.

    Attributes
    ----------
    manifest_id:
        Unique identifier auto-generated at construction time.
    package_name:
        Fully qualified Python package path, e.g.
        ``"jugeo.encodings.ir_stack"``.
    package_version:
        The :class:`PackageVersion` this manifest describes.
    components:
        Mapping from component name to its :class:`ComponentDescriptor`.
    created_at:
        Unix timestamp recorded when the manifest was constructed.
    capabilities:
        Union of all capability flags across all registered components.
    """

    manifest_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    package_name: str = "jugeo.encodings.ir_stack"
    package_version: PackageVersion = PackageVersion.V2_0
    components: dict[str, ComponentDescriptor] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    capabilities: set[CapabilityFlag] = field(default_factory=set)

    # ------------------------------------------------------------------
    def register_component(self, descriptor: ComponentDescriptor) -> None:
        """Add *descriptor* to this manifest's component registry.

        If a component with the same name already exists it is replaced.
        The manifest's capability set is updated to include the new
        component's capabilities.

        :param descriptor: The :class:`ComponentDescriptor` to register.
        """
        self.components[descriptor.name] = descriptor
        for cap in descriptor.capabilities:
            self.capabilities.add(cap)

    def lookup_component(self, name: str) -> ComponentDescriptor | None:
        """Return the :class:`ComponentDescriptor` for *name*, or ``None``.

        :param name: The component name key.
        :returns: The matching descriptor, or ``None`` if not found.
        """
        return self.components.get(name)

    def list_components(
        self,
        status_filter: ComponentStatus | None = None,
    ) -> list[ComponentDescriptor]:
        """Return all registered components, optionally filtered by status.

        :param status_filter: When given, only components with this
            :class:`ComponentStatus` are returned.
        :returns: A list of :class:`ComponentDescriptor` objects.
        """
        comps = list(self.components.values())
        if status_filter is not None:
            comps = [c for c in comps if c.status == status_filter]
        return sorted(comps, key=lambda c: c.name)

    def validate_dependencies(self) -> list[str]:
        """Check that all declared dependencies are satisfied in the manifest.

        For each component, every name in its ``dependencies`` list must
        refer to a component registered in this manifest.

        :returns: A list of error strings; empty if all deps are satisfied.
        """
        errors: list[str] = []
        for comp in self.components.values():
            for dep_name in comp.dependencies:
                if dep_name not in self.components:
                    errors.append(
                        f"Component '{comp.name}' depends on '{dep_name}' "
                        f"which is not registered in this manifest."
                    )
        return errors

    def to_dict(self) -> dict[str, Any]:
        """Serialise the manifest to a JSON-compatible dictionary.

        :returns: A nested dictionary representation.
        """
        return {
            "manifest_id": self.manifest_id,
            "package_name": self.package_name,
            "package_version": self.package_version.value,
            "components": {
                name: comp.to_dict()
                for name, comp in self.components.items()
            },
            "created_at": self.created_at,
            "capabilities": sorted(c.value for c in self.capabilities),
        }

    def serialize(self) -> str:
        """Return a canonical JSON string for this manifest.

        Keys are sorted so that the output is deterministic and suitable
        for hashing.

        :returns: A JSON-serialised string with sorted keys.
        """
        return json.dumps(self.to_dict(), sort_keys=True)

    def compute_checksum(self) -> str:
        """Compute the SHA-256 checksum of the canonical serialisation.

        :returns: A 64-character lowercase hex string.
        """
        raw = self.serialize().encode("utf-8")
        return hashlib.sha256(raw).hexdigest()

    def get_capability_matrix(self) -> dict[str, list[str]]:
        """Return a mapping from component name to its capability labels.

        Useful for generating documentation tables and CI coverage reports.

        :returns: A dictionary keyed by component name with lists of
            capability display labels.
        """
        matrix: dict[str, list[str]] = {}
        for name, comp in sorted(self.components.items()):
            matrix[name] = [cap.display_label() for cap in comp.capabilities]
        return matrix

    def merge_manifest(self, other: IRStackManifest) -> IRStackManifest:
        """Return a new manifest combining *self* and *other*.

        Components from *other* override those in *self* when the same name
        is present.  The resulting manifest uses *other*'s version and a
        fresh manifest ID.

        :param other: The :class:`IRStackManifest` to merge in.
        :returns: A new merged :class:`IRStackManifest`.
        """
        merged_components: dict[str, ComponentDescriptor] = dict(self.components)
        merged_components.update(other.components)
        merged_caps: set[CapabilityFlag] = set(self.capabilities) | set(other.capabilities)
        return IRStackManifest(
            manifest_id=str(uuid.uuid4()),
            package_name=other.package_name,
            package_version=other.package_version,
            components=merged_components,
            created_at=time.time(),
            capabilities=merged_caps,
        )

    def validate(self) -> list[str]:
        """Run full validation of this manifest.

        Combines component-level validation errors and dependency checks.

        :returns: A flat list of all error strings; empty if valid.
        """
        errors: list[str] = []
        for comp in self.components.values():
            errors.extend(comp.validate())
        errors.extend(self.validate_dependencies())
        return errors


# ===================================================================== #
# 4. APIExport                                                           #
# ===================================================================== #


@dataclass
class APIExport:
    """Describes a single exported symbol from the IR stack package.

    An :class:`APIExport` record is used by the manifest registry to track
    the public surface of the package, annotate deprecated symbols, and
    guide the copilot oracle when suggesting imports.

    Attributes
    ----------
    export_id:
        Unique identifier for this export record.
    symbol_name:
        The bare symbol name, e.g. ``"IRNode"``.
    symbol_type:
        A string classifying the symbol: ``"class"``, ``"function"``,
        ``"constant"``, or ``"module"``.
    module_path:
        Dotted module path where the symbol lives, e.g.
        ``"jugeo.encodings.ir_stack.models"``.
    is_public:
        ``True`` when the symbol is part of the documented public API.
    deprecated:
        ``True`` when the symbol is kept for backwards compatibility only.
    replacement:
        For deprecated symbols, the recommended replacement symbol name.
    """

    export_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    symbol_name: str = ""
    symbol_type: str = "class"
    module_path: str = ""
    is_public: bool = True
    deprecated: bool = False
    replacement: str | None = None

    # ------------------------------------------------------------------
    def full_path(self) -> str:
        """Return the fully qualified import path for this symbol.

        :returns: A dotted path string, e.g.
            ``"jugeo.encodings.ir_stack.models.IRNode"``.
        """
        if self.module_path:
            return f"{self.module_path}.{self.symbol_name}"
        return self.symbol_name

    def to_dict(self) -> dict[str, Any]:
        """Serialise this export record to a plain dictionary.

        :returns: A JSON-serializable dictionary.
        """
        return {
            "export_id": self.export_id,
            "symbol_name": self.symbol_name,
            "symbol_type": self.symbol_type,
            "module_path": self.module_path,
            "full_path": self.full_path(),
            "is_public": self.is_public,
            "deprecated": self.deprecated,
            "replacement": self.replacement,
        }

    def is_available(self) -> bool:
        """Return ``True`` when this symbol is safe to import and use.

        A symbol is available when it is public and not deprecated with no
        replacement (which would indicate it is scheduled for removal).

        :returns: ``True`` for all public symbols including deprecated ones
            that have a ``replacement`` specified.
        """
        if not self.is_public:
            return False
        if self.deprecated and self.replacement is None:
            return False
        return True

    def deprecation_notice(self) -> str:
        """Return a human-readable deprecation notice string.

        Returns an empty string when the symbol is not deprecated.

        :returns: A descriptive notice, or ``""`` if not deprecated.
        """
        if not self.deprecated:
            return ""
        base = f"'{self.symbol_name}' is deprecated."
        if self.replacement:
            return f"{base} Use '{self.replacement}' instead."
        return f"{base} No replacement is available; this symbol will be removed."


# ===================================================================== #
# 5. ManifestRegistry                                                    #
# ===================================================================== #

# copilot: ManifestRegistry assists copilot oracle in discovering available IR capabilities


@dataclass
class ManifestRegistry:
    """Global registry mapping manifest IDs to :class:`IRStackManifest` objects.

    The registry provides a single point of truth for all known IR stack
    manifests.  It supports lookup by manifest ID, bulk validation, default
    manifest resolution, and aggregate export.

    Attributes
    ----------
    _registry:
        Internal mapping from manifest ID to :class:`IRStackManifest`.
    _default_manifest:
        The manifest to use when no specific ID is requested.
    """

    _registry: dict[str, IRStackManifest] = field(default_factory=dict)
    _default_manifest: IRStackManifest | None = field(default=None)

    # ------------------------------------------------------------------
    def register(self, manifest: IRStackManifest) -> None:
        """Add *manifest* to the registry under its ``manifest_id``.

        If a manifest with the same ID already exists, it is silently
        replaced.

        :param manifest: The :class:`IRStackManifest` to register.
        """
        self._registry[manifest.manifest_id] = manifest
        if self._default_manifest is None:
            self._default_manifest = manifest

    def get(self, manifest_id: str) -> IRStackManifest | None:
        """Return the manifest with the given *manifest_id*, or ``None``.

        :param manifest_id: The UUID string key for the manifest.
        :returns: The matching :class:`IRStackManifest`, or ``None``.
        """
        return self._registry.get(manifest_id)

    def list_all(self) -> list[IRStackManifest]:
        """Return all registered manifests sorted by creation time.

        :returns: A list of :class:`IRStackManifest` objects, oldest first.
        """
        return sorted(
            self._registry.values(),
            key=lambda m: m.created_at,
        )

    def validate_all(self) -> dict[str, list[str]]:
        """Validate every registered manifest and return error maps.

        :returns: A dictionary keyed by ``manifest_id`` with lists of
            validation error strings.  Only manifests with errors are
            included in the result.
        """
        results: dict[str, list[str]] = {}
        for mid, manifest in self._registry.items():
            errors = manifest.validate()
            if errors:
                results[mid] = errors
        return results

    def get_default(self) -> IRStackManifest | None:
        """Return the default manifest, or ``None`` if the registry is empty.

        :returns: The :class:`IRStackManifest` set as default.
        """
        return self._default_manifest

    def set_default(self, manifest_id: str) -> bool:
        """Promote the manifest with *manifest_id* to the default.

        :param manifest_id: The ID of the manifest to set as default.
        :returns: ``True`` if the manifest was found and set; ``False``
            otherwise.
        """
        manifest = self._registry.get(manifest_id)
        if manifest is None:
            return False
        self._default_manifest = manifest
        return True

    def export_all(self) -> dict[str, Any]:
        """Return a full JSON-compatible export of all registered manifests.

        The result includes a ``"checksum_map"`` sub-key that maps each
        manifest ID to its SHA-256 checksum.

        :returns: A nested dictionary suitable for ``json.dumps``.
        """
        export: dict[str, Any] = {
            "registry_size": len(self._registry),
            "default_manifest_id": (
                self._default_manifest.manifest_id
                if self._default_manifest is not None
                else None
            ),
            "manifests": {
                mid: manifest.to_dict()
                for mid, manifest in self._registry.items()
            },
            "checksum_map": {
                mid: manifest.compute_checksum()
                for mid, manifest in self._registry.items()
            },
        }
        return export

    def find_by_capability(self, flag: CapabilityFlag) -> list[IRStackManifest]:
        """Return all manifests that advertise *flag* in their capability set.

        :param flag: The :class:`CapabilityFlag` to search for.
        :returns: A list of matching :class:`IRStackManifest` objects.
        """
        return [
            m for m in self._registry.values()
            if flag in m.capabilities
        ]

    def summary(self) -> dict[str, Any]:
        """Return a concise summary of registry contents.

        Useful for health-check endpoints and CI manifest audits.

        :returns: A dictionary with component counts, capability coverage,
            and version distribution.
        """
        version_counts: dict[str, int] = collections.Counter(
            m.package_version.value for m in self._registry.values()
        )  # type: ignore[assignment]
        all_caps: set[str] = set()
        for m in self._registry.values():
            all_caps.update(c.value for c in m.capabilities)
        return {
            "total_manifests": len(self._registry),
            "version_distribution": dict(version_counts),
            "unique_capabilities": sorted(all_caps),
        }


# ===================================================================== #
# 6. Module-level helpers and canonical manifest construction           #
# ===================================================================== #

_global_registry: ManifestRegistry = ManifestRegistry()


def get_default_manifest() -> IRStackManifest:
    """Return the default registered manifest, building it if necessary.

    If no manifest has been registered yet, :func:`build_ir_stack_manifest`
    is called automatically and its result is registered and returned.

    :returns: The default :class:`IRStackManifest`.
    """
    default = _global_registry.get_default()
    if default is None:
        manifest = build_ir_stack_manifest()
        _global_registry.register(manifest)
        return manifest
    return default


def register_component(name: str, descriptor: ComponentDescriptor) -> None:
    """Register *descriptor* into the default manifest under *name*.

    If the default manifest does not yet exist it is created on demand.

    :param name: The component name key.
    :param descriptor: The :class:`ComponentDescriptor` to register.
    """
    manifest = get_default_manifest()
    manifest.register_component(descriptor)


def list_capabilities() -> list[CapabilityFlag]:
    """Return the sorted union of capabilities across all registered manifests.

    :returns: A deduplicated, value-sorted list of :class:`CapabilityFlag`.
    """
    all_caps: set[CapabilityFlag] = set()
    for manifest in _global_registry.list_all():
        all_caps.update(manifest.capabilities)
    return sorted(all_caps, key=lambda c: c.value)


def build_ir_stack_manifest() -> IRStackManifest:
    """Construct the canonical :class:`IRStackManifest` for this package.

    This function is the single authoritative source that lists every
    component in ``jugeo.encodings.ir_stack`` with its capabilities and
    dependencies.  It is called automatically by :func:`get_default_manifest`
    and may be called explicitly to obtain a fresh manifest.

    The components registered correspond to the modules defined in the
    package:

    * ``models``        — core data structures (IRNode, IRLayer, IRStack, …)
    * ``ir_nodes``  — node taxonomy, payloads, traversal, substitution
    * ``ir_layers`` — layer construction, constraint propagation
    * ``manifest``      — registry and API surface (this module)

    :returns: A fully populated :class:`IRStackManifest`.
    """
    manifest = IRStackManifest(
        manifest_id=str(uuid.uuid4()),
        package_name="jugeo.encodings.ir_stack",
        package_version=PackageVersion.V2_0,
        created_at=time.time(),
    )

    models_desc = ComponentDescriptor(
        name="models",
        version=PackageVersion.V1_0,
        status=ComponentStatus.ACTIVE,
        capabilities=[
            CapabilityFlag.AMBIGUITY_TRACKING,
            CapabilityFlag.NORMAL_FORMS,
            CapabilityFlag.LOWERING_PASSES,
        ],
        dependencies=[],
        description=(
            "Core data structures: IRNode, IRLayer, IRStack, AmbiguityMark, "
            "LoweringPass, NormalForm."
        ),
    )

    desc = ComponentDescriptor(
        name="ir_nodes",
        version=PackageVersion.V1_1,
        status=ComponentStatus.ACTIVE,
        capabilities=[
            CapabilityFlag.AMBIGUITY_TRACKING,
            CapabilityFlag.COPILOT_ASSIST,
        ],
        dependencies=["models"],
        description=(
            "IR node taxonomy, payload encoding, ambiguity propagation, "
            "node substitution, tree traversal, and copilot node suggestion."
        ),
    )

    desc = ComponentDescriptor(
        name="ir_layers",
        version=PackageVersion.V1_1,
        status=ComponentStatus.ACTIVE,
        capabilities=[
            CapabilityFlag.LOWERING_PASSES,
            CapabilityFlag.Z3_INTEGRATION,
            CapabilityFlag.CACHE_ENABLED,
        ],
        dependencies=["models", "ir_nodes"],
        description=(
            "IR layer construction, lowering pass orchestration, "
            "Z3-ready layer serialization, and layer cache management."
        ),
    )

    manifest_desc = ComponentDescriptor(
        name="manifest",
        version=PackageVersion.V2_0,
        status=ComponentStatus.ACTIVE,
        capabilities=[
            CapabilityFlag.COPILOT_ASSIST,
        ],
        dependencies=[],
        description=(
            "Package registry, versioning, capability matrix, and "
            "public API export descriptors."
        ),
    )

    for desc in [models_desc, desc, desc, manifest_desc]:
        manifest.register_component(desc)

    return manifest
