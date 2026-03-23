"""Module manifest for the theorem_schemas package — copilot-assisted Ch36 encoding.

Encodes the manifest metadata and schema registry for Chapter 36 of theory2.tex:
Subsystem theorem schemas. This module manages the registration, lookup, and
validation of theorem schemas across all JuGeo subsystems.

copilot: module manifest and schema registry for theorem_schemas package.

Mathematical Background
-----------------------
Chapter 36 of theory2.tex introduces a formal system in which each subsystem
of the JuGeo architecture is required to discharge a finite list of theorem
schemas before its outputs may be accepted by downstream consumers.  A
*theorem schema* is a parameterised statement — a template with free variables
— that becomes a concrete theorem once the variables are instantiated with
subsystem-specific witnesses.

The manifest layer defined here acts as the single source of truth for which
schemas exist, which subsystem owns them, and whether the current build of the
software has satisfied all proof obligations.  This separates *bookkeeping*
(registry, versioning, validation) from *proof content* (the actual
derivations, which live in the companion modules).

Design Principles
-----------------
1.  **Immutability by convention** — once a schema is registered its
    ``schema_id`` must not change.  The registry enforces uniqueness.
2.  **Auditability** — every mutation is timestamped via ``time.time()`` and
    carries a UUID so that logs can be correlated across processes.
3.  **Subsystem isolation** — each subsystem has its own slice of the registry;
    ``list_by_subsystem`` and the ``_by_subsystem`` index enforce this.
4.  **Forward compatibility** — ``from_dict`` / ``to_dict`` round-trips are
    stable across minor version bumps thanks to ``SCHEMA_FORMAT_VERSION``.

Usage Example
-------------
::

    from jugeo.encodings.theorem_schemas.manifest import (
        build_manifest, SchemaRegistry, SchemaDescriptor
    )

    manifest = build_manifest()
    assert manifest.validate() == []

    registry = SchemaRegistry()
    desc = SchemaDescriptor(
        schema_id="trust-001",
        subsystem="TRUST",
        description="Trust propagation monotonicity",
        template_vars=["T", "S"],
        proof_style="inductive",
    )
    registry.register(desc)
    print(registry.count())  # 1
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import ClassVar

__all__ = [
    "KNOWN_SUBSYSTEMS",
    "CURRENT_VERSION",
    "SCHEMA_FORMAT_VERSION",
    "TheoremSchemasManifest",
    "SchemaDescriptor",
    "SchemaRegistry",
    "build_manifest",
    "validate_manifest",
]

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

KNOWN_SUBSYSTEMS: list[str] = [
    "DESCENT",
    "TRUST",
    "EVIDENCE",
    "FEDERATION",
    "INVALIDATION",
    "MEMORY",
    "JUDGMENT",
    "ENCODING",
]
"""The exhaustive list of subsystems covered by Chapter 36."""

CURRENT_VERSION: str = "1.0.0"
"""Current version of the theorem_schemas package."""

SCHEMA_FORMAT_VERSION: str = "1.0"
"""Serialisation format version for schema descriptors and manifests.

