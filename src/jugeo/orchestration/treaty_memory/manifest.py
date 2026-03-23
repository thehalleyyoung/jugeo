"""Treaty-memory package manifest, schema registry, and archive catalog for JuGeo.

This module provides the structural metadata infrastructure for the treaty_memory
package: manifest declarations, schema validation, archive cataloguing, module
descriptors, and health checking.

Theory reference
----------------
theory2.tex Ch48 – "Treaty memory, archival semantics, and negotiation recall"

The TreatyMemoryManifest declares version, chapter reference, and module inventory
for the package.  MemorySchemaRegistry maintains named JSON-schema-like dicts for
validating memory records.  ArchiveCatalog provides a persistent index of all
treaty archives known to the system.  PackageHealthCheck verifies the integrity of
the treaty_memory package at runtime.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

# ---------------------------------------------------------------------------
# Guarded upstream imports
# ---------------------------------------------------------------------------

try:
    from jugeo.orchestration.negotiation import (
        NegotiationMemory,
        TreatyArchive,
        NegotiationEventBus,
        NegotiationDiagnostics,
        SessionState,
    )
    _NEG_AVAILABLE = True
except ImportError:
    _NEG_AVAILABLE = False
    NegotiationMemory = object        # type: ignore[assignment,misc]
    TreatyArchive = object            # type: ignore[assignment,misc]
    NegotiationEventBus = object      # type: ignore[assignment,misc]
    NegotiationDiagnostics = object   # type: ignore[assignment,misc]
    SessionState = object             # type: ignore[assignment,misc]

try:
    from jugeo.orchestration.controller import (
        OrchestratorState,
        OrchestratorConfiguration,
        ConvergenceMonitor,
    )
    _CTRL_AVAILABLE = True
except ImportError:
    _CTRL_AVAILABLE = False
    OrchestratorState = object           # type: ignore[assignment,misc]
    OrchestratorConfiguration = object   # type: ignore[assignment,misc]
    ConvergenceMonitor = object          # type: ignore[assignment,misc]

try:
    from jugeo.evidence.trust import TrustLevel, TrustPolicy, TrustAuditLog
    _TRUST_AVAILABLE = True
except ImportError:
    _TRUST_AVAILABLE = False
    TrustLevel = object    # type: ignore[assignment,misc]
    TrustPolicy = object   # type: ignore[assignment,misc]
    TrustAuditLog = object # type: ignore[assignment,misc]

try:
    from jugeo.geometry.descent import (
        DescentEngine,
        DescentStrategy,
        DescentLog,
    )
    _DESCENT_AVAILABLE = True
except ImportError:
    _DESCENT_AVAILABLE = False
    DescentEngine = object   # type: ignore[assignment,misc]
    DescentStrategy = object # type: ignore[assignment,misc]
    DescentLog = object      # type: ignore[assignment,misc]

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

_PACKAGE_VERSION: str = "1.0.0"
_SCHEMA_VERSION: int = 1
_CHAPTER_REF: str = "Ch48"
_PACKAGE_NAME: str = "treaty_memory"
_AUTHOR: str = "jugeo"
_MODULE_NAMES: list[str] = ["models", "manifest"]

# ---------------------------------------------------------------------------
# TreatyMemoryManifest
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class TreatyMemoryManifest:
    """Manifest declaration for the treaty_memory package.

    Captures version metadata, chapter reference from theory2.tex, a human-
    readable description, the list of module names included in the package,
    the schema version number used to version-gate records, and authorship.

    Theory reference: theory2.tex Ch48 – "Treaty memory, archival semantics,
    and negotiation recall".  The manifest is the authoritative identifier for
    a particular release of the treaty_memory subsystem; downstream components
    should check :meth:`is_compatible` before consuming memory records.

    Attributes
    ----------
    version:
        PEP 440-style version string (``X.Y.Z``).
    chapter_ref:
        Reference to the theory chapter that governs this package (e.g.
        ``"Ch48"``).
    package_name:
        Dotted package name, typically ``"treaty_memory"``.
    description:
        Free-text description of the package's purpose.
    created_at:
        Unix timestamp (float) recording when this manifest was created.
    modules:
        Ordered list of Python module names that belong to this package.
    schema_version:
        Integer version of the memory-record schema.  Incremented on breaking
        changes to :class:`~jugeo.orchestration.treaty_memory.models.TreatyMemoryRecord`.
    author:
        Authoring organisation or individual identifier.
    """

    version: str
    chapter_ref: str
    package_name: str
    description: str
    created_at: float
    modules: list[str]
    schema_version: int
    author: str

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Serialise this manifest to a plain Python dictionary.

        The returned dict is JSON-serialisable and round-trips cleanly through
        :func:`json.dumps` / :func:`json.loads`.  It carries a ``"manifest_id"``
        field derived from a stable hash of ``package_name + version`` so that
        remote peers can cache a single canonical copy.

        Returns
        -------
        dict[str, Any]
            A fully populated serialisation of this manifest.

        Examples
        --------
        >>> m = build_manifest()
        >>> d = m.to_dict()
        >>> d["version"]
        '1.0.0'
        """
        stable_seed = f"{self.package_name}:{self.version}:{self.schema_version}"
        manifest_id = hashlib.sha256(stable_seed.encode()).hexdigest()[:16]
        return {
            "manifest_id": manifest_id,
            "version": self.version,
            "chapter_ref": self.chapter_ref,
            "package_name": self.package_name,
            "description": self.description,
            "created_at": self.created_at,
            "modules": list(self.modules),
            "schema_version": self.schema_version,
            "author": self.author,
        }

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def validate(self) -> list[str]:
        """Return a list of validation-error strings.

        Performs structural checks on every field.  An empty return value
        indicates a fully valid manifest.

        Checks performed
        ~~~~~~~~~~~~~~~~
        * ``version`` must match the ``X.Y.Z`` numeric triplet pattern.
        * ``chapter_ref`` must be non-empty.
        * ``package_name`` must be non-empty and contain only alphanumerics,
          underscores, or dots.
        * ``modules`` must be a non-empty list of non-empty strings.
        * ``schema_version`` must be >= 1.
        * ``author`` must be non-empty.

        Returns
        -------
        list[str]
            Zero or more human-readable error descriptions.
        """
        errors: list[str] = []

        # version format
        parts = self.version.split(".")
        if len(parts) != 3 or not all(p.isdigit() for p in parts):
            errors.append(
                f"version {self.version!r} does not match X.Y.Z format"
            )

        # chapter_ref
        if not self.chapter_ref or not self.chapter_ref.strip():
            errors.append("chapter_ref must be a non-empty string")

        # package_name
        if not self.package_name or not self.package_name.strip():
            errors.append("package_name must be a non-empty string")
        else:
            allowed = set("abcdefghijklmnopqrstuvwxyz_.")
            bad_chars = [c for c in self.package_name if c not in allowed]
            if bad_chars:
                errors.append(
                    f"package_name contains invalid characters: {bad_chars}"
                )

        # modules
        if not self.modules:
            errors.append("modules must be a non-empty list")
        else:
            for idx, mod in enumerate(self.modules):
                if not mod or not mod.strip():
                    errors.append(f"modules[{idx}] is an empty string")

        # schema_version
        if self.schema_version < 1:
            errors.append(
                f"schema_version must be >= 1, got {self.schema_version}"
            )

        # author
        if not self.author or not self.author.strip():
            errors.append("author must be a non-empty string")

        return errors

    # ------------------------------------------------------------------
    # Human-readable summary
    # ------------------------------------------------------------------

    def summary(self) -> str:
        """Return a multi-line human-readable summary of this manifest.

        Suitable for logging, CLI output, or diagnostic reports.

        Returns
        -------
        str
            A formatted multi-line string.
        """
        errors = self.validate()
        validity = "VALID" if not errors else f"INVALID ({len(errors)} error(s))"
        lines = [
            f"TreatyMemoryManifest",
            f"  package      : {self.package_name}",
            f"  version      : {self.version}",
            f"  schema_ver   : {self.schema_version}",
            f"  chapter_ref  : {self.chapter_ref}",
            f"  author       : {self.author}",
            f"  modules      : {', '.join(self.modules)}",
            f"  created_at   : {self.created_at:.3f}",
            f"  description  : {self.description}",
            f"  validity     : {validity}",
        ]
        if errors:
            for err in errors:
                lines.append(f"    ERROR: {err}")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Compatibility
    # ------------------------------------------------------------------

    def is_compatible(self, other_version: str) -> bool:
        """Return True if *other_version* shares the same major version.

        Two manifests are considered compatible when their major version
        component (the ``X`` in ``X.Y.Z``) is identical.  Minor and patch
        differences are tolerated.

        Parameters
        ----------
        other_version:
            A version string in ``X.Y.Z`` format to compare against.

        Returns
        -------
        bool
            ``True`` when major versions match, ``False`` otherwise or if
            either version string is malformed.
        """
        try:
            self_major = int(self.version.split(".")[0])
            other_major = int(other_version.split(".")[0])
            return self_major == other_major
        except (ValueError, IndexError):
            return False


