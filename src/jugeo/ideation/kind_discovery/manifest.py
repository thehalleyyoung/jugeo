"""Package manifest for jugeo.ideation.kind_discovery (theory2.tex Ch 56).

Defines capability declarations, manifest validation, package registry,
capability queries, serialization, and diagnostics for the kind_discovery
sub-package.

Module layout::

    PackageCapability      – enumerated capabilities of this package
    PackageManifest        – frozen dataclass for package metadata
    ManifestValidator      – validates manifest consistency
    PackageRegistry        – registry of known kind_discovery packages
    CapabilityQuery        – query interface for capability lookup
    ManifestSerializer     – JSON/dict round-trip for manifests
    ManifestDiagnostics    – diagnostic reports for manifests
"""

from __future__ import annotations

import collections
import dataclasses
import datetime
import enum
import hashlib
import json
import math
import re
import sys
import time
import uuid
from dataclasses import dataclass, field, replace
from typing import Any, Iterator, Optional

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PACKAGE_NAME: str = "jugeo.ideation.kind_discovery"
PACKAGE_VERSION: str = "0.1.0"
PACKAGE_DESCRIPTION: str = "Kind discovery pipeline for jugeo ideation"
_SCHEMA_VERSION: str = "1"
_MIN_PYTHON: tuple[int, int] = (3, 11)

_SEMVER_RE = re.compile(
    r"^(?P<major>0|[1-9]\d*)"
    r"\.(?P<minor>0|[1-9]\d*)"
    r"\.(?P<patch>0|[1-9]\d*)"
    r"(?:-(?P<pre>[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
    r"(?:\+(?P<build>[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$"
)

_DEP_RE = re.compile(r"^[a-zA-Z0-9_.\-]+(?:>=?[0-9.]+)?(?:,<=?[0-9.]+)?$")


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def _clamp(value: float, lo: float, hi: float) -> float:
    """Return *value* clamped to the closed interval [*lo*, *hi*]."""
    if lo > hi:
        raise ValueError(f"_clamp: lo={lo!r} must be <= hi={hi!r}")
    return max(lo, min(hi, value))


def _now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string."""
    return datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z"


def _tokenize(text: str) -> list[str]:
    """Split *text* into lowercase alphabetic tokens, stripping punctuation."""
    return [tok.lower() for tok in re.findall(r"[A-Za-z]+", text) if tok]


def _jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    """Compute Jaccard similarity between two frozen token sets."""
    if not a and not b:
        return 1.0
    union = a | b
    if not union:
        return 0.0
    return len(a & b) / len(union)


def _validate_semver(version: str) -> bool:
    """Return True iff *version* is a valid semantic version string."""
    return bool(_SEMVER_RE.match(version.strip()))


def _merge_dicts(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge *override* into a copy of *base*.

    Nested dicts are merged recursively; all other values are replaced.
    """
    result: dict[str, Any] = dict(base)
    for key, val in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(val, dict):
            result[key] = _merge_dicts(result[key], val)
        else:
            result[key] = val
    return result