Bump the minor digit when adding optional fields; bump the major digit when
removing or renaming existing fields.
"""

_REQUIRED_MANIFEST_FIELDS: tuple[str, ...] = (
    "version",
    "author",
    "chapter_ref",
    "theory_section",
    "subsystems_covered",
)
"""Fields that must be non-empty for a manifest to be considered valid."""


# ---------------------------------------------------------------------------
# TheoremSchemasManifest
# ---------------------------------------------------------------------------


@dataclass
class TheoremSchemasManifest:
    """Top-level manifest for the theorem_schemas package.

    The manifest records *meta-information* about the package: which version
    of the theory it encodes, which subsystems are covered, which public
    symbols are exported, and when the manifest was created.

    It is intentionally lightweight — no proof content lives here.  Its main
    job is to allow automated tooling to check coverage (``is_complete``) and
    to produce human-readable summaries for documentation.

    Attributes
    ----------
    version:
        Semantic version of the package (e.g. ``"1.0.0"``).
    author:
        Authoring team or individual (default ``"jugeo"``).
    chapter_ref:
        Reference to the theory chapter (default ``"Ch36"``).
    theory_section:
        Human-readable name of the theory section.
    subsystems_covered:
        Names of the JuGeo subsystems whose theorem schemas are encoded.
    exports:
        List of public symbol names provided by the package.
    created_at:
        Unix timestamp (seconds) of manifest creation.
    manifest_id:
        UUID4 string uniquely identifying this manifest instance.
    """

    version: str = CURRENT_VERSION
    author: str = "jugeo"
    chapter_ref: str = "Ch36"
    theory_section: str = "Subsystem Theorem Schemas"
    subsystems_covered: list[str] = field(
        default_factory=lambda: list(KNOWN_SUBSYSTEMS)
    )
    exports: list[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    manifest_id: str = field(default_factory=lambda: str(uuid.uuid4()))

    # ------------------------------------------------------------------
    # Serialisation helpers
    # ------------------------------------------------------------------

    def to_dict(self) -> dict:
        """Serialise the manifest to a plain Python dictionary.

        The returned dictionary is JSON-serialisable (all values are strings,
        floats, or lists of strings).  It includes a ``format_version`` key so
        that deserialisers can detect incompatible formats.

        Returns
        -------
        dict
            A fully populated dictionary representation of ``self``.

        Examples
        --------
        ::

            d = manifest.to_dict()
            assert d["chapter_ref"] == "Ch36"
        """
        return {
            "format_version": SCHEMA_FORMAT_VERSION,
            "version": self.version,
            "author": self.author,
            "chapter_ref": self.chapter_ref,
            "theory_section": self.theory_section,
            "subsystems_covered": list(self.subsystems_covered),
            "exports": list(self.exports),
            "created_at": self.created_at,
            "manifest_id": self.manifest_id,
        }

    @classmethod
    def from_dict(cls, d: dict) -> TheoremSchemasManifest:
        """Deserialise a manifest from a dictionary produced by ``to_dict``.

        Parameters
        ----------
        d:
            Dictionary previously returned by ``to_dict``.

        Returns
        -------
        TheoremSchemasManifest
            A new instance populated from ``d``.

        Raises
        ------
        KeyError
            If a required field is absent from ``d``.
        """
        return cls(
            version=d["version"],
            author=d["author"],
            chapter_ref=d["chapter_ref"],
            theory_section=d["theory_section"],
            subsystems_covered=list(d.get("subsystems_covered", [])),
            exports=list(d.get("exports", [])),
            created_at=float(d.get("created_at", time.time())),
            manifest_id=d.get("manifest_id", str(uuid.uuid4())),
        )

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def validate(self) -> list[str]:
        """Validate the manifest, returning a list of error strings.

        An empty list means the manifest is valid.  Each error string
        describes a single failed constraint.

        Returns
        -------
        list[str]
            Possibly empty list of human-readable error descriptions.

        Examples
        --------
        ::

            errors = manifest.validate()
            if errors:
                for e in errors:
                    print(f"  ERROR: {e}")
        """
        errors: list[str] = []
        if not self.version or not self.version.strip():
            errors.append("'version' must be a non-empty string")
        else:
            parts = self.version.split(".")
            if len(parts) != 3 or not all(p.isdigit() for p in parts):
                errors.append(
                    f"'version' must follow semver (got {self.version!r})"
                )
        if not self.author or not self.author.strip():
            errors.append("'author' must be a non-empty string")
        if not self.chapter_ref or not self.chapter_ref.strip():
            errors.append("'chapter_ref' must be a non-empty string")
        if not self.theory_section or not self.theory_section.strip():
            errors.append("'theory_section' must be a non-empty string")
        if not self.subsystems_covered:
            errors.append("'subsystems_covered' must be non-empty")
        for sub in self.subsystems_covered:
            if sub not in KNOWN_SUBSYSTEMS:
                errors.append(
                    f"Unknown subsystem {sub!r} in 'subsystems_covered'"
                )
        if self.created_at <= 0:
            errors.append("'created_at' must be a positive timestamp")
        try:
            uuid.UUID(self.manifest_id)
        except ValueError:
            errors.append(
                f"'manifest_id' is not a valid UUID (got {self.manifest_id!r})"
            )
        return errors

    # ------------------------------------------------------------------
    # Mutation helpers
    # ------------------------------------------------------------------

    def add_export(self, name: str) -> None:
        """Add a public symbol name to the exports list.

        Silently ignores duplicates.

        Parameters
        ----------
        name:
            The symbol name to add (e.g. ``"TheoremSchema"``).
        """
        if not name or not name.strip():
            raise ValueError("Export name must be a non-empty string")
        clean = name.strip()
        if clean not in self.exports:
            self.exports.append(clean)

    def remove_export(self, name: str) -> None:
        """Remove a symbol name from the exports list.

        Silently ignores names that are not present.

        Parameters
        ----------
        name:
            The symbol name to remove.
        """
        clean = (name or "").strip()
        if clean in self.exports:
            self.exports.remove(clean)

    # ------------------------------------------------------------------
    # Query helpers
    # ------------------------------------------------------------------

    def summary(self) -> str:
        """Return a short human-readable summary of the manifest.

        Returns
        -------
        str
            A single paragraph describing the manifest contents.
        """
        subs = ", ".join(self.subsystems_covered)
        return (
            f"TheoremSchemasManifest v{self.version} [{self.chapter_ref}] "
            f"covering {len(self.subsystems_covered)} subsystems: {subs}. "
            f"Author: {self.author}.  ID: {self.manifest_id}."
        )

    def is_complete(self) -> bool:
        """Return True iff all known subsystems are covered.

        Completeness is defined as covering every subsystem listed in
        ``KNOWN_SUBSYSTEMS``.

        Returns
        -------
        bool
            True if the manifest covers all known subsystems.
        """
        return set(KNOWN_SUBSYSTEMS).issubset(set(self.subsystems_covered))

    def get_version_tuple(self) -> tuple[int, int, int]:
        """Parse the semantic version string and return it as a 3-tuple.

        Returns
        -------
        tuple[int, int, int]
            ``(major, minor, patch)`` integers.

        Raises
        ------
        ValueError
            If ``self.version`` is not a valid semver string.
        """
        parts = self.version.split(".")
        if len(parts) != 3:
            raise ValueError(
                f"Expected semver X.Y.Z, got {self.version!r}"
            )
        try:
            return (int(parts[0]), int(parts[1]), int(parts[2]))
        except ValueError as exc:
            raise ValueError(
                f"Non-integer component in version {self.version!r}"
            ) from exc


# ---------------------------------------------------------------------------
# SchemaDescriptor
# ---------------------------------------------------------------------------


@dataclass
class SchemaDescriptor:
    """Lightweight descriptor for a single theorem schema.

    A ``SchemaDescriptor`` stores enough information to identify and
    categorise a theorem schema without embedding the full proof content.
    It is the unit of registration in ``SchemaRegistry``.

    Attributes
    ----------
    schema_id:
        Unique identifier for the schema (typically a stable slug like
        ``"trust-monotone-001"``).
    subsystem:
        Name of the owning subsystem, one of ``KNOWN_SUBSYSTEMS``.
    description:
        Human-readable one-line description of what the schema asserts.
    template_vars:
        Names of the free variables in the schema template (e.g.
        ``["T", "S", "e"]``).
    proof_style:
        Proof strategy: one of ``"direct"``, ``"inductive"``,
        ``"contradiction"``, ``"categorical"``.
    created_at:
        Unix timestamp of registration.
    tags:
        Arbitrary string labels for filtering/searching.
    """

    schema_id: str
    subsystem: str
    description: str
    template_vars: list[str]
    proof_style: str
    created_at: float = field(default_factory=time.time)
    tags: list[str] = field(default_factory=list)

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict:
        """Serialise to a plain dictionary suitable for JSON encoding.

        Returns
        -------
        dict
            Dictionary with all fields plus ``format_version``.
        """
        return {
            "format_version": SCHEMA_FORMAT_VERSION,
            "schema_id": self.schema_id,
            "subsystem": self.subsystem,
            "description": self.description,
            "template_vars": list(self.template_vars),
            "proof_style": self.proof_style,
            "created_at": self.created_at,
            "tags": list(self.tags),
        }

    @classmethod
    def from_dict(cls, d: dict) -> SchemaDescriptor:
        """Deserialise from a dictionary produced by ``to_dict``.

        Parameters
        ----------
        d:
            Source dictionary.

        Returns
        -------
        SchemaDescriptor
            New instance populated from ``d``.
        """
        return cls(
            schema_id=d["schema_id"],
            subsystem=d["subsystem"],
            description=d["description"],
            template_vars=list(d.get("template_vars", [])),
            proof_style=d.get("proof_style", "direct"),
            created_at=float(d.get("created_at", time.time())),
            tags=list(d.get("tags", [])),
        )

    # ------------------------------------------------------------------
    # Tag management
    # ------------------------------------------------------------------

    def has_tag(self, tag: str) -> bool:
        """Return True if this descriptor carries the given tag.

        Parameters
        ----------
        tag:
            Tag string to check.

        Returns
        -------
        bool
        """
        return tag in self.tags

    def add_tag(self, tag: str) -> None:
        """Add ``tag`` to the descriptor's tag list if not already present.

        Parameters
        ----------
        tag:
            Tag to add.
        """
        if not tag or not tag.strip():
            raise ValueError("Tag must be a non-empty string")
        clean = tag.strip()
        if clean not in self.tags:
            self.tags.append(clean)

    # ------------------------------------------------------------------
    # Query helpers
    # ------------------------------------------------------------------

    def var_count(self) -> int:
        """Return the number of free template variables.

        Returns
        -------
        int
        """
        return len(self.template_vars)

    def summary(self) -> str:
        """Return a one-line human-readable summary.

        Returns
        -------
        str
        """
        vars_str = (
            ", ".join(self.template_vars) if self.template_vars else "none"
        )
        return (
            f"[{self.subsystem}] {self.schema_id}: {self.description} "
            f"(vars={vars_str}, style={self.proof_style})"
        )

    def matches_subsystem(self, sub: str) -> bool:
        """Return True if this descriptor belongs to subsystem ``sub``.

        Case-insensitive comparison is performed.

        Parameters
        ----------
        sub:
            Subsystem name to match against.

        Returns
        -------
        bool
        """
        return self.subsystem.upper() == sub.upper()


# ---------------------------------------------------------------------------
# SchemaRegistry
# ---------------------------------------------------------------------------


class SchemaRegistry:
    """A mutable registry mapping schema IDs and subsystem names to descriptors.

    The registry maintains two internal indices:

    ``_by_id``
        Maps ``schema_id -> SchemaDescriptor``.
    ``_by_subsystem``
        Maps ``subsystem_name -> list[SchemaDescriptor]``.

    Both indices are kept in sync by the mutating methods ``register`` and
    ``remove``.  Consumers should treat the registry as the definitive source
    of truth for which schemas exist at runtime.

    Thread Safety
    -------------
    This class is *not* thread-safe.  External locking is required if the
    registry is mutated from multiple threads.
    """

    def __init__(self) -> None:
        """Initialise an empty registry."""
        self._by_id: dict[str, SchemaDescriptor] = {}
        self._by_subsystem: dict[str, list[SchemaDescriptor]] = {}

    # ------------------------------------------------------------------
    # Mutating operations
    # ------------------------------------------------------------------

    def register(self, descriptor: SchemaDescriptor) -> None:
        """Register a schema descriptor.

        Parameters
        ----------
        descriptor:
            The descriptor to register.

        Raises
        ------
        ValueError
            If a descriptor with the same ``schema_id`` already exists.
        """
        sid = descriptor.schema_id
        if sid in self._by_id:
            raise ValueError(
                f"Schema {sid!r} is already registered. "
                "Use remove() first if you intend to replace it."
            )
        self._by_id[sid] = descriptor
        sub = descriptor.subsystem.upper()
        if sub not in self._by_subsystem:
            self._by_subsystem[sub] = []
        self._by_subsystem[sub].append(descriptor)

    def remove(self, schema_id: str) -> bool:
        """Remove a descriptor by its schema ID.

        Parameters
        ----------
        schema_id:
            ID of the schema to remove.

        Returns
        -------
        bool
            True if the schema was found and removed, False otherwise.
        """
        if schema_id not in self._by_id:
            return False
        desc = self._by_id.pop(schema_id)
        sub = desc.subsystem.upper()
        if sub in self._by_subsystem:
            try:
                self._by_subsystem[sub].remove(desc)
            except ValueError:
                pass
            if not self._by_subsystem[sub]:
                del self._by_subsystem[sub]
        return True

    # ------------------------------------------------------------------
    # Query operations
    # ------------------------------------------------------------------

    def lookup(self, schema_id: str) -> SchemaDescriptor | None:
        """Look up a descriptor by its schema ID.

        Parameters
        ----------
        schema_id:
            ID to look up.

        Returns
        -------
        SchemaDescriptor | None
            The descriptor, or ``None`` if not found.
        """
        return self._by_id.get(schema_id)

    def list_by_subsystem(self, subsystem: str) -> list[SchemaDescriptor]:
        """Return all descriptors belonging to ``subsystem``.

        Parameters
        ----------
        subsystem:
            Subsystem name (case-insensitive).

        Returns
        -------
        list[SchemaDescriptor]
            Possibly empty list of descriptors.
        """
        return list(self._by_subsystem.get(subsystem.upper(), []))

    def list_all(self) -> list[SchemaDescriptor]:
        """Return all registered descriptors in insertion order.

        Returns
        -------
        list[SchemaDescriptor]
        """
        return list(self._by_id.values())

    def count(self) -> int:
        """Return the total number of registered schemas.

        Returns
        -------
        int
        """
        return len(self._by_id)

    def has(self, schema_id: str) -> bool:
        """Return True iff a descriptor with ``schema_id`` is registered.

        Parameters
        ----------
        schema_id:
            ID to check.

        Returns
        -------
        bool
        """
        return schema_id in self._by_id

    def subsystems(self) -> list[str]:
        """Return a sorted list of subsystem names present in the registry.

        Returns
        -------
        list[str]
        """
        return sorted(self._by_subsystem.keys())

    def merge(self, other: SchemaRegistry) -> SchemaRegistry:
        """Return a new registry that is the union of ``self`` and ``other``.

        Schemas present in both are taken from ``other`` (other takes
        precedence on conflict).

        Parameters
        ----------
        other:
            Another registry to merge with.

        Returns
        -------
        SchemaRegistry
            A new registry containing all schemas from both.
        """
        merged = SchemaRegistry()
        for desc in self.list_all():
            merged.register(desc)
        for desc in other.list_all():
            if merged.has(desc.schema_id):
                merged.remove(desc.schema_id)
            merged.register(desc)
        return merged

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict:
        """Serialise the registry to a plain dictionary.

        Returns
        -------
        dict
            Dict with ``format_version`` and ``schemas`` list.
        """
        return {
            "format_version": SCHEMA_FORMAT_VERSION,
            "schemas": [d.to_dict() for d in self.list_all()],
        }

    @classmethod
    def from_dict(cls, d: dict) -> SchemaRegistry:
        """Deserialise a registry from a dictionary produced by ``to_dict``.

        Parameters
        ----------
        d:
            Source dictionary.

        Returns
        -------
        SchemaRegistry
        """
        registry = cls()
        for raw in d.get("schemas", []):
            registry.register(SchemaDescriptor.from_dict(raw))
        return registry

    def __repr__(self) -> str:
        return (
            f"SchemaRegistry(count={self.count()}, "
            f"subsystems={self.subsystems()})"
        )


# ---------------------------------------------------------------------------
# Public factory functions
# ---------------------------------------------------------------------------


def build_manifest() -> TheoremSchemasManifest:
    """Build and return a fully-populated default manifest.

    The returned manifest covers all subsystems listed in
    ``KNOWN_SUBSYSTEMS`` and carries the current package version.

    Returns
    -------
    TheoremSchemasManifest
        A fresh manifest with all subsystems covered.

    Examples
    --------
    ::

        manifest = build_manifest()
        assert manifest.is_complete()
        assert manifest.validate() == []
    """
    m = TheoremSchemasManifest(
        version=CURRENT_VERSION,
        author="jugeo",
        chapter_ref="Ch36",
        theory_section="Subsystem Theorem Schemas",
        subsystems_covered=list(KNOWN_SUBSYSTEMS),
    )
    # Populate the exports list with the canonical __all__ of this package.
    _default_exports = [
        "TheoremSchema",
        "SubsystemSchema",
        "SchemaInstance",
        "ProofObligation",
        "SchemaValidator",
        "SubsystemRegistry",
        "build_subsystem_registry",
        "ProofStyle",
        "InstanceStatus",
        "SubsystemKind",
        "ProofAgent",
        "TheoremSchemasManifest",
        "SchemaDescriptor",
        "SchemaRegistry",
        "build_manifest",
        "validate_manifest",
    ]
    for export in _default_exports:
        m.add_export(export)
    return m


def validate_manifest(manifest: TheoremSchemasManifest) -> list[str]:
    """Validate a manifest and return a list of error strings.

    This is a standalone function that delegates to
    ``TheoremSchemasManifest.validate()`` and additionally checks that all
    listed exports are non-empty strings without whitespace.

    Parameters
    ----------
    manifest:
        The manifest to validate.

    Returns
    -------
    list[str]
        Possibly empty list of error strings.  An empty list means the
        manifest is valid.

    Examples
    --------
    ::

        errors = validate_manifest(build_manifest())
        assert errors == []
    """
    errors = manifest.validate()
    for i, export in enumerate(manifest.exports):
        if not export or not export.strip():
            errors.append(f"Export at index {i} is empty or whitespace-only")
        elif " " in export:
            errors.append(
                f"Export {export!r} contains spaces (should be a bare symbol name)"
            )
    # Check for duplicate exports
    seen: set[str] = set()
    for export in manifest.exports:
        clean = export.strip()
        if clean in seen:
            errors.append(f"Duplicate export {clean!r} in exports list")
        seen.add(clean)
    return errors


# ---------------------------------------------------------------------------
# Internal helpers (not exported)
# ---------------------------------------------------------------------------


def _make_default_descriptors() -> list[SchemaDescriptor]:
    """Build a list of default SchemaDescriptors for the built-in subsystems.

    These descriptors are primarily useful for testing and documentation.
    They encode the *names* of the key theorems each subsystem must prove
    but do not carry proof content.

    Returns
    -------
    list[SchemaDescriptor]
    """
    now = time.time()
    return [
        SchemaDescriptor(
            schema_id="descent-001",
            subsystem="DESCENT",
            description="Descent data coherence along morphisms",
            template_vars=["X", "Y", "f"],
            proof_style="categorical",
            created_at=now,
            tags=["descent", "coherence"],
        ),
        SchemaDescriptor(
            schema_id="trust-001",
            subsystem="TRUST",
            description="Trust propagation monotonicity",
            template_vars=["T", "S"],
            proof_style="inductive",
            created_at=now,
            tags=["trust", "monotone"],
        ),
        SchemaDescriptor(
            schema_id="evidence-001",
            subsystem="EVIDENCE",
            description="Evidence accumulation soundness",
            template_vars=["E", "H"],
            proof_style="direct",
            created_at=now,
            tags=["evidence", "soundness"],
        ),
        SchemaDescriptor(
            schema_id="federation-001",
            subsystem="FEDERATION",
            description="Federation agreement consistency",
            template_vars=["N1", "N2", "msg"],
            proof_style="contradiction",
            created_at=now,
            tags=["federation", "consistency"],
        ),
        SchemaDescriptor(
            schema_id="invalidation-001",
            subsystem="INVALIDATION",
            description="Invalidation cascade termination",
            template_vars=["G", "n"],
            proof_style="inductive",
            created_at=now,
            tags=["invalidation", "termination"],
        ),
        SchemaDescriptor(
            schema_id="memory-001",
            subsystem="MEMORY",
            description="Memory snapshot consistency",
            template_vars=["M", "t"],
            proof_style="direct",
            created_at=now,
            tags=["memory", "consistency"],
        ),
        SchemaDescriptor(
            schema_id="judgment-001",
            subsystem="JUDGMENT",
            description="Judgment algebra associativity",
            template_vars=["J1", "J2", "J3"],
            proof_style="categorical",
            created_at=now,
            tags=["judgment", "algebra"],
        ),
        SchemaDescriptor(
            schema_id="encoding-001",
            subsystem="ENCODING",
            description="Encoding round-trip fidelity",
            template_vars=["obj", "codec"],
            proof_style="direct",
            created_at=now,
            tags=["encoding", "round-trip"],
        ),
    ]


def build_default_registry() -> SchemaRegistry:
    """Build a SchemaRegistry pre-populated with default descriptors.

    This is useful for tests, documentation generation, and initialising a
    fresh deployment.

    Returns
    -------
    SchemaRegistry
        Registry containing one descriptor per known subsystem.
    """
    registry = SchemaRegistry()
    for desc in _make_default_descriptors():
        registry.register(desc)
    return registry