# ---------------------------------------------------------------------------
# MemorySchemaRegistry
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class MemorySchemaRegistry:
    """Registry of named JSON-schema-like dicts for validating memory records.

    Each schema is a plain Python dict containing at minimum:
    * ``"version"`` (int): the schema's own version number.
    * ``"required"`` (list[str]): field names that must be present in any
      conforming record.
    * ``"description"`` (str): a human description of the schema.

    The registry can be *locked* after initial population to prevent accidental
    mutation in production code paths.

    Theory reference: theory2.tex Ch48 §48.3 – schema versioning for treaty
    memory records ensures that archives are readable across software releases.

    Attributes
    ----------
    schemas:
        Mapping of schema name → schema dict.
    version:
        Registry-level version counter, incremented externally if needed.
    locked:
        When ``True``, :meth:`register` raises :exc:`ValueError`.
    """

    schemas: dict[str, dict] = field(default_factory=dict)
    version: int = _SCHEMA_VERSION
    locked: bool = False

    # ------------------------------------------------------------------
    # Mutation
    # ------------------------------------------------------------------

    def register(self, name: str, schema: dict) -> None:
        """Register a named schema.

        Parameters
        ----------
        name:
            Unique schema identifier (e.g. ``"friction_pattern"``).
        schema:
            A dict with at least ``"version"``, ``"required"``, and
            ``"description"`` keys.

        Raises
        ------
        ValueError
            If the registry is locked, or if *name* is already registered.
        """
        if self.locked:
            raise ValueError(
                f"MemorySchemaRegistry is locked; cannot register {name!r}"
            )
        if name in self.schemas:
            raise ValueError(
                f"Schema {name!r} already registered; use a different name or "
                "create a new registry"
            )
        self.schemas[name] = copy.deepcopy(schema)

    # ------------------------------------------------------------------
    # Lookup
    # ------------------------------------------------------------------

    def lookup(self, name: str) -> dict | None:
        """Return the schema dict for *name*, or ``None`` if not registered.

        Parameters
        ----------
        name:
            The schema name to look up.

        Returns
        -------
        dict | None
            A shallow copy of the registered schema dict, or ``None``.
        """
        raw = self.schemas.get(name)
        return copy.deepcopy(raw) if raw is not None else None

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def validate_against(self, name: str, data: dict) -> list[str]:
        """Validate *data* against the named schema.

        Checks that every field listed in the schema's ``"required"`` list is
        present as a key in *data*.  Additional fields in *data* beyond the
        required set are silently accepted.

        Parameters
        ----------
        name:
            Name of the schema to validate against.
        data:
            The record dict to validate.

        Returns
        -------
        list[str]
            Zero or more human-readable error descriptions.  An empty list
            means *data* satisfies all required fields.
        """
        errors: list[str] = []
        schema = self.schemas.get(name)
        if schema is None:
            errors.append(f"Unknown schema {name!r}; cannot validate")
            return errors

        required_fields: list[str] = schema.get("required", [])
        for req in required_fields:
            if req not in data:
                errors.append(
                    f"Missing required field {req!r} (schema={name!r})"
                )

        schema_ver = schema.get("version")
        if schema_ver is not None and not isinstance(schema_ver, int):
            errors.append(
                f"Schema {name!r} has non-integer 'version' field: "
                f"{schema_ver!r}"
            )

        return errors

    # ------------------------------------------------------------------
    # Listing / export
    # ------------------------------------------------------------------

    def list_schemas(self) -> list[str]:
        """Return a sorted list of all registered schema names.

        Returns
        -------
        list[str]
            Alphabetically sorted schema names.
        """
        return sorted(self.schemas.keys())

    def export(self) -> dict[str, Any]:
        """Export the entire registry as a JSON-serialisable dict.

        Returns
        -------
        dict[str, Any]
            A dict with ``"version"`` and ``"schemas"`` keys.
        """
        return {
            "version": self.version,
            "schemas": {name: dict(schema) for name, schema in self.schemas.items()},
        }

    # ------------------------------------------------------------------
    # Locking
    # ------------------------------------------------------------------

    def lock(self) -> None:
        """Lock the registry, preventing further :meth:`register` calls.

        Idempotent: calling :meth:`lock` on an already-locked registry is safe.
        """
        self.locked = True

    def is_locked(self) -> bool:
        """Return whether the registry is currently locked.

        Returns
        -------
        bool
            ``True`` if locked, ``False`` otherwise.
        """
        return self.locked

    # ------------------------------------------------------------------
    # Utility
    # ------------------------------------------------------------------

    def schema_version(self, name: str) -> int | None:
        """Return the ``"version"`` field of the named schema, or ``None``.

        Parameters
        ----------
        name:
            The schema name to inspect.

        Returns
        -------
        int | None
            The integer schema version, or ``None`` if the schema is not
            registered or has no ``"version"`` field.
        """
        schema = self.schemas.get(name)
        if schema is None:
            return None
        return schema.get("version")


