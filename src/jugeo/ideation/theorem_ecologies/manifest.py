"""Package manifest for the theorem_ecologies sub-package (theory2.tex Ch61).

This module declares what the ``jugeo.ideation.theorem_ecologies`` package
provides, how it can be discovered at runtime, and how its capabilities are
validated against external requirements.

Module layout::

    PackageCapability      – enum of capabilities this package exposes
    PackageManifest        – frozen dataclass describing the package
    ManifestValidator      – validates PackageManifest instances
    PackageRegistry        – runtime registry of manifests
    CapabilityQuery        – chainable capability-query builder
    ManifestSerializer     – JSON serialisation helpers (static methods)
    ManifestDiagnostics    – health-check and reporting utilities
    _DEFAULT_MANIFEST      – pre-built manifest for this package
"""

from __future__ import annotations

import json
import math
import re
import time
import uuid
from collections import defaultdict, deque
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Iterable, Mapping, Sequence

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

PACKAGE_NAME: str = "jugeo.ideation.theorem_ecologies"
PACKAGE_VERSION: str = "0.1.0"
MIN_SUPPORTED_VERSION: str = "0.1.0"
DEFAULT_CAPABILITY_WEIGHT: float = 1.0
MAX_REGISTRY_SIZE: int = 1000
MANIFEST_SCHEMA_VERSION: str = "1"

# ---------------------------------------------------------------------------
# Module-level helpers  (no imports from other jugeo modules to avoid cycles)
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    """Return the current UTC timestamp as an ISO-8601 string."""
    return datetime.now(tz=timezone.utc).isoformat()


def _parse_semver(version: str) -> tuple[int, int, int]:
    """Parse a semantic version string into a (major, minor, patch) triple.

    Raises ``ValueError`` when the string does not match ``X.Y.Z``.
    """
    m = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", version.strip())
    if not m:
        raise ValueError(f"Invalid semver string: {version!r}")
    return int(m.group(1)), int(m.group(2)), int(m.group(3))


def _semver_gte(a: str, b: str) -> bool:
    """Return True when version string *a* is >= version string *b*."""
    try:
        return _parse_semver(a) >= _parse_semver(b)
    except ValueError:
        return False


def _semver_compatible(version: str, minimum: str) -> bool:
    """Return True when *version* satisfies *minimum* under semver rules.

    Two versions are compatible when they share the same major number and
    *version* >= *minimum*.
    """
    try:
        va = _parse_semver(version)
        vb = _parse_semver(minimum)
        return va[0] == vb[0] and va >= vb
    except ValueError:
        return False


def _slug(name: str) -> str:
    """Convert *name* to a lower-case alphanumeric slug with dots allowed."""
    return re.sub(r"[^a-z0-9.]", "-", name.lower())


def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    """Clamp *value* to [*lo*, *hi*]."""
    return max(lo, min(hi, float(value)))


# ---------------------------------------------------------------------------
# Enumeration
# ---------------------------------------------------------------------------


class PackageCapability(str, Enum):
    """Capabilities exposed by the ``theorem_ecologies`` package.

    Each member corresponds to a distinct analytical or modelling service
    that this package can provide to other components of the JuGeo system.
    """

    ECOLOGY_MODELING = "ecology_modeling"
    LEMMA_PORTFOLIO_MANAGEMENT = "lemma_portfolio_management"
    COMPOUNDING_ANALYSIS = "compounding_analysis"
    ECOLOGICAL_DYNAMICS = "ecological_dynamics"
    PORTFOLIO_OPTIMIZATION = "portfolio_optimization"

    # ------------------------------------------------------------------
    # Convenience helpers
    # ------------------------------------------------------------------

    def label(self) -> str:
        """Return a human-readable label for this capability."""
        return self.value.replace("_", " ").title()

    def is_core(self) -> bool:
        """Return True for the two most fundamental capabilities."""
        return self in (
            PackageCapability.ECOLOGY_MODELING,
            PackageCapability.LEMMA_PORTFOLIO_MANAGEMENT,
        )