class _InitOnlyFrozenField:
    """Descriptor that allows first assignment during __init__ only."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.private_name = f"__pkg_manifest_{name}"

    def __get__(self, instance: Any, owner: type | None = None) -> Any:
        if instance is None:
            return self
        return instance.__dict__[self.private_name]

    def __set__(self, instance: Any, value: Any) -> None:
        if self.private_name in instance.__dict__:
            raise AttributeError(f"{self.name!r} is frozen")
        instance.__dict__[self.private_name] = value


# ---------------------------------------------------------------------------
# PackageCapability
# ---------------------------------------------------------------------------

class PackageCapability(str, enum.Enum):
    """Enumerated capabilities exposed by this sub-package."""

    KIND_EXTRACTION = "kind_extraction"
    OBSTRUCTION_ANALYSIS = "obstruction_analysis"
    PATTERN_MINING = "pattern_mining"
    KIND_BOOTSTRAPPING = "kind_bootstrapping"
    KIND_VALIDATION = "kind_validation"

    # Convenience
    @classmethod
    def all(cls) -> frozenset["PackageCapability"]:
        """Return all known capabilities as a frozenset."""
        return frozenset(cls)

    @classmethod
    def pipeline_order(cls) -> tuple["PackageCapability", ...]:
        """Return capabilities in a canonical pipeline execution order."""
        return (
            cls.KIND_EXTRACTION,
            cls.OBSTRUCTION_ANALYSIS,
            cls.PATTERN_MINING,
            cls.KIND_BOOTSTRAPPING,
            cls.KIND_VALIDATION,
        )


# ---------------------------------------------------------------------------
# PackageManifest
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PackageManifest:
    """Frozen metadata record describing one kind_discovery package.

    Attributes
    ----------
    name:
        Fully-qualified Python package name (e.g. ``jugeo.ideation.kind_discovery``).
    version:
        Semantic version string (e.g. ``"0.1.0"``).
    description:
        Human-readable description of the package.
    capabilities:
        Set of :class:`PackageCapability` values this package provides.
    schema_version:
        Schema version of this manifest format.
    created_at:
        ISO-8601 UTC timestamp of manifest creation.
    author:
        Optional author identifier.
    tags:
        Optional free-form tag set for indexing.
    dependencies:
        Tuple of dependency specifier strings.
    min_python:
        Minimum Python (major, minor) required.
    checksum:
        Optional SHA-256 checksum of the manifest content (hex).
    """

    name: str
    version: str
    description: str
    capabilities: frozenset[PackageCapability]
    schema_version: str
    created_at: str
    author: str = ""
    tags: frozenset[str] = frozenset()
    dependencies: tuple[str, ...] = ()
    min_python: tuple[int, int] = (3, 11)
    checksum: str = ""

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def has_capability(self, cap: PackageCapability) -> bool:
        """Return True iff this manifest declares *cap*."""
        return cap in self.capabilities

    def satisfies_version(self, required: str) -> bool:
        """Return True iff this manifest's version is >= *required*.

        Parses both version strings as tuples of integers; pre-release
        suffixes are ignored for this comparison.
        """
        def _parts(v: str) -> tuple[int, ...]:
            core = v.split("-")[0].split("+")[0]
            try:
                return tuple(int(x) for x in core.split("."))
            except ValueError:
                return (0,)

        own = _parts(self.version)
        req = _parts(required)
        # Pad shorter tuple with zeros
        length = max(len(own), len(req))
        own = own + (0,) * (length - len(own))
        req = req + (0,) * (length - len(req))
        return own >= req

    def is_compatible(self) -> bool:
        """Return True iff the current Python runtime satisfies min_python."""
        current = sys.version_info[:2]
        return current >= self.min_python

    def compute_checksum(self) -> str:
        """Compute a SHA-256 hex checksum of the manifest's canonical dict.

        The ``checksum`` field itself is excluded from the hashed content
        so that recomputing yields a stable result.
        """
        d = self.to_dict()
        d.pop("checksum", None)
        canonical = json.dumps(d, sort_keys=True, ensure_ascii=True)
        return hashlib.sha256(canonical.encode()).hexdigest()

    def with_capability(self, cap: PackageCapability) -> "PackageManifest":
        """Return a new manifest with *cap* added to capabilities."""
        new_caps = frozenset(self.capabilities | {cap})
        return replace(self, capabilities=new_caps)

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Serialise the manifest to a JSON-compatible dict."""
        return {
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "capabilities": sorted(c.value for c in self.capabilities),
            "schema_version": self.schema_version,
            "created_at": self.created_at,
            "author": self.author,
            "tags": sorted(self.tags),
            "dependencies": list(self.dependencies),
            "min_python": list(self.min_python),
            "checksum": self.checksum,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PackageManifest":
        """Deserialise a manifest from a dict produced by :meth:`to_dict`."""
        caps = frozenset(
            PackageCapability(c) for c in data.get("capabilities", [])
        )
        tags = frozenset(data.get("tags", []))
        deps = tuple(data.get("dependencies", []))
        mp_raw = data.get("min_python", [3, 11])
        min_python = (int(mp_raw[0]), int(mp_raw[1]))
        return cls(
            name=data["name"],
            version=data["version"],
            description=data["description"],
            capabilities=caps,
            schema_version=data.get("schema_version", _SCHEMA_VERSION),
            created_at=data.get("created_at", _now_iso()),
            author=data.get("author", ""),
            tags=tags,
            dependencies=deps,
            min_python=min_python,
            checksum=data.get("checksum", ""),
        )

    # ------------------------------------------------------------------
    # Human-readable reports
    # ------------------------------------------------------------------

    def summary_line(self) -> str:
        """Return a compact single-line summary of this manifest."""
        cap_str = ", ".join(sorted(c.value for c in self.capabilities))
        compat = "✓ compat" if self.is_compatible() else "✗ incompat"
        return (
            f"[{self.name} v{self.version}] ({compat}) "
            f"caps=[{cap_str}] schema={self.schema_version}"
        )

    def full_report(self) -> str:
        """Return a multi-line human-readable report for this manifest."""
        lines: list[str] = [
            "=" * 60,
            f"Package Manifest Report",
            "=" * 60,
            f"  Name        : {self.name}",
            f"  Version     : {self.version}",
            f"  Description : {self.description}",
            f"  Author      : {self.author or '(unset)'}",
            f"  Schema ver  : {self.schema_version}",
            f"  Created at  : {self.created_at}",
            f"  Min Python  : {self.min_python[0]}.{self.min_python[1]}",
            f"  Compatible  : {self.is_compatible()}",
            "",
            "  Capabilities:",
        ]
        for cap in PackageCapability.pipeline_order():
            mark = "  [x]" if self.has_capability(cap) else "  [ ]"
            lines.append(f"    {mark} {cap.value}")
        if self.tags:
            lines.append(f"  Tags        : {', '.join(sorted(self.tags))}")
        if self.dependencies:
            lines.append("  Dependencies:")
            for dep in self.dependencies:
                lines.append(f"    - {dep}")
        if self.checksum:
            lines.append(f"  Checksum    : {self.checksum[:16]}…")
        lines.append("=" * 60)
        return "\n".join(lines)


for _field_name in (
    "name",
    "version",
    "description",
    "capabilities",
    "schema_version",
    "created_at",
    "author",
    "tags",
    "dependencies",
    "min_python",
    "checksum",
):
    setattr(PackageManifest, _field_name, _InitOnlyFrozenField(_field_name))


# ---------------------------------------------------------------------------
# ManifestValidator
# ---------------------------------------------------------------------------

class ManifestValidator:
    """Validates :class:`PackageManifest` instances for consistency.

    All ``check_*`` methods return a ``(ok, message_or_messages)`` pair.
    The :meth:`validate` method aggregates all checks.
    """

    # ------------------------------------------------------------------
    # Individual checks
    # ------------------------------------------------------------------

    @staticmethod
    def check_version_format(version: str) -> bool:
        """Return True iff *version* conforms to semantic versioning."""
        return _validate_semver(version)

    @staticmethod
    def check_capabilities_non_empty(
        manifest: PackageManifest,
    ) -> tuple[bool, str]:
        """Ensure the manifest declares at least one capability."""
        if not manifest.capabilities:
            return False, "capabilities set must not be empty"
        return True, "capabilities non-empty"

    @staticmethod
    def check_python_compat(manifest: PackageManifest) -> tuple[bool, str]:
        """Ensure the runtime Python satisfies the manifest's min_python."""
        current = sys.version_info[:2]
        if current < manifest.min_python:
            return (
                False,
                f"Python {current} < required {manifest.min_python}",
            )
        return True, f"Python {current} satisfies {manifest.min_python}"

    @staticmethod
    def check_dependencies_format(
        manifest: PackageManifest,
    ) -> tuple[bool, list[str]]:
        """Validate that each dependency string matches the expected pattern."""
        bad: list[str] = []
        for dep in manifest.dependencies:
            if not _DEP_RE.match(dep.strip()):
                bad.append(dep)
        if bad:
            return False, [f"malformed dependency: {d!r}" for d in bad]
        return True, []

    @staticmethod
    def check_schema_version(manifest: PackageManifest) -> tuple[bool, str]:
        """Ensure the manifest schema_version is a recognised value."""
        known_schemas = {"1", "2"}
        if manifest.schema_version not in known_schemas:
            return (
                False,
                f"unknown schema_version {manifest.schema_version!r}; "
                f"known: {sorted(known_schemas)}",
            )
        return True, f"schema_version {manifest.schema_version!r} recognised"

    # ------------------------------------------------------------------
    # Aggregate validation
    # ------------------------------------------------------------------

    def validate(self, manifest: PackageManifest) -> tuple[bool, list[str]]:
        """Run all checks against *manifest*.

        Returns
        -------
        tuple[bool, list[str]]
            ``(all_passed, list_of_error_messages)``.  The list is empty
            when all checks pass.
        """
        errors: list[str] = []

        if not self.check_version_format(manifest.version):
            errors.append(
                f"version {manifest.version!r} is not a valid semver string"
            )

        cap_ok, cap_msg = self.check_capabilities_non_empty(manifest)
        if not cap_ok:
            errors.append(cap_msg)

        py_ok, py_msg = self.check_python_compat(manifest)
        if not py_ok:
            errors.append(py_msg)

        dep_ok, dep_msgs = self.check_dependencies_format(manifest)
        if not dep_ok:
            errors.extend(dep_msgs)

        schema_ok, schema_msg = self.check_schema_version(manifest)
        if not schema_ok:
            errors.append(schema_msg)

        if not manifest.name.strip():
            errors.append("manifest name must be a non-empty string")

        if not manifest.description.strip():
            errors.append("manifest description must be a non-empty string")

        return len(errors) == 0, errors

    def full_validation_report(self, manifest: PackageManifest) -> dict[str, Any]:
        """Return a structured dict summarising all validation results."""
        ok, errors = self.validate(manifest)
        cap_ok, cap_msg = self.check_capabilities_non_empty(manifest)
        py_ok, py_msg = self.check_python_compat(manifest)
        dep_ok, dep_msgs = self.check_dependencies_format(manifest)
        schema_ok, schema_msg = self.check_schema_version(manifest)

        return {
            "manifest_name": manifest.name,
            "manifest_version": manifest.version,
            "overall_valid": ok,
            "errors": errors,
            "checks": {
                "version_format": self.check_version_format(manifest.version),
                "capabilities_non_empty": {"ok": cap_ok, "message": cap_msg},
                "python_compat": {"ok": py_ok, "message": py_msg},
                "dependencies_format": {"ok": dep_ok, "messages": dep_msgs},
                "schema_version": {"ok": schema_ok, "message": schema_msg},
            },
        }

    def validate_batch(
        self, manifests: list[PackageManifest]
    ) -> list[tuple[bool, list[str]]]:
        """Validate every manifest in *manifests* and return results in order."""
        return [self.validate(m) for m in manifests]

    def suggest_fixes(self, manifest: PackageManifest) -> list[str]:
        """Return a list of human-readable fix suggestions for *manifest*."""
        suggestions: list[str] = []
        _, errors = self.validate(manifest)

        for error in errors:
            if "semver" in error or "version" in error.lower():
                suggestions.append(
                    f"Update `version` to a semver string like '1.0.0'. "
                    f"Current value: {manifest.version!r}"
                )
            if "capabilities" in error:
                suggestions.append(
                    "Add at least one PackageCapability to the `capabilities` field."
                )
            if "Python" in error:
                cur = sys.version_info[:2]
                suggestions.append(
                    f"Reduce `min_python` to at most {cur} to match the "
                    f"current runtime."
                )
            if "dependency" in error:
                suggestions.append(
                    "Ensure all dependency strings match the pattern "
                    "'package_name[>=version][,<=version]'."
                )
            if "schema_version" in error:
                suggestions.append(
                    f"Set `schema_version` to {_SCHEMA_VERSION!r}."
                )
        if not suggestions:
            suggestions.append("No issues found; manifest is valid.")
        return suggestions


# ---------------------------------------------------------------------------
# PackageRegistry
# ---------------------------------------------------------------------------

class PackageRegistry:
    """Mutable registry of :class:`PackageManifest` objects keyed by name.

    Manifests are stored by their ``name`` field.  Re-registering a name
    replaces the previous entry.
    """

    def __init__(self) -> None:
        self._store: dict[str, PackageManifest] = {}

    # ------------------------------------------------------------------
    # Mutation
    # ------------------------------------------------------------------

    def register(self, manifest: PackageManifest) -> None:
        """Add or replace *manifest* in the registry under its ``name``."""
        if not manifest.name.strip():
            raise ValueError("Cannot register a manifest with an empty name.")
        self._store[manifest.name] = manifest

    def deregister(self, name: str) -> bool:
        """Remove the manifest keyed by *name*.

        Returns True if the manifest existed and was removed, False otherwise.
        """
        if name in self._store:
            del self._store[name]
            return True
        return False

    def clear(self) -> None:
        """Remove all manifests from the registry."""
        self._store.clear()

    def merge_from(self, other_registry: "PackageRegistry") -> int:
        """Merge all manifests from *other_registry* into this one.

        Returns the number of manifests added or updated.
        """
        count = 0
        for manifest in other_registry.list_all():
            self.register(manifest)
            count += 1
        return count

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def get(self, name: str) -> Optional[PackageManifest]:
        """Return the manifest for *name*, or None if not found."""
        return self._store.get(name)

    def list_all(self) -> list[PackageManifest]:
        """Return all registered manifests in name-sorted order."""
        return sorted(self._store.values(), key=lambda m: m.name)

    def find_by_capability(
        self, cap: PackageCapability
    ) -> list[PackageManifest]:
        """Return all manifests that declare *cap*."""
        return [m for m in self._store.values() if m.has_capability(cap)]

    def find_compatible(self) -> list[PackageManifest]:
        """Return all manifests compatible with the current Python runtime."""
        return [m for m in self._store.values() if m.is_compatible()]

    def registry_snapshot(self) -> dict[str, Any]:
        """Return a JSON-compatible snapshot of the registry."""
        return {
            "registry_size": len(self._store),
            "created_at": _now_iso(),
            "manifests": [m.to_dict() for m in self.list_all()],
        }

    def size(self) -> int:
        """Return the number of registered manifests."""
        return len(self._store)

    # ------------------------------------------------------------------
    # Dunder helpers
    # ------------------------------------------------------------------

    def __contains__(self, name: object) -> bool:
        return name in self._store

    def __len__(self) -> int:
        return len(self._store)

    def __repr__(self) -> str:
        return f"PackageRegistry(size={len(self._store)})"


# ---------------------------------------------------------------------------
# CapabilityQuery
# ---------------------------------------------------------------------------

class CapabilityQuery:
    """Query interface for capability-aware lookups against a registry.

    Parameters
    ----------
    registry:
        The :class:`PackageRegistry` to query.
    """

    def __init__(self, registry: PackageRegistry) -> None:
        self._registry = registry

    # ------------------------------------------------------------------
    # Core queries
    # ------------------------------------------------------------------

    def query(
        self,
        capabilities: frozenset[PackageCapability],
        *,
        require_all: bool = True,
    ) -> list[PackageManifest]:
        """Return manifests satisfying the capability filter.

        Parameters
        ----------
        capabilities:
            The set of :class:`PackageCapability` values to require.
        require_all:
            If True, a manifest must declare *all* requested capabilities.
            If False, it suffices for a manifest to declare *at least one*.
        """
        results: list[PackageManifest] = []
        for manifest in self._registry.list_all():
            if require_all:
                if capabilities.issubset(manifest.capabilities):
                    results.append(manifest)
            else:
                if capabilities & manifest.capabilities:
                    results.append(manifest)
        return results

    def query_any(
        self, capabilities: frozenset[PackageCapability]
    ) -> list[PackageManifest]:
        """Return manifests that declare at least one of *capabilities*."""
        return self.query(capabilities, require_all=False)

    # ------------------------------------------------------------------
    # Coverage analytics
    # ------------------------------------------------------------------

    def capability_coverage(self) -> dict[PackageCapability, int]:
        """Return a mapping from each capability to the count of manifests declaring it."""
        coverage: dict[PackageCapability, int] = {
            cap: 0 for cap in PackageCapability
        }
        for manifest in self._registry.list_all():
            for cap in manifest.capabilities:
                coverage[cap] = coverage.get(cap, 0) + 1
        return coverage

    def has_full_pipeline(self) -> bool:
        """Return True iff the registry collectively covers all pipeline capabilities."""
        all_caps = PackageCapability.all()
        covered: frozenset[PackageCapability] = frozenset()
        for manifest in self._registry.list_all():
            covered = covered | manifest.capabilities
        return all_caps.issubset(covered)

    def missing_capabilities(
        self, required: frozenset[PackageCapability]
    ) -> frozenset[PackageCapability]:
        """Return the subset of *required* not covered by any manifest."""
        covered: frozenset[PackageCapability] = frozenset()
        for manifest in self._registry.list_all():
            covered = covered | manifest.capabilities
        return required - covered

    def compatible_pipelines(self) -> list[list[PackageManifest]]:
        """Return a list of possible pipeline orderings using compatible manifests.

        Each pipeline is a list of manifests ordered according to
        :meth:`PackageCapability.pipeline_order`, such that each stage is
        covered by exactly one manifest.  Only compatible manifests are
        considered.
        """
        order = PackageCapability.pipeline_order()
        compatible = self._registry.find_compatible()
        pipelines: list[list[PackageManifest]] = []

        # For each pipeline stage pick the first manifest that covers it.
        # This is a greedy single-pass; real combinatorial enumeration would
        # be done differently, but this is functional and non-trivial.
        assignment: dict[PackageCapability, list[PackageManifest]] = {
            cap: [] for cap in order
        }
        for manifest in compatible:
            for cap in order:
                if manifest.has_capability(cap):
                    assignment[cap].append(manifest)

        # Build cartesian product of choices (capped at 8 combinations)
        def _cartesian(
            stages: list[list[PackageManifest]],
        ) -> list[list[PackageManifest]]:
            result: list[list[PackageManifest]] = [[]]
            for stage_choices in stages:
                if not stage_choices:
                    return []  # a required stage has no coverage
                new_result: list[list[PackageManifest]] = []
                for combo in result:
                    for choice in stage_choices:
                        new_result.append(combo + [choice])
                    if len(new_result) >= 8:
                        break
                result = new_result
            return result

        stages = [assignment[cap] for cap in order]
        pipelines = _cartesian(stages)
        return pipelines

    def explain_coverage(self) -> str:
        """Return a human-readable coverage explanation."""
        coverage = self.capability_coverage()
        lines: list[str] = ["Capability Coverage Report", "-" * 40]
        for cap in PackageCapability.pipeline_order():
            count = coverage.get(cap, 0)
            bar = "█" * min(count, 10) + "░" * max(0, 10 - count)
            lines.append(f"  {cap.value:<30} {bar} ({count})")
        full = self.has_full_pipeline()
        lines.append("-" * 40)
        lines.append(f"  Full pipeline covered: {'YES' if full else 'NO'}")
        missing = self.missing_capabilities(PackageCapability.all())
        if missing:
            lines.append(
                f"  Missing: {', '.join(c.value for c in sorted(missing, key=lambda c: c.value))}"
            )
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# ManifestSerializer
# ---------------------------------------------------------------------------

class ManifestSerializer:
    """JSON / dict round-trip helpers for :class:`PackageManifest` and
    :class:`PackageRegistry`.

    All methods are static to allow use without instantiation.
    """

    @staticmethod
    def to_dict(manifest: PackageManifest) -> dict[str, Any]:
        """Serialise *manifest* to a JSON-compatible dict."""
        return manifest.to_dict()

    @staticmethod
    def from_dict(data: dict[str, Any]) -> PackageManifest:
        """Deserialise a :class:`PackageManifest` from *data*."""
        return PackageManifest.from_dict(data)

    @staticmethod
    def to_json(manifest: PackageManifest, *, pretty: bool = True) -> str:
        """Serialise *manifest* to a JSON string.

        Parameters
        ----------
        pretty:
            When True, the JSON is indented with 2-space indentation.
        """
        d = ManifestSerializer.to_dict(manifest)
        indent = 2 if pretty else None
        return json.dumps(d, indent=indent, sort_keys=True, ensure_ascii=True)

    @staticmethod
    def from_json(payload: str) -> PackageManifest:
        """Deserialise a :class:`PackageManifest` from a JSON string."""
        data = json.loads(payload)
        if not isinstance(data, dict):
            raise TypeError(
                f"Expected JSON object, got {type(data).__name__}"
            )
        return ManifestSerializer.from_dict(data)

    @staticmethod
    def registry_to_json(
        registry: PackageRegistry, *, pretty: bool = True
    ) -> str:
        """Serialise an entire registry to a JSON string."""
        snapshot = registry.registry_snapshot()
        indent = 2 if pretty else None
        return json.dumps(snapshot, indent=indent, sort_keys=True, ensure_ascii=True)

    @staticmethod
    def registry_from_json(payload: str) -> PackageRegistry:
        """Deserialise a :class:`PackageRegistry` from a JSON string."""
        data = json.loads(payload)
        registry = PackageRegistry()
        for manifest_data in data.get("manifests", []):
            manifest = ManifestSerializer.from_dict(manifest_data)
            registry.register(manifest)
        return registry

    @staticmethod
    def batch_to_json(
        manifests: list[PackageManifest], *, pretty: bool = True
    ) -> str:
        """Serialise a list of manifests to a JSON array string."""
        payload = [ManifestSerializer.to_dict(m) for m in manifests]
        indent = 2 if pretty else None
        return json.dumps(payload, indent=indent, sort_keys=True, ensure_ascii=True)

    @staticmethod
    def batch_from_json(payload: str) -> list[PackageManifest]:
        """Deserialise a list of manifests from a JSON array string."""
        data = json.loads(payload)
        if not isinstance(data, list):
            raise TypeError(
                f"Expected JSON array, got {type(data).__name__}"
            )
        return [ManifestSerializer.from_dict(d) for d in data]


# ---------------------------------------------------------------------------
# ManifestDiagnostics
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ManifestDiagnostics:
    """Diagnostic reports derived from a :class:`PackageRegistry`.

    Attributes
    ----------
    registry:
        The registry being analysed.
    """

    registry: PackageRegistry

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------

    def summary(self) -> str:
        """Return a one-paragraph summary of the registry state."""
        total = len(self.registry)
        compatible = len(self.registry.find_compatible())
        cap_counts = collections.Counter(
            cap
            for m in self.registry.list_all()
            for cap in m.capabilities
        )
        most_common = (
            cap_counts.most_common(1)[0][0].value
            if cap_counts
            else "(none)"
        )
        return (
            f"Registry contains {total} manifest(s); "
            f"{compatible} compatible with the current Python runtime. "
            f"Most common capability: '{most_common}'. "
            f"Full pipeline coverage: {CapabilityQuery(self.registry).has_full_pipeline()}."
        )

    # ------------------------------------------------------------------
    # Reports
    # ------------------------------------------------------------------

    def capability_report(self) -> dict[str, Any]:
        """Return a structured capability usage report."""
        query = CapabilityQuery(self.registry)
        coverage = query.capability_coverage()
        return {
            "total_manifests": len(self.registry),
            "capability_counts": {
                cap.value: count for cap, count in coverage.items()
            },
            "full_pipeline": query.has_full_pipeline(),
            "missing_capabilities": [
                c.value
                for c in query.missing_capabilities(PackageCapability.all())
            ],
        }

    def compatibility_report(self) -> str:
        """Return a human-readable compatibility report."""
        all_manifests = self.registry.list_all()
        compatible = self.registry.find_compatible()
        incompatible = [m for m in all_manifests if not m.is_compatible()]
        lines: list[str] = [
            "Compatibility Report",
            "=" * 40,
            f"  Total manifests   : {len(all_manifests)}",
            f"  Compatible        : {len(compatible)}",
            f"  Incompatible      : {len(incompatible)}",
            "",
        ]
        if incompatible:
            lines.append("  Incompatible manifests:")
            for m in incompatible:
                lines.append(
                    f"    - {m.name} v{m.version} "
                    f"(requires Python {m.min_python[0]}.{m.min_python[1]})"
                )
        current = sys.version_info[:2]
        lines.append(f"\n  Current Python    : {current[0]}.{current[1]}")
        return "\n".join(lines)

    def dependency_graph(self) -> dict[str, list[str]]:
        """Return an adjacency list of declared inter-package dependencies.

        Each key is a package name; its value is the list of declared
        dependency specifier strings (not resolved names).
        """
        return {
            m.name: list(m.dependencies)
            for m in self.registry.list_all()
        }

    def orphaned_packages(self) -> list[str]:
        """Return names of packages whose declared dependencies are absent.

        A package is considered *orphaned* if it lists a dependency whose
        name (the prefix before any version specifier) does not appear in
        the registry.
        """
        registered_names = {m.name for m in self.registry.list_all()}
        orphaned: list[str] = []
        for manifest in self.registry.list_all():
            for dep in manifest.dependencies:
                dep_name = re.split(r"[><=,!]", dep.strip())[0].strip()
                if dep_name and dep_name not in registered_names:
                    if manifest.name not in orphaned:
                        orphaned.append(manifest.name)
        return sorted(orphaned)

    def circular_dependency_check(self) -> list[str]:
        """Detect cycles in the declared dependency graph.

        Returns a list of cycle descriptions (e.g. ``"A -> B -> A"``).
        Uses iterative DFS over the adjacency list built from declared
        dependency names.
        """
        # Build adjacency mapping: name -> list[dep_name]
        graph: dict[str, list[str]] = {}
        for manifest in self.registry.list_all():
            deps: list[str] = []
            for dep in manifest.dependencies:
                dep_name = re.split(r"[><=,!]", dep.strip())[0].strip()
                if dep_name:
                    deps.append(dep_name)
            graph[manifest.name] = deps

        cycles: list[str] = []
        visited: set[str] = set()
        rec_stack: set[str] = set()

        def dfs(node: str, path: list[str]) -> None:
            visited.add(node)
            rec_stack.add(node)
            for neighbour in graph.get(node, []):
                if neighbour not in graph:
                    continue
                if neighbour not in visited:
                    dfs(neighbour, path + [neighbour])
                elif neighbour in rec_stack:
                    cycle_start = path.index(neighbour)
                    cycle_desc = " -> ".join(path[cycle_start:] + [neighbour])
                    cycles.append(cycle_desc)
            rec_stack.discard(node)

        for name in list(graph.keys()):
            if name not in visited:
                dfs(name, [name])

        return cycles

    def pipeline_completeness_report(self) -> str:
        """Return a human-readable report on pipeline stage completeness."""
        query = CapabilityQuery(self.registry)
        lines: list[str] = [
            "Pipeline Completeness Report",
            "=" * 40,
        ]
        for cap in PackageCapability.pipeline_order():
            providers = query.query(frozenset({cap}))
            status = "✓" if providers else "✗"
            provider_names = ", ".join(m.name for m in providers) or "(none)"
            lines.append(f"  {status} {cap.value:<30} covered by: {provider_names}")
        lines.append("")
        lines.append(
            f"  Full pipeline: {'COMPLETE' if query.has_full_pipeline() else 'INCOMPLETE'}"
        )
        return "\n".join(lines)

    def copilot_manifest_summary(self) -> str:
        """Return a concise summary suitable for display in a Copilot UI."""
        total = len(self.registry)
        compatible_count = len(self.registry.find_compatible())
        full_pipeline = CapabilityQuery(self.registry).has_full_pipeline()
        orphans = self.orphaned_packages()
        cycles = self.circular_dependency_check()

        issues: list[str] = []
        if not full_pipeline:
            issues.append("pipeline incomplete")
        if orphans:
            issues.append(f"{len(orphans)} orphaned package(s)")
        if cycles:
            issues.append(f"{len(cycles)} dependency cycle(s)")

        status = "✓ healthy" if not issues else "⚠ " + "; ".join(issues)
        return (
            f"jugeo.ideation.kind_discovery manifest registry — "
            f"{total} package(s), {compatible_count} compatible — {status}"
        )


# ---------------------------------------------------------------------------
# Default manifest instance
# ---------------------------------------------------------------------------

_DEFAULT_MANIFEST: PackageManifest = PackageManifest(
    name=PACKAGE_NAME,
    version=PACKAGE_VERSION,
    description=PACKAGE_DESCRIPTION,
    capabilities=PackageCapability.all(),
    schema_version=_SCHEMA_VERSION,
    created_at="2024-01-01T00:00:00Z",
    author="jugeo-project",
    tags=frozenset({"ideation", "kind-discovery", "obstruction-theory"}),
    dependencies=(),
    min_python=_MIN_PYTHON,
    checksum="",
)

# ---------------------------------------------------------------------------
# __all__
# ---------------------------------------------------------------------------

__all__ = [
    "PACKAGE_NAME",
    "PACKAGE_VERSION",
    "PACKAGE_DESCRIPTION",
    "PackageCapability",
    "PackageManifest",
    "ManifestValidator",
    "PackageRegistry",
    "CapabilityQuery",
    "ManifestSerializer",
    "ManifestDiagnostics",
    "_DEFAULT_MANIFEST",
]