# ---------------------------------------------------------------------------
# ArchiveCatalog
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class ArchiveCatalog:
    """Persistent index of all treaty archives known to the system.

    Each entry in the catalog is keyed by an *archive_id* (a hex string) and
    stores arbitrary metadata about that archive (e.g. participant list,
    creation date, policy tags).

    The catalog exposes import/export helpers that use JSON so it can be
    trivially persisted to disk or transmitted over the network.

    Theory reference: theory2.tex Ch48 §48.5 – "Archive indexing and recall
    semantics" describes how the catalog enables O(1) lookup of historical
    treaty records by identifier.

    Attributes
    ----------
    entries:
        Mapping of archive_id → metadata dict.
    created_at:
        Unix timestamp when this catalog instance was created.
    last_modified:
        Unix timestamp of the most recent mutation.
    """

    entries: dict[str, dict] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    last_modified: float = field(default_factory=time.time)

    # ------------------------------------------------------------------
    # Mutation
    # ------------------------------------------------------------------

    def register_archive(self, archive_id: str, metadata: dict) -> None:
        """Register a new archive entry in the catalog.

        Parameters
        ----------
        archive_id:
            Unique identifier for the archive (typically a 16-char hex string).
        metadata:
            Arbitrary key-value metadata associated with the archive.

        Notes
        -----
        If *archive_id* already exists, the existing entry is silently
        overwritten with *metadata*.  Call :meth:`update_metadata` when you
        want a non-destructive merge instead.
        """
        self.entries[archive_id] = dict(metadata)
        self.last_modified = time.time()

    def remove_archive(self, archive_id: str) -> bool:
        """Remove an archive entry from the catalog.

        Parameters
        ----------
        archive_id:
            The archive identifier to remove.

        Returns
        -------
        bool
            ``True`` if the entry existed and was removed, ``False`` if the
            archive_id was not found.
        """
        if archive_id in self.entries:
            del self.entries[archive_id]
            self.last_modified = time.time()
            return True
        return False

    def update_metadata(self, archive_id: str, updates: dict) -> bool:
        """Merge *updates* into the metadata for an existing archive entry.

        Parameters
        ----------
        archive_id:
            The archive identifier to update.
        updates:
            Key-value pairs to merge into the existing metadata.  Existing
            keys are overwritten; new keys are added.

        Returns
        -------
        bool
            ``True`` if the entry existed and was updated, ``False`` if the
            archive_id was not found.
        """
        if archive_id not in self.entries:
            return False
        self.entries[archive_id].update(updates)
        self.last_modified = time.time()
        return True

    # ------------------------------------------------------------------
    # Lookup / listing
    # ------------------------------------------------------------------

    def lookup_archive(self, archive_id: str) -> dict | None:
        """Return the metadata dict for *archive_id*, or ``None``.

        Parameters
        ----------
        archive_id:
            The archive identifier to look up.

        Returns
        -------
        dict | None
            A shallow copy of the metadata dict, or ``None`` if not found.
        """
        raw = self.entries.get(archive_id)
        return dict(raw) if raw is not None else None

    def list_archives(self) -> list[str]:
        """Return a sorted list of all registered archive IDs.

        Returns
        -------
        list[str]
            Alphabetically sorted archive identifiers.
        """
        return sorted(self.entries.keys())

    def count(self) -> int:
        """Return the number of archive entries currently in the catalog.

        Returns
        -------
        int
            Non-negative entry count.
        """
        return len(self.entries)

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Serialise the catalog to a plain Python dictionary.

        The returned dict is JSON-serialisable and includes ``created_at``,
        ``last_modified``, and the full ``entries`` mapping.

        Returns
        -------
        dict[str, Any]
            Complete catalog snapshot.
        """
        return {
            "created_at": self.created_at,
            "last_modified": self.last_modified,
            "entry_count": len(self.entries),
            "entries": {k: dict(v) for k, v in self.entries.items()},
        }

    def export_catalog(self) -> str:
        """Serialise the catalog to a JSON string.

        Returns
        -------
        str
            A compact (no extra whitespace) JSON string representation of
            :meth:`to_dict`.
        """
        return json.dumps(self.to_dict(), separators=(",", ":"))

    def import_catalog(self, data: str) -> None:
        """Populate this catalog from a previously exported JSON string.

        Replaces *all* current entries, ``created_at``, and ``last_modified``
        with the values parsed from *data*.

        Parameters
        ----------
        data:
            A JSON string as produced by :meth:`export_catalog`.

        Raises
        ------
        ValueError
            If *data* is not valid JSON or is missing expected keys.
        """
        try:
            parsed = json.loads(data)
        except json.JSONDecodeError as exc:
            raise ValueError(f"import_catalog: invalid JSON – {exc}") from exc

        if "entries" not in parsed:
            raise ValueError(
                "import_catalog: JSON missing 'entries' key"
            )

        self.entries = {k: dict(v) for k, v in parsed["entries"].items()}
        if "created_at" in parsed:
            self.created_at = float(parsed["created_at"])
        if "last_modified" in parsed:
            self.last_modified = float(parsed["last_modified"])


# ---------------------------------------------------------------------------
# MemoryModuleDescriptor
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MemoryModuleDescriptor:
    """Immutable descriptor for a single Python module within treaty_memory.

    Captures the module's name, file path relative to the project root, the
    public classes and functions it exports, its version string, and a short
    description.  Because descriptors are immutable they are safe to cache,
    hash, and share across threads.

    Theory reference: theory2.tex Ch48 §48.1 – module inventory allows the
    manifest to self-describe its own contents, enabling introspection-driven
    tooling (documentation generators, compatibility checkers, etc.).

    Attributes
    ----------
    module_name:
        Short Python module name (e.g. ``"models"``).
    file_path:
        Path to the module file relative to the repository root.
    classes:
        List of public class names exported by the module.
    functions:
        List of public function names exported by the module.
    version:
        Version string matching the package version.
    description:
        One-line description of the module's purpose.
    """

    module_name: str
    file_path: str
    classes: list[str]
    functions: list[str]
    version: str
    description: str

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Serialise this descriptor to a plain Python dictionary.

        Returns
        -------
        dict[str, Any]
            JSON-serialisable representation of all fields.
        """
        return {
            "module_name": self.module_name,
            "file_path": self.file_path,
            "classes": list(self.classes),
            "functions": list(self.functions),
            "version": self.version,
            "description": self.description,
            "class_count": len(self.classes),
            "function_count": len(self.functions),
        }

    # ------------------------------------------------------------------
    # Human-readable summary
    # ------------------------------------------------------------------

    def summary(self) -> str:
        """Return a one-line human-readable summary of this descriptor.

        Returns
        -------
        str
            Format: ``"<module_name>: <N> classes, <M> functions – <description>"``.
        """
        return (
            f"{self.module_name}: {len(self.classes)} class(es), "
            f"{len(self.functions)} function(s) – {self.description}"
        )