# ---------------------------------------------------------------------------
# PackageManifest
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PackageManifest:
    """Immutable description of a Python package and its capabilities.

    A :class:`PackageManifest` is the primary registration artifact for a
    JuGeo sub-package.  It records the package's identity (name, version),
    what it can do (capabilities), who wrote it (authors), and what it
    needs (dependencies).

    Args:
        name: Fully-qualified package name (e.g. ``jugeo.ideation.theorem_ecologies``).
        version: Semantic version string in ``X.Y.Z`` format.
        capabilities: Tuple of :class:`PackageCapability` values.
        description: Short human-readable description.
        authors: Tuple of author strings.
        dependencies: Required package names.
        schema_version: Version of the manifest schema itself.
        created_at: ISO-8601 UTC creation timestamp.
        metadata: Arbitrary key/value annotations.
    """

    name: str
    version: str
    capabilities: tuple[PackageCapability, ...]
    description: str
    authors: tuple[str, ...]
    dependencies: tuple[str, ...]
    schema_version: str = MANIFEST_SCHEMA_VERSION
    created_at: str = field(default_factory=_now_iso)
    metadata: dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("Manifest name must not be empty.")
        if not re.fullmatch(r"[a-z][a-z0-9._-]*", self.name):
            raise ValueError(
                f"Manifest name {self.name!r} must be a lower-case dotted identifier."
            )
        try:
            _parse_semver(self.version)
        except ValueError as exc:
            raise ValueError(f"Invalid version for manifest {self.name!r}: {exc}") from exc

    # ------------------------------------------------------------------
    # Capability queries
    # ------------------------------------------------------------------

    def has_capability(self, cap: PackageCapability) -> bool:
        """Return True when *cap* is listed in this manifest."""
        return cap in self.capabilities

    def capability_count(self) -> int:
        """Return the number of capabilities declared by this manifest."""
        return len(self.capabilities)

    def missing_capabilities(
        self, required: Iterable[PackageCapability]
    ) -> tuple[PackageCapability, ...]:
        """Return capabilities in *required* that this manifest does not provide."""
        return tuple(c for c in required if c not in self.capabilities)

    # ------------------------------------------------------------------
    # Version compatibility
    # ------------------------------------------------------------------

    def is_compatible_with(self, other_version: str) -> bool:
        """Return True when *other_version* is semver-compatible with this manifest.

        Compatibility means both versions share the same major number and
        this manifest's version is >= *other_version*.
        """
        return _semver_compatible(self.version, other_version)

    # ------------------------------------------------------------------
    # Text summary
    # ------------------------------------------------------------------

    def summary(self) -> str:
        """Return a multi-line plain-text summary of this manifest."""
        cap_labels = ", ".join(c.label() for c in self.capabilities) or "(none)"
        dep_list = ", ".join(self.dependencies) or "(none)"
        author_list = ", ".join(self.authors) or "(unknown)"
        lines = [
            f"Package : {self.name}",
            f"Version : {self.version}",
            f"Authors : {author_list}",
            f"Schema  : {self.schema_version}",
            f"Created : {self.created_at}",
            f"Caps    : {cap_labels}",
            f"Deps    : {dep_list}",
            f"Desc    : {self.description}",
        ]
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Serialise this manifest to a plain Python dict."""
        return {
            "name": self.name,
            "version": self.version,
            "capabilities": [c.value for c in self.capabilities],
            "description": self.description,
            "authors": list(self.authors),
            "dependencies": list(self.dependencies),
            "schema_version": self.schema_version,
            "created_at": self.created_at,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PackageManifest:
        """Reconstruct a :class:`PackageManifest` from a plain dict."""
        caps = tuple(PackageCapability(v) for v in data.get("capabilities", []))
        return cls(
            name=data["name"],
            version=data["version"],
            capabilities=caps,
            description=data.get("description", ""),
            authors=tuple(data.get("authors", [])),
            dependencies=tuple(data.get("dependencies", [])),
            schema_version=data.get("schema_version", MANIFEST_SCHEMA_VERSION),
            created_at=data.get("created_at", _now_iso()),
            metadata=dict(data.get("metadata", {})),
        )


# ---------------------------------------------------------------------------
# ManifestValidator
# ---------------------------------------------------------------------------


class ManifestValidator:
    """Validates :class:`PackageManifest` instances against a set of rules.

    The validator can operate in *strict* mode (default) where even minor
    issues are reported, or in lenient mode where only critical errors are
    raised.

    Args:
        strict: When True, warnings are escalated to errors.
    """

    def __init__(self, strict: bool = True) -> None:
        self.strict = strict

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def validate(self, manifest: PackageManifest) -> list[str]:
        """Return a list of error strings for *manifest*.

        An empty list means the manifest is valid.
        """
        errors: list[str] = []
        errors.extend(self._check_name(manifest.name))
        if not self.validate_version(manifest.version):
            errors.append(f"Version {manifest.version!r} is not a valid semver string.")
        errors.extend(self.validate_capabilities(manifest.capabilities))
        errors.extend(self.validate_dependencies(manifest.dependencies))
        errors.extend(self._check_metadata(manifest.metadata))
        if not manifest.description:
            msg = "Manifest description is empty."
            errors.append(msg) if self.strict else None
        if not manifest.authors:
            msg = "Manifest has no listed authors."
            errors.append(msg) if self.strict else None
        return errors

    def validate_version(self, version: str) -> bool:
        """Return True when *version* matches ``X.Y.Z`` (semver)."""
        try:
            _parse_semver(version)
            return True
        except ValueError:
            return False

    def validate_capabilities(
        self, caps: tuple[PackageCapability, ...]
    ) -> list[str]:
        """Validate the capability tuple and return any error strings."""
        errors: list[str] = []
        if not caps and self.strict:
            errors.append("Manifest declares no capabilities.")
        seen: set[PackageCapability] = set()
        for cap in caps:
            if cap in seen:
                errors.append(f"Duplicate capability: {cap.value!r}.")
            seen.add(cap)
        return errors

    def validate_dependencies(self, deps: tuple[str, ...]) -> list[str]:
        """Validate the dependency list and return any error strings."""
        errors: list[str] = []
        seen: set[str] = set()
        for dep in deps:
            if not dep:
                errors.append("Dependency entry must not be an empty string.")
                continue
            if dep in seen:
                errors.append(f"Duplicate dependency: {dep!r}.")
            seen.add(dep)
            if re.search(r"\s", dep):
                errors.append(f"Dependency name {dep!r} must not contain whitespace.")
        return errors

    def is_valid(self, manifest: PackageManifest) -> bool:
        """Return True when *manifest* has no validation errors."""
        return len(self.validate(manifest)) == 0

    def require_valid(self, manifest: PackageManifest) -> None:
        """Raise :exc:`ValueError` listing all errors if *manifest* is invalid."""
        errors = self.validate(manifest)
        if errors:
            formatted = "\n  - ".join(errors)
            raise ValueError(
                f"Manifest {manifest.name!r} has {len(errors)} error(s):\n  - {formatted}"
            )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _check_name(self, name: str) -> list[str]:
        """Validate the package name and return any error strings."""
        errors: list[str] = []
        if not name:
            errors.append("Package name must not be empty.")
            return errors
        if not re.fullmatch(r"[a-z][a-z0-9._-]*", name):
            errors.append(
                f"Package name {name!r} must start with a lowercase letter and "
                "contain only lowercase alphanumerics, dots, hyphens, or underscores."
            )
        if ".." in name:
            errors.append(f"Package name {name!r} must not contain consecutive dots.")
        max_len = 128
        if len(name) > max_len:
            errors.append(f"Package name {name!r} exceeds the maximum length of {max_len}.")
        return errors

    def _check_metadata(self, metadata: dict[str, Any]) -> list[str]:
        """Validate the metadata dict and return any error strings."""
        errors: list[str] = []
        for key, value in metadata.items():
            if not isinstance(key, str):
                errors.append(f"Metadata key {key!r} must be a string.")
            elif not key:
                errors.append("Metadata keys must not be empty strings.")
            # Values are intentionally unrestricted but we flag obviously bad ones
            if value is None and self.strict:
                errors.append(
                    f"Metadata key {key!r} has a None value (consider omitting it)."
                )
        return errors


# ---------------------------------------------------------------------------
# PackageRegistry
# ---------------------------------------------------------------------------


class PackageRegistry:
    """Runtime registry of :class:`PackageManifest` instances.

    The registry acts as a service locator: packages register themselves at
    startup so that other modules can discover available capabilities without
    hard-coding imports.

    Args:
        strict: Passed through to the internal :class:`ManifestValidator`.
    """

    def __init__(self, strict: bool = True) -> None:
        self._manifests: dict[str, PackageManifest] = {}
        self._validator: ManifestValidator = ManifestValidator(strict=strict)

    # ------------------------------------------------------------------
    # Mutation
    # ------------------------------------------------------------------

    def register(self, manifest: PackageManifest) -> None:
        """Add *manifest* to the registry after validation.

        Raises:
            ValueError: If the manifest is invalid.
            OverflowError: If the registry has reached ``MAX_REGISTRY_SIZE``.
        """
        if len(self._manifests) >= MAX_REGISTRY_SIZE:
            raise OverflowError(
                f"Registry is full ({MAX_REGISTRY_SIZE} entries). "
                "Unregister an entry before adding a new one."
            )
        self._validator.require_valid(manifest)
        self._manifests[manifest.name] = manifest

    def unregister(self, name: str) -> bool:
        """Remove the manifest for *name* from the registry.

        Returns:
            True if the entry was found and removed, False otherwise.
        """
        if name in self._manifests:
            del self._manifests[name]
            return True
        return False

    # ------------------------------------------------------------------
    # Lookup
    # ------------------------------------------------------------------

    def get(self, name: str) -> PackageManifest | None:
        """Return the manifest registered under *name*, or ``None``."""
        return self._manifests.get(name)

    def require(self, name: str) -> PackageManifest:
        """Return the manifest registered under *name*.

        Raises:
            KeyError: If no manifest is registered under *name*.
        """
        try:
            return self._manifests[name]
        except KeyError:
            raise KeyError(f"No manifest registered for {name!r}.") from None

    def list_names(self) -> list[str]:
        """Return a sorted list of all registered package names."""
        return sorted(self._manifests.keys())

    def by_capability(self, cap: PackageCapability) -> list[PackageManifest]:
        """Return all manifests that declare *cap* as a capability."""
        return [m for m in self._manifests.values() if m.has_capability(cap)]

    def compatible_with(self, version: str) -> list[PackageManifest]:
        """Return all manifests whose version is semver-compatible with *version*."""
        return [m for m in self._manifests.values() if m.is_compatible_with(version)]

    def size(self) -> int:
        """Return the number of manifests currently registered."""
        return len(self._manifests)

    def clear(self) -> None:
        """Remove all manifests from the registry."""
        self._manifests.clear()

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Serialise the registry to a plain dict."""
        return {
            "manifests": {name: m.to_dict() for name, m in self._manifests.items()},
            "schema_version": MANIFEST_SCHEMA_VERSION,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PackageRegistry:
        """Reconstruct a :class:`PackageRegistry` from a plain dict."""
        registry = cls()
        for raw_manifest in data.get("manifests", {}).values():
            manifest = PackageManifest.from_dict(raw_manifest)
            # Bypass validation during reconstruction to avoid schema drift issues
            registry._manifests[manifest.name] = manifest
        return registry


# ---------------------------------------------------------------------------
# CapabilityQuery
# ---------------------------------------------------------------------------


class CapabilityQuery:
    """Immutable, chainable query builder for capability-based manifest lookups.

    Build a query by chaining :meth:`requiring`, :meth:`excluding`, and
    :meth:`with_minimum`, then execute it with :meth:`search`.

    Example::

        results = (
            CapabilityQuery()
            .requiring(PackageCapability.ECOLOGY_MODELING)
            .excluding(PackageCapability.PORTFOLIO_OPTIMIZATION)
            .with_minimum(2)
            .search(registry)
        )
    """

    def __init__(
        self,
        required: frozenset[PackageCapability] | None = None,
        excluded: frozenset[PackageCapability] | None = None,
        min_count: int = 0,
    ) -> None:
        self._required: frozenset[PackageCapability] = required or frozenset()
        self._excluded: frozenset[PackageCapability] = excluded or frozenset()
        self._min_count: int = max(0, min_count)

    # ------------------------------------------------------------------
    # Builder
    # ------------------------------------------------------------------

    def requiring(self, *caps: PackageCapability) -> CapabilityQuery:
        """Return a new query that requires all of *caps*."""
        return CapabilityQuery(
            required=self._required | frozenset(caps),
            excluded=self._excluded,
            min_count=self._min_count,
        )

    def excluding(self, *caps: PackageCapability) -> CapabilityQuery:
        """Return a new query that excludes any manifest with any of *caps*."""
        return CapabilityQuery(
            required=self._required,
            excluded=self._excluded | frozenset(caps),
            min_count=self._min_count,
        )

    def with_minimum(self, count: int) -> CapabilityQuery:
        """Return a new query that demands at least *count* capabilities."""
        return CapabilityQuery(
            required=self._required,
            excluded=self._excluded,
            min_count=count,
        )

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    def matches(self, manifest: PackageManifest) -> bool:
        """Return True when *manifest* satisfies all query constraints."""
        cap_set = frozenset(manifest.capabilities)
        if not self._required.issubset(cap_set):
            return False
        if self._excluded & cap_set:
            return False
        if manifest.capability_count() < self._min_count:
            return False
        return True

    def search(self, registry: PackageRegistry) -> list[PackageManifest]:
        """Return all manifests in *registry* that satisfy this query."""
        return [
            registry.require(name)
            for name in registry.list_names()
            if self.matches(registry.require(name))
        ]

    def describe(self) -> str:
        """Return a human-readable description of this query."""
        parts: list[str] = []
        if self._required:
            req_labels = ", ".join(c.label() for c in sorted(self._required, key=lambda c: c.value))
            parts.append(f"requires [{req_labels}]")
        if self._excluded:
            exc_labels = ", ".join(c.label() for c in sorted(self._excluded, key=lambda c: c.value))
            parts.append(f"excludes [{exc_labels}]")
        if self._min_count:
            parts.append(f"min_capabilities={self._min_count}")
        return "CapabilityQuery(" + ", ".join(parts) + ")" if parts else "CapabilityQuery(any)"


# ---------------------------------------------------------------------------
# ManifestSerializer
# ---------------------------------------------------------------------------


class ManifestSerializer:
    """Static helpers for serialising and deserialising manifests and registries.

    All methods are static — no instance is needed.
    """

    @staticmethod
    def serialize(manifest: PackageManifest) -> str:
        """Return a JSON string representation of *manifest*."""
        return json.dumps(manifest.to_dict(), indent=2, sort_keys=True)

    @staticmethod
    def deserialize(data: str) -> PackageManifest:
        """Reconstruct a :class:`PackageManifest` from a JSON string."""
        raw = json.loads(data)
        return PackageManifest.from_dict(raw)

    @staticmethod
    def serialize_registry(registry: PackageRegistry) -> str:
        """Return a JSON string representation of *registry*."""
        return json.dumps(registry.to_dict(), indent=2, sort_keys=True)

    @staticmethod
    def deserialize_registry(data: str) -> PackageRegistry:
        """Reconstruct a :class:`PackageRegistry` from a JSON string."""
        raw = json.loads(data)
        return PackageRegistry.from_dict(raw)

    @staticmethod
    def to_file(manifest: PackageManifest, path: str) -> None:
        """Write *manifest* as JSON to the file at *path*.

        The parent directory must already exist.  Existing files are
        overwritten without warning.
        """
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(ManifestSerializer.serialize(manifest))

    @staticmethod
    def from_file(path: str) -> PackageManifest:
        """Read a :class:`PackageManifest` from the JSON file at *path*."""
        with open(path, "r", encoding="utf-8") as fh:
            return ManifestSerializer.deserialize(fh.read())


# ---------------------------------------------------------------------------
# ManifestDiagnostics
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ManifestDiagnostics:
    """Snapshot diagnostics for a :class:`PackageRegistry`.

    Provides health-check and reporting utilities that do not mutate the
    registry.

    Args:
        registry: The registry to diagnose.
    """

    registry: PackageRegistry

    # ------------------------------------------------------------------
    # Health & coverage
    # ------------------------------------------------------------------

    def health_check(self) -> dict[str, Any]:
        """Return a dictionary describing the overall health of the registry.

        Keys:
            ``size``            – number of registered manifests.
            ``capacity_used``   – fraction of ``MAX_REGISTRY_SIZE`` consumed.
            ``all_capabilities_covered``
                                – True if every ``PackageCapability`` is
                                  provided by at least one manifest.
            ``manifests_with_no_capabilities``
                                – count of manifests declaring no capabilities.
            ``duplicate_versions``
                                – dict mapping version strings to lists of
                                  package names sharing that version.
            ``status``          – ``"ok"`` or ``"degraded"``.
        """
        size = self.registry.size()
        capacity_used = size / MAX_REGISTRY_SIZE
        cap_coverage = self.capability_coverage()
        all_covered = all(
            cap_coverage.get(c.value, 0) > 0 for c in PackageCapability
        )
        manifests_without_caps = sum(
            1
            for name in self.registry.list_names()
            if self.registry.require(name).capability_count() == 0
        )
        version_groups: dict[str, list[str]] = defaultdict(list)
        for name in self.registry.list_names():
            m = self.registry.require(name)
            version_groups[m.version].append(name)
        duplicate_versions = {v: names for v, names in version_groups.items() if len(names) > 1}
        status = "ok" if all_covered and manifests_without_caps == 0 else "degraded"
        return {
            "size": size,
            "capacity_used": round(capacity_used, 4),
            "all_capabilities_covered": all_covered,
            "manifests_with_no_capabilities": manifests_without_caps,
            "duplicate_versions": duplicate_versions,
            "status": status,
        }

    def capability_coverage(self) -> dict[str, int]:
        """Return a mapping from capability value to number of manifests providing it."""
        counts: dict[str, int] = {c.value: 0 for c in PackageCapability}
        for name in self.registry.list_names():
            manifest = self.registry.require(name)
            for cap in manifest.capabilities:
                counts[cap.value] = counts.get(cap.value, 0) + 1
        return counts

    def missing_capabilities(
        self, required: Iterable[PackageCapability]
    ) -> list[PackageCapability]:
        """Return capabilities in *required* not provided by any registered manifest."""
        covered = {
            cap
            for name in self.registry.list_names()
            for cap in self.registry.require(name).capabilities
        }
        return [cap for cap in required if cap not in covered]

    def version_summary(self) -> dict[str, str]:
        """Return a mapping from package name to version string."""
        return {
            name: self.registry.require(name).version
            for name in self.registry.list_names()
        }

    def summary_report(self) -> str:
        """Return a multi-line plain-text diagnostic report.

        The report includes:

        * Registry size and capacity utilisation.
        * Per-capability coverage counts.
        * A list of any capabilities with zero coverage.
        * Overall status (``OK`` / ``DEGRADED``).
        """
        health = self.health_check()
        cov = self.capability_coverage()
        missing = [c for c, count in cov.items() if count == 0]
        lines: list[str] = [
            "=" * 60,
            "  Theorem-Ecologies Package Registry — Diagnostics Report",
            "=" * 60,
            f"  Registered packages : {health['size']} / {MAX_REGISTRY_SIZE}",
            f"  Capacity used       : {health['capacity_used'] * 100:.1f}%",
            f"  Status              : {health['status'].upper()}",
            "",
            "  Capability Coverage:",
        ]
        for cap in PackageCapability:
            count = cov.get(cap.value, 0)
            marker = "✓" if count > 0 else "✗"
            lines.append(f"    {marker}  {cap.label():<40} {count} manifest(s)")
        if missing:
            lines.append("")
            lines.append("  ⚠ Uncovered capabilities:")
            for cap_value in missing:
                lines.append(f"    - {cap_value}")
        if health["duplicate_versions"]:
            lines.append("")
            lines.append("  ⚠ Shared version strings:")
            for ver, names in health["duplicate_versions"].items():
                lines.append(f"    {ver}: {', '.join(names)}")
        lines.append("=" * 60)
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Default manifest (module-level singleton)
# ---------------------------------------------------------------------------

_DEFAULT_MANIFEST = PackageManifest(
    name=PACKAGE_NAME,
    version=PACKAGE_VERSION,
    capabilities=tuple(PackageCapability),
    description="Theorem ecology modeling and lemma portfolio management for JuGeo.",
    authors=("JuGeo Team",),
    dependencies=(
        "jugeo.ideation.ideas",
        "jugeo.ideation.novelty",
        "jugeo.evidence.trust",
    ),
)

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    "PackageCapability",
    "PackageManifest",
    "ManifestValidator",
    "PackageRegistry",
    "CapabilityQuery",
    "ManifestSerializer",
    "ManifestDiagnostics",
    "_DEFAULT_MANIFEST",
    "PACKAGE_NAME",
    "PACKAGE_VERSION",
]