# ---------------------------------------------------------------------------
# PackageHealthCheck
# ---------------------------------------------------------------------------


@dataclass
class PackageHealthCheck:
    """Runtime health checker for the treaty_memory package.

    Probes each optional dependency module and verifies that the core
    treaty_memory sub-modules are importable.  Results are returned as a
    dict mapping check-name to boolean.

    Theory reference: theory2.tex Ch48 §48.8 – "Integrity assertions" require
    that a live treaty_memory installation can always identify which of its
    upstream collaborators are reachable.

    This class intentionally stores no fields; all checks are computed fresh
    on each :meth:`run` invocation to reflect the current Python environment.
    """

    # ------------------------------------------------------------------
    # Core check runner
    # ------------------------------------------------------------------

    def run(self) -> dict[str, bool]:
        """Execute all health checks and return results.

        Checks
        ------
        negotiation_module
            Whether ``jugeo.orchestration.negotiation`` imported successfully.
        controller_module
            Whether ``jugeo.orchestration.controller`` imported successfully.
        trust_module
            Whether ``jugeo.evidence.trust`` imported successfully.
        descent_module
            Whether ``jugeo.geometry.descent`` imported successfully.
        treaty_memory_models
            Whether ``jugeo.orchestration.treaty_memory.models`` is importable
            at the time of the call.
        treaty_memory_manifest
            Always ``True``; this module itself is clearly importable.

        Returns
        -------
        dict[str, bool]
            Mapping of check name to boolean result.
        """
        results: dict[str, bool] = {}

        results["negotiation_module"] = _NEG_AVAILABLE
        results["controller_module"] = _CTRL_AVAILABLE
        results["trust_module"] = _TRUST_AVAILABLE
        results["descent_module"] = _DESCENT_AVAILABLE

        # Dynamic import check for treaty_memory.models
        try:
            import importlib
            importlib.import_module(
                "jugeo.orchestration.treaty_memory.models"
            )
            results["treaty_memory_models"] = True
        except ImportError:
            results["treaty_memory_models"] = False

        # This module is obviously importable
        results["treaty_memory_manifest"] = True

        return results

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------

    def summary(self) -> str:
        """Return a multi-line summary of all health check results.

        Returns
        -------
        str
            One line per check, with ``[OK]`` or ``[FAIL]`` indicators,
            followed by a totals line.
        """
        results = self.run()
        lines = ["PackageHealthCheck results:"]
        for name, ok in sorted(results.items()):
            tag = "[OK  ]" if ok else "[FAIL]"
            lines.append(f"  {tag} {name}")
        passed = sum(1 for v in results.values() if v)
        total = len(results)
        lines.append(f"  ── {passed}/{total} checks passed")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Aggregate
    # ------------------------------------------------------------------

    def all_healthy(self) -> bool:
        """Return ``True`` iff every health check passes.

        Returns
        -------
        bool
            ``True`` when all checks return ``True``, ``False`` otherwise.
        """
        return all(self.run().values())


# ---------------------------------------------------------------------------
# Factory / module-level functions
# ---------------------------------------------------------------------------


def build_manifest() -> TreatyMemoryManifest:
    """Build and return the default TreatyMemoryManifest for the treaty_memory package.

    Constructs the manifest using the module-level constants ``_PACKAGE_VERSION``,
    ``_SCHEMA_VERSION``, ``_CHAPTER_REF``, ``_PACKAGE_NAME``, ``_AUTHOR``, and
    ``_MODULE_NAMES``.  The description is grounded in the theoretical framework
    described in theory2.tex Ch48.

    Returns
    -------
    TreatyMemoryManifest
        A fully populated manifest ready for use or validation.

    Examples
    --------
    >>> m = build_manifest()
    >>> m.package_name
    'treaty_memory'
    >>> m.validate()
    []
    """
    description = (
        "Treaty-memory subsystem (theory2.tex Ch48): provides archival semantics "
        "and negotiation recall for JuGeo's orchestration layer.  Stores friction "
        "patterns, treaty archive entries, and negotiation outcomes in a "
        "schema-versioned record format suitable for long-term retention and "
        "cross-session replay."
    )
    return TreatyMemoryManifest(
        version=_PACKAGE_VERSION,
        chapter_ref=_CHAPTER_REF,
        package_name=_PACKAGE_NAME,
        description=description,
        created_at=time.time(),
        modules=list(_MODULE_NAMES),
        schema_version=_SCHEMA_VERSION,
        author=_AUTHOR,
    )


def validate_manifest(manifest: TreatyMemoryManifest) -> list[str]:
    """Return a list of validation error strings for the given manifest.

    An empty list means the manifest is valid.  Delegates to
    :meth:`TreatyMemoryManifest.validate` and adds additional cross-field
    checks:

    * ``schema_version`` must not exceed a reasonable upper bound (< 1000) to
      catch accidental large integers.
    * ``modules`` must contain all entries in ``_MODULE_NAMES`` (the canonical
      module list for this package release).
    * ``chapter_ref`` should start with ``"Ch"`` for consistency with the
      theory2.tex naming convention.

    Parameters
    ----------
    manifest:
        The manifest to validate.

    Returns
    -------
    list[str]
        Zero or more human-readable error descriptions.
    """
    errors: list[str] = manifest.validate()

    # Cross-field: schema_version upper bound
    if manifest.schema_version >= 1000:
        errors.append(
            f"schema_version {manifest.schema_version} seems unreasonably large "
            "(expected < 1000)"
        )

    # Cross-field: canonical module coverage
    missing_modules = [m for m in _MODULE_NAMES if m not in manifest.modules]
    if missing_modules:
        errors.append(
            f"manifest.modules is missing canonical module(s): {missing_modules}"
        )

    # Cross-field: chapter_ref convention
    if manifest.chapter_ref and not manifest.chapter_ref.startswith("Ch"):
        errors.append(
            f"chapter_ref {manifest.chapter_ref!r} does not follow the 'ChN' "
            "convention from theory2.tex"
        )

    return errors


def build_module_registry() -> dict[str, MemoryModuleDescriptor]:
    """Build and return the module registry for the treaty_memory package.

    Returns a dict mapping module_name to :class:`MemoryModuleDescriptor` for
    each module in the package.  This registry is the machine-readable
    counterpart to the human-readable manifest description.

    Returns
    -------
    dict[str, MemoryModuleDescriptor]
        Mapping of ``"models"`` and ``"manifest"`` to their descriptors.

    Examples
    --------
    >>> reg = build_module_registry()
    >>> sorted(reg.keys())
    ['manifest', 'models']
    """
    models_descriptor = MemoryModuleDescriptor(
        module_name="models",
        file_path="src/jugeo/orchestration/treaty_memory/models.py",
        classes=[
            "TreatyMemoryRecord",
            "FrictionPattern",
            "TreatyArchiveEntry",
            "NegotiationResult",
            "MemoryQuery",
            "MemoryStatistics",
            "TreatyClause",
            "NegotiationOutcome",
            "MemoryIndexKind",
            "ArchivePolicy",
        ],
        functions=[
            "make_friction_pattern",
            "make_archive_entry",
            "make_negotiation_result",
            "make_memory_query",
            "compute_memory_statistics",
        ],
        version=_PACKAGE_VERSION,
        description=(
            "Core data models for treaty memory: friction patterns, archive "
            "entries, results, queries."
        ),
    )
    manifest_descriptor = MemoryModuleDescriptor(
        module_name="manifest",
        file_path="src/jugeo/orchestration/treaty_memory/manifest.py",
        classes=[
            "TreatyMemoryManifest",
            "MemorySchemaRegistry",
            "ArchiveCatalog",
            "MemoryModuleDescriptor",
            "PackageHealthCheck",
        ],
        functions=[
            "build_manifest",
            "validate_manifest",
            "build_module_registry",
        ],
        version=_PACKAGE_VERSION,
        description=(
            "Package manifest, schema registry, archive catalog, and health checks."
        ),
    )
    return {
        "models": models_descriptor,
        "manifest": manifest_descriptor,
    }


def _default_schema_registry() -> MemorySchemaRegistry:
    """Build the default MemorySchemaRegistry with built-in schemas.

    Registers four canonical schemas used by the treaty_memory subsystem:

    ``friction_pattern``
        Schema for :class:`~jugeo.orchestration.treaty_memory.models.FrictionPattern`
        records; required fields: ``friction_id``, ``pattern_type``,
        ``magnitude``, ``created_at``.

    ``archive_entry``
        Schema for :class:`~jugeo.orchestration.treaty_memory.models.TreatyArchiveEntry`
        records; required fields: ``entry_id``, ``archive_id``, ``content``,
        ``timestamp``.

    ``negotiation_result``
        Schema for :class:`~jugeo.orchestration.treaty_memory.models.NegotiationResult`
        records; required fields: ``result_id``, ``session_id``, ``outcome``,
        ``participants``, ``created_at``.

    ``memory_query``
        Schema for :class:`~jugeo.orchestration.treaty_memory.models.MemoryQuery`
        records; required fields: ``query_id``, ``filters``, ``limit``,
        ``issued_at``.

    The registry is *not* locked after construction so callers may extend it
    with project-specific schemas before locking.

    Returns
    -------
    MemorySchemaRegistry
        A populated (but unlocked) schema registry.
    """
    registry = MemorySchemaRegistry()

    registry.register(
        "friction_pattern",
        {
            "version": _SCHEMA_VERSION,
            "description": (
                "Schema for FrictionPattern records.  A friction pattern captures "
                "a recurring source of negotiation resistance between agents, "
                "including its magnitude and category."
            ),
            "required": [
                "friction_id",
                "pattern_type",
                "magnitude",
                "created_at",
            ],
            "optional": ["tags", "source_agent", "target_agent", "metadata"],
        },
    )

    registry.register(
        "archive_entry",
        {
            "version": _SCHEMA_VERSION,
            "description": (
                "Schema for TreatyArchiveEntry records.  An archive entry "
                "represents a single persisted treaty clause or negotiation "
                "artefact stored in a named archive."
            ),
            "required": [
                "entry_id",
                "archive_id",
                "content",
                "timestamp",
            ],
            "optional": ["author", "tags", "expiry", "signature"],
        },
    )

    registry.register(
        "negotiation_result",
        {
            "version": _SCHEMA_VERSION,
            "description": (
                "Schema for NegotiationResult records.  A negotiation result "
                "encapsulates the outcome of a single completed negotiation "
                "session, including the list of participating agents and the "
                "agreed outcome value."
            ),
            "required": [
                "result_id",
                "session_id",
                "outcome",
                "participants",
                "created_at",
            ],
            "optional": ["clauses", "trust_delta", "friction_ids", "notes"],
        },
    )

    registry.register(
        "memory_query",
        {
            "version": _SCHEMA_VERSION,
            "description": (
                "Schema for MemoryQuery records.  A memory query specifies "
                "search filters, result limit, and issue timestamp for "
                "retrieving historical treaty memory records."
            ),
            "required": [
                "query_id",
                "filters",
                "limit",
                "issued_at",
            ],
            "optional": ["sort_by", "offset", "include_expired", "tags"],
        },
    )

    return registry


def _default_archive_catalog() -> ArchiveCatalog:
    """Build an empty ArchiveCatalog with proper timestamps.

    Returns an :class:`ArchiveCatalog` instance with no entries, but with
    ``created_at`` and ``last_modified`` both set to the current wall-clock
    time.  This is the recommended way to initialise a catalog before
    populating it from persistent storage or a network source.

    Returns
    -------
    ArchiveCatalog
        A freshly initialised, empty archive catalog.

    Examples
    --------
    >>> cat = _default_archive_catalog()
    >>> cat.count()
    0
    """
    now = time.time()
    return ArchiveCatalog(
        entries={},
        created_at=now,
        last_modified=now,
    )
