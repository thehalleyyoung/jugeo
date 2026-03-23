"""Incremental Memory Encoding Manifest — theory2.tex Ch34.

This module defines the package manifest for the incremental_memory encoding
subsystem, providing descriptor, registry, and validation infrastructure.
The manifest was developed with copilot assistance to align with the formal
specification in theory2.tex Chapter 34, which governs incremental semantic
memory updates via the Glue construction M' = Glue(M|_{X\\S}, new_sections, overlap_data).

Subsystems covered: RUNTIME_MEMORY, INVALIDATION, GEOMETRY, EVIDENCE, JUDGMENTS, ORCHESTRATION.
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, ClassVar

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class EncodingStatus(Enum):
    """Lifecycle status for an encoding descriptor.

    Values indicate the maturity and reliability of an encoding within the
    incremental_memory subsystem.  Consumers should treat STABLE encodings as
    part of the public API contract, EXPERIMENTAL ones as subject to change,
    DEPRECATED ones as slated for removal, and DRAFT ones as incomplete
    work-in-progress that should never be used in production.

    Attributes:
        STABLE: The encoding has been formally reviewed and is part of the
            stable public interface.  Backward-compatibility is guaranteed
            within the same major version.
        EXPERIMENTAL: The encoding is under active development.  Its interface
            may change without notice between minor versions.
        DEPRECATED: The encoding is retained for backward compatibility only.
            Callers should migrate to the recommended replacement.
        DRAFT: The encoding has been sketched but not yet implemented or
            reviewed.  It must not be depended upon in any production code.
    """

    STABLE = auto()
    EXPERIMENTAL = auto()
    DEPRECATED = auto()
    DRAFT = auto()


class SubsystemKind(Enum):
    """Identifies a logical subsystem within the incremental_memory package.

    Each value corresponds to a cluster of related modules that together
    implement one layer of the Glue construction described in theory2.tex
    Chapter 34.  The division follows the separation of concerns articulated
    in §34.2 of that chapter.

    Attributes:
        RUNTIME_MEMORY: The runtime memory bridge that manages live
            MemoryRegion objects and MemorySnapshot capture.
        INVALIDATION: The invalidation engine responsible for propagating
            ChangeEvents through the dependency graph as invalidation waves.
        GEOMETRY: The geometric support-set machinery including SupportSet,
            SupportRegion, and coordinate arithmetic.
        EVIDENCE: The evidence-layer models that wrap judgments into
            IncrementalUpdate payloads.
        JUDGMENTS: The judgment-term models including Provenance, JudgmentStatus,
            and related metadata required by the Glue construction.
        ORCHESTRATION: High-level pipeline and integration classes that
            coordinate all other subsystems end-to-end.
    """

    RUNTIME_MEMORY = auto()
    INVALIDATION = auto()
    GEOMETRY = auto()
    EVIDENCE = auto()
    JUDGMENTS = auto()
    ORCHESTRATION = auto()


# ---------------------------------------------------------------------------
# EncodingDescriptor
# ---------------------------------------------------------------------------

@dataclass
class EncodingDescriptor:
    """Describes a single encoding exported by the incremental_memory package.

    An EncodingDescriptor captures the contract of one encoding: what type it
    accepts, what type it produces, which theorems justify its correctness, and
    what lifecycle status it currently holds.  Descriptors are stored inside
    IncrementalMemoryManifest instances and indexed by PackageRegistry objects
    for fast lookup at runtime.

    The descriptor is intentionally lightweight — it carries metadata only and
    does not hold a reference to the encoding implementation itself.  This
    design prevents circular imports between the manifest module and the
    modules that implement each encoding.

    Attributes:
        name: Unique identifier for the encoding, e.g. ``"GlueComputation"``.
        description: Human-readable description of what the encoding does.
        input_type: Fully-qualified Python type name accepted as input.
        output_type: Fully-qualified Python type name produced as output.
        theorem_refs: List of theorem references in ``"theorem:X.Y"`` format
            that formally justify this encoding's correctness.
        status: Current lifecycle status; defaults to EXPERIMENTAL.
        version: SemVer string for this encoding's interface version.
        created_at: Unix timestamp recording when this descriptor was created.
    """

    name: str
    description: str
    input_type: str
    output_type: str
    theorem_refs: list[str] = field(default_factory=list)
    status: EncodingStatus = EncodingStatus.EXPERIMENTAL
    version: str = "0.1.0"
    created_at: float = field(default_factory=time.time)

    def to_json(self) -> str:
        """Serialize this descriptor to a JSON string.

        All fields are included in the output.  The ``status`` field is
        serialised as its enum ``.value`` so that the JSON is portable to
        non-Python consumers.

        Returns:
            A compact JSON string representing this descriptor.
        """
        return json.dumps({
            "name": self.name,
            "description": self.description,
            "input_type": self.input_type,
            "output_type": self.output_type,
            "theorem_refs": self.theorem_refs,
            "status": self.status.value,
            "version": self.version,
            "created_at": self.created_at,
        })

    @classmethod
    def from_json(cls, data: str) -> EncodingDescriptor:
        """Deserialize an EncodingDescriptor from a JSON string.

        The ``status`` field is expected to be an integer matching one of the
        EncodingStatus enum values.  Unknown status values fall back to
        EXPERIMENTAL so that forward-compatibility is preserved.

        Args:
            data: A JSON string previously produced by ``to_json``.

        Returns:
            A fully-populated EncodingDescriptor instance.
        """
        raw = json.loads(data)
        status_val = raw.get("status", EncodingStatus.EXPERIMENTAL.value)
        try:
            status = EncodingStatus(status_val)
        except ValueError:
            status = EncodingStatus.EXPERIMENTAL
        return cls(
            name=raw["name"],
            description=raw["description"],
            input_type=raw["input_type"],
            output_type=raw["output_type"],
            theorem_refs=raw.get("theorem_refs", []),
            status=status,
            version=raw.get("version", "0.1.0"),
            created_at=raw.get("created_at", time.time()),
        )

    def is_stable(self) -> bool:
        """Return True if this descriptor carries STABLE status.

        Callers can use this predicate to filter registries down to only those
        encodings that are safe to depend upon in production code.

        Returns:
            True if ``self.status == EncodingStatus.STABLE``, False otherwise.
        """
        return self.status == EncodingStatus.STABLE

    def get_theorem_count(self) -> int:
        """Return the number of theorem references attached to this descriptor.

        A high theorem count generally indicates a well-justified encoding
        whose correctness has been established across multiple results in
        theory2.tex Chapter 34.

        Returns:
            Integer count of entries in ``self.theorem_refs``.
        """
        return len(self.theorem_refs)

    def summary(self) -> str:
        """Return a human-readable multi-line summary of this descriptor.

        The summary is intended for logging and CLI output.  It includes every
        field so that developers can inspect a descriptor without needing to
        deserialize its JSON form.

        Returns:
            A multi-line string with each field on its own labelled line.
        """
        return (
            f"EncodingDescriptor(\n"
            f"  name        = {self.name!r}\n"
            f"  description = {self.description!r}\n"
            f"  input_type  = {self.input_type!r}\n"
            f"  output_type = {self.output_type!r}\n"
            f"  theorem_refs= {self.theorem_refs!r}\n"
            f"  status      = {self.status.name}\n"
            f"  version     = {self.version!r}\n"
            f"  created_at  = {self.created_at}\n"
            f")"
        )

    def validate(self) -> list[str]:
        """Check this descriptor for structural validity.

        Validation rules:
        - ``name`` must be a non-empty string.
        - ``description`` must be a non-empty string.
        - ``version`` must conform to a simplified SemVer pattern
          ``MAJOR.MINOR.PATCH`` where each component is a non-negative integer.

        Returns:
            A list of human-readable error messages.  An empty list means the
            descriptor is valid.
        """
        errors: list[str] = []
        if not self.name or not self.name.strip():
            errors.append("EncodingDescriptor.name must not be empty.")
        if not self.description or not self.description.strip():
            errors.append("EncodingDescriptor.description must not be empty.")
        parts = self.version.split(".")
        if len(parts) != 3 or not all(p.isdigit() for p in parts):
            errors.append(
                f"EncodingDescriptor.version {self.version!r} does not match "
                "MAJOR.MINOR.PATCH format."
            )
        return errors


# ---------------------------------------------------------------------------
# IncrementalMemoryManifest
# ---------------------------------------------------------------------------

@dataclass
class IncrementalMemoryManifest:
    """Top-level manifest for the incremental_memory encoding subsystem.

    An IncrementalMemoryManifest aggregates all metadata required to fully
    describe the incremental_memory package: its version, authorship, the
    chapter in theory2.tex that formally motivates it, the subsystems it
    requires, the public names it exports, and the individual encoding
    descriptors for each exported class.

    The manifest is the single authoritative source of truth for what the
    incremental_memory package exports and what its dependencies are.  It is
    consumed at package initialisation time and can be serialised to JSON for
    storage in a build artefact registry.

    Attributes:
        version: SemVer string for the manifest itself.
        author: Name or identifier of the team that owns this manifest.
        chapter_ref: Reference to the theory chapter, e.g. ``"theory2.tex:Ch34"``.
        theory_section: Short section label, e.g. ``"§34"``.
        required_subsystems: Subsystems that must be initialised before this
            package can operate correctly.
        exports: List of public class/function names exported by the package.
        manifest_id: Stable UUID for this manifest instance.
        created_at: Unix timestamp of manifest creation.
        description: Free-text description of the manifest's purpose.
        theorem_count: Total number of theorems referenced across all
            encoding descriptors.
        encoding_descriptors: Individual descriptors for each export.
    """

    version: str
    author: str
    chapter_ref: str
    theory_section: str
    required_subsystems: list[SubsystemKind] = field(default_factory=list)
    exports: list[str] = field(default_factory=list)
    manifest_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    created_at: float = field(default_factory=time.time)
    description: str = ""
    theorem_count: int = 0
    encoding_descriptors: list[EncodingDescriptor] = field(default_factory=list)

    def copilot_summary(self) -> str:
        """Return a detailed multi-line summary suitable for copilot logging.

        This method produces a verbose, human-readable block that includes
        every field in the manifest, making it suitable for inclusion in
        copilot-generated commit messages or pull-request descriptions.

        Returns:
            A multi-line string with every manifest field labelled and
            formatted for easy reading.
        """
        subsystem_names = ", ".join(s.name for s in self.required_subsystems)
        descriptor_count = len(self.encoding_descriptors)
        export_preview = self.exports[:5]
        return (
            f"IncrementalMemoryManifest Summary\n"
            f"==================================\n"
            f"  manifest_id      : {self.manifest_id}\n"
            f"  version          : {self.version}\n"
            f"  author           : {self.author}\n"
            f"  chapter_ref      : {self.chapter_ref}\n"
            f"  theory_section   : {self.theory_section}\n"
            f"  description      : {self.description}\n"
            f"  required_subsys  : {subsystem_names}\n"
            f"  export_count     : {len(self.exports)}\n"
            f"  export_preview   : {export_preview!r}\n"
            f"  theorem_count    : {self.theorem_count}\n"
            f"  descriptor_count : {descriptor_count}\n"
            f"  created_at       : {self.created_at}\n"
        )

    def to_json(self) -> str:
        """Serialize this manifest to a JSON string.

        Subsystem kinds are serialised as their integer ``.value`` and
        encoding descriptors are serialised as dicts (via their own
        ``to_json`` / JSON round-trip) to keep the output self-contained.

        Returns:
            A JSON string representing the complete manifest.
        """
        descriptors_raw = []
        for d in self.encoding_descriptors:
            descriptors_raw.append(json.loads(d.to_json()))
        return json.dumps({
            "version": self.version,
            "author": self.author,
            "chapter_ref": self.chapter_ref,
            "theory_section": self.theory_section,
            "required_subsystems": [s.value for s in self.required_subsystems],
            "exports": self.exports,
            "manifest_id": self.manifest_id,
            "created_at": self.created_at,
            "description": self.description,
            "theorem_count": self.theorem_count,
            "encoding_descriptors": descriptors_raw,
        })

    @classmethod
    def from_json(cls, data: str) -> IncrementalMemoryManifest:
        """Deserialize an IncrementalMemoryManifest from a JSON string.

        Subsystem kind values are mapped back to SubsystemKind enum members;
        unknown values are silently skipped.  Encoding descriptors are
        reconstructed via EncodingDescriptor.from_json.

        Args:
            data: A JSON string previously produced by ``to_json``.

        Returns:
            A fully-populated IncrementalMemoryManifest instance.
        """
        raw = json.loads(data)
        subsystems: list[SubsystemKind] = []
        for val in raw.get("required_subsystems", []):
            try:
                subsystems.append(SubsystemKind(val))
            except ValueError:
                pass
        descriptors: list[EncodingDescriptor] = []
        for d_raw in raw.get("encoding_descriptors", []):
            try:
                descriptors.append(EncodingDescriptor.from_json(json.dumps(d_raw)))
            except Exception as exc:  # noqa: BLE001
                logger.warning("Skipping malformed descriptor: %s", exc)
        return cls(
            version=raw["version"],
            author=raw["author"],
            chapter_ref=raw["chapter_ref"],
            theory_section=raw["theory_section"],
            required_subsystems=subsystems,
            exports=raw.get("exports", []),
            manifest_id=raw.get("manifest_id", str(uuid.uuid4())),
            created_at=raw.get("created_at", time.time()),
            description=raw.get("description", ""),
            theorem_count=raw.get("theorem_count", 0),
            encoding_descriptors=descriptors,
        )

    def validate(self) -> list[str]:
        """Check this manifest for structural validity.

        Validation rules enforced:
        - ``version`` must match MAJOR.MINOR.PATCH.
        - ``author`` must be non-empty.
        - ``exports`` list must contain at least one entry.

        Returns:
            A list of human-readable error messages.  An empty list means the
            manifest is structurally valid.
        """
        errors: list[str] = []
        parts = self.version.split(".")
        if len(parts) != 3 or not all(p.isdigit() for p in parts):
            errors.append(
                f"IncrementalMemoryManifest.version {self.version!r} does not "
                "match MAJOR.MINOR.PATCH."
            )
        if not self.author or not self.author.strip():
            errors.append("IncrementalMemoryManifest.author must not be empty.")
        if not self.exports:
            errors.append("IncrementalMemoryManifest.exports must contain at least one entry.")
        return errors

    def get_export_count(self) -> int:
        """Return the number of names in the exports list.

        Returns:
            Integer count of entries in ``self.exports``.
        """
        return len(self.exports)

    def add_descriptor(self, d: EncodingDescriptor) -> None:
        """Append an EncodingDescriptor to this manifest and update theorem_count.

        If a descriptor with the same name already exists it is replaced rather
        than duplicated, keeping the manifest consistent.

        Args:
            d: The EncodingDescriptor to add.
        """
        for i, existing in enumerate(self.encoding_descriptors):
            if existing.name == d.name:
                self.encoding_descriptors[i] = d
                self.theorem_count = sum(
                    x.get_theorem_count() for x in self.encoding_descriptors
                )
                return
        self.encoding_descriptors.append(d)
        self.theorem_count += d.get_theorem_count()

    def find_descriptor(self, name: str) -> EncodingDescriptor | None:
        """Look up a descriptor by its name.

        The search is case-sensitive and returns the first match found.  If no
        descriptor with the given name exists, ``None`` is returned.

        Args:
            name: The descriptor name to search for.

        Returns:
            The matching EncodingDescriptor, or None if not found.
        """
        for d in self.encoding_descriptors:
            if d.name == name:
                return d
        return None

    def get_stable_encodings(self) -> list[EncodingDescriptor]:
        """Return only those descriptors whose status is STABLE.

        This method is the recommended way to obtain a list of encodings that
        are safe to depend upon in production code.

        Returns:
            A (possibly empty) list of EncodingDescriptor instances with
            ``status == EncodingStatus.STABLE``.
        """
        return [d for d in self.encoding_descriptors if d.is_stable()]

    def compute_checksum(self) -> str:
        """Compute a SHA-256 checksum of the serialised manifest.

        The checksum is computed over the UTF-8 encoding of the JSON produced
        by ``to_json()``.  It can be used to detect accidental modifications
        to a manifest stored on disk.

        Returns:
            A lowercase hex-encoded SHA-256 digest string.
        """
        content = self.to_json().encode("utf-8")
        return hashlib.sha256(content).hexdigest()


# ---------------------------------------------------------------------------
# PackageRegistry
# ---------------------------------------------------------------------------

class PackageRegistry:
    """In-process registry of encoding descriptors for the incremental_memory package.

    PackageRegistry maintains a mutable mapping from encoding names to
    EncodingDescriptor instances and optionally stores a reference to the
    governing IncrementalMemoryManifest.  It provides CRUD-like operations
    for registering, unregistering, and looking up descriptors at runtime.

    The registry is intentionally not a dataclass so that it can encapsulate
    its internal dictionary state and enforce invariants (e.g., no duplicate
    names) through method logic rather than dataclass field mechanics.

    The registry is not thread-safe by default.  Callers that share a registry
    across threads are responsible for external synchronisation.

    Typical usage::

        registry = PackageRegistry()
        registry.register(my_descriptor)
        found = registry.lookup("GlueComputation")
    """

    def __init__(self) -> None:
        """Initialise an empty registry with no manifest."""
        self._encodings: dict[str, EncodingDescriptor] = {}
        self._manifest: IncrementalMemoryManifest | None = None

    def register(self, descriptor: EncodingDescriptor) -> None:
        """Add or replace an encoding descriptor in the registry.

        If a descriptor with the same name is already registered it is
        silently replaced.  A debug log message is emitted in both cases.

        Args:
            descriptor: The EncodingDescriptor to register.
        """
        action = "Replacing" if descriptor.name in self._encodings else "Registering"
        logger.debug("%s descriptor %r (status=%s)", action, descriptor.name, descriptor.status.name)
        self._encodings[descriptor.name] = descriptor

    def unregister(self, name: str) -> bool:
        """Remove a descriptor from the registry by name.

        Args:
            name: The name of the descriptor to remove.

        Returns:
            True if the descriptor was found and removed, False if it was not
            present in the registry.
        """
        if name in self._encodings:
            del self._encodings[name]
            logger.debug("Unregistered descriptor %r.", name)
            return True
        logger.debug("Attempted to unregister unknown descriptor %r.", name)
        return False

    def lookup(self, name: str) -> EncodingDescriptor | None:
        """Retrieve a descriptor by name.

        Args:
            name: The name of the descriptor to look up.

        Returns:
            The EncodingDescriptor if found, or None.
        """
        return self._encodings.get(name)

    def list_encodings(self) -> list[EncodingDescriptor]:
        """Return all registered descriptors in insertion order.

        Returns:
            A list of all EncodingDescriptor instances currently in the
            registry.  The list is a snapshot; mutating it does not affect
            the registry.
        """
        return list(self._encodings.values())

    def list_stable(self) -> list[EncodingDescriptor]:
        """Return only the descriptors whose status is STABLE.

        Returns:
            A (possibly empty) list of EncodingDescriptor instances with
            ``status == EncodingStatus.STABLE``.
        """
        return [d for d in self._encodings.values() if d.is_stable()]

    def set_manifest(self, m: IncrementalMemoryManifest) -> None:
        """Associate a manifest with this registry.

        The manifest is stored as-is; no validation is performed here.
        Call ``validate_all`` separately if validation is required.

        Args:
            m: The IncrementalMemoryManifest to associate with this registry.
        """
        self._manifest = m
        logger.debug("Manifest %r attached to registry.", m.manifest_id)

    def get_manifest(self) -> IncrementalMemoryManifest | None:
        """Return the manifest associated with this registry, if any.

        Returns:
            The IncrementalMemoryManifest if one has been set, or None.
        """
        return self._manifest

    def validate_all(self) -> dict[str, list[str]]:
        """Validate every registered descriptor and return all error lists.

        Each descriptor is validated via its own ``validate()`` method.  The
        result is a mapping from descriptor name to a (possibly empty) list of
        error strings.  Descriptors with no errors are still included in the
        result with an empty list for easy programmatic inspection.

        Returns:
            A dict mapping descriptor name to a list of validation error
            messages.
        """
        results: dict[str, list[str]] = {}
        for name, descriptor in self._encodings.items():
            results[name] = descriptor.validate()
        return results

    def summary(self) -> str:
        """Return a human-readable summary of this registry.

        The summary includes the total count of registered descriptors, the
        count of stable ones, and a brief listing of names grouped by status.

        Returns:
            A multi-line string summarising the registry contents.
        """
        all_descs = self.list_encodings()
        stable = [d.name for d in all_descs if d.status == EncodingStatus.STABLE]
        experimental = [d.name for d in all_descs if d.status == EncodingStatus.EXPERIMENTAL]
        deprecated = [d.name for d in all_descs if d.status == EncodingStatus.DEPRECATED]
        draft = [d.name for d in all_descs if d.status == EncodingStatus.DRAFT]
        manifest_id = self._manifest.manifest_id if self._manifest else "None"
        return (
            f"PackageRegistry(\n"
            f"  manifest_id  = {manifest_id}\n"
            f"  total        = {len(all_descs)}\n"
            f"  stable       = {len(stable)}: {stable}\n"
            f"  experimental = {len(experimental)}: {experimental[:5]}\n"
            f"  deprecated   = {len(deprecated)}: {deprecated}\n"
            f"  draft        = {len(draft)}: {draft}\n"
            f")"
        )

    def to_json(self) -> str:
        """Serialize the registry (descriptors + manifest) to a JSON string.

        Returns:
            A JSON string containing all registered descriptors and the
            manifest if one is set.
        """
        descriptors_raw = [json.loads(d.to_json()) for d in self._encodings.values()]
        manifest_raw = json.loads(self._manifest.to_json()) if self._manifest else None
        return json.dumps({
            "encodings": descriptors_raw,
            "manifest": manifest_raw,
        })

    @classmethod
    def from_json(cls, data: str) -> PackageRegistry:
        """Deserialize a PackageRegistry from a JSON string.

        Args:
            data: A JSON string previously produced by ``to_json``.

        Returns:
            A PackageRegistry populated with the descriptors and manifest from
            the JSON payload.
        """
        raw = json.loads(data)
        registry = cls()
        for d_raw in raw.get("encodings", []):
            try:
                descriptor = EncodingDescriptor.from_json(json.dumps(d_raw))
                registry.register(descriptor)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Skipping malformed descriptor during from_json: %s", exc)
        if raw.get("manifest") is not None:
            try:
                manifest = IncrementalMemoryManifest.from_json(json.dumps(raw["manifest"]))
                registry.set_manifest(manifest)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Could not restore manifest during from_json: %s", exc)
        return registry


# ---------------------------------------------------------------------------
# ManifestValidator
# ---------------------------------------------------------------------------

class ManifestValidator:
    """Validates IncrementalMemoryManifest and EncodingDescriptor instances.

    ManifestValidator provides a centralised location for all validation logic
    that concerns the incremental_memory manifest subsystem.  It supplements
    the lightweight ``validate()`` methods on the dataclasses with deeper
    cross-field and cross-descriptor checks.

    The validator is stateless — every call to a validation method is
    independent, making instances safe to share across threads and to reuse
    across multiple validation passes.

    Typical usage::

        validator = ManifestValidator()
        errors = validator.validate_manifest(my_manifest)
        if errors:
            for e in errors:
                logger.error("Manifest error: %s", e)
    """

    def validate_manifest(self, m: IncrementalMemoryManifest) -> list[str]:
        """Validate an IncrementalMemoryManifest comprehensively.

        Runs both the manifest's own ``validate()`` checks and additional
        cross-field checks: exports must be non-empty, and each descriptor
        referenced must also individually validate cleanly.

        Args:
            m: The IncrementalMemoryManifest to validate.

        Returns:
            A list of human-readable error strings.  Empty means valid.
        """
        errors = m.validate()
        if not m.required_subsystems:
            errors.append("IncrementalMemoryManifest.required_subsystems is empty.")
        if not m.chapter_ref or not m.chapter_ref.strip():
            errors.append("IncrementalMemoryManifest.chapter_ref must not be empty.")
        export_errors = self.validate_exports(m.exports)
        errors.extend(export_errors)
        for descriptor in m.encoding_descriptors:
            d_errors = self.validate_descriptor(descriptor)
            for e in d_errors:
                errors.append(f"[descriptor:{descriptor.name}] {e}")
        return errors

    def validate_descriptor(self, d: EncodingDescriptor) -> list[str]:
        """Validate an individual EncodingDescriptor.

        Runs the descriptor's own ``validate()`` method and additionally
        validates the theorem_refs list for correct formatting.

        Args:
            d: The EncodingDescriptor to validate.

        Returns:
            A list of human-readable error strings.  Empty means valid.
        """
        errors = d.validate()
        theorem_errors = self.check_theorem_refs(d.theorem_refs)
        errors.extend(theorem_errors)
        return errors

    def check_theorem_refs(self, refs: list[str]) -> list[str]:
        """Validate that all theorem reference strings use the expected format.

        The accepted format is ``"theorem:X.Y"`` where ``X`` and ``Y`` are
        each non-empty strings (they may be numeric or alphanumeric chapter
        identifiers).  Any reference that does not match is reported as an
        error.

        Args:
            refs: A list of theorem reference strings to check.

        Returns:
            A list of error messages for any malformed references.  Empty
            means all references are well-formed.
        """
        errors: list[str] = []
        for ref in refs:
            if not ref.startswith("theorem:"):
                errors.append(
                    f"Theorem ref {ref!r} does not start with 'theorem:'."
                )
                continue
            tail = ref[len("theorem:"):]
            if "." not in tail:
                errors.append(
                    f"Theorem ref {ref!r} lacks a '.' separator after 'theorem:'."
                )
                continue
            parts = tail.split(".", 1)
            if not parts[0] or not parts[1]:
                errors.append(
                    f"Theorem ref {ref!r} has empty X or Y component."
                )
        return errors

    def validate_exports(self, exports: list[str]) -> list[str]:
        """Validate the exports list for an IncrementalMemoryManifest.

        Rules:
        - Exports must be non-empty.
        - Each entry must be a non-empty, valid Python identifier.
        - There must be no duplicate entries.

        Args:
            exports: The list of export name strings to validate.

        Returns:
            A list of error messages.  Empty means the exports list is valid.
        """
        errors: list[str] = []
        if not exports:
            errors.append("Exports list is empty.")
            return errors
        seen: set[str] = set()
        for name in exports:
            if not name or not name.strip():
                errors.append("Export list contains an empty string entry.")
            elif not name.isidentifier():
                errors.append(f"Export name {name!r} is not a valid Python identifier.")
            elif name in seen:
                errors.append(f"Export name {name!r} is duplicated in the exports list.")
            else:
                seen.add(name)
        return errors


# ---------------------------------------------------------------------------
# build_manifest
# ---------------------------------------------------------------------------

def build_manifest() -> IncrementalMemoryManifest:
    """Build and return the canonical IncrementalMemoryManifest for this package.

    This function constructs the manifest that describes the incremental_memory
    encoding subsystem as specified in theory2.tex Chapter 34.  The manifest
    is created with hard-coded values that reflect the current state of the
    package: author, version, chapter reference, required subsystems, and the
    full list of public exports.

    The exports list includes every public class and function defined across
    the models, geometry, invalidation, orchestration, and theorem modules.
    Callers that need to iterate over or validate exports should use this
    function as the single source of truth.

    Returns:
        A fully-populated IncrementalMemoryManifest instance ready for use.
    """
    exports = [
        "EncodingSupportSet",
        "IncrementalUpdate",
        "ChangeEvent",
        "ChangeEventKind",
        "MemoryInvalidationCascade",
        "InvalidationWaveInfo",
        "PersistentMemoryState",
        "RegionType",
        "ChangeEventStream",
        "ChangeEventBatch",
        "SupportTracker",
        "EventAggregator",
        "ChangeEventSerializer",
        "ChangeEventFilter",
        "OverlapData",
        "RestrictionResult",
        "GlueComputation",
        "RestrictionOperation",
        "OverlapChecker",
        "GlueOperation",
        "UpdateLawProver",
        "CascadePolicy",
        "RepairAction",
        "RepairPlan",
        "InvalidationWave",
        "DependencyTracer",
        "CascadeComputer",
        "CascadeScheduler",
        "GlueAlgorithm",
        "SectionDiffAlgorithm",
        "OverlapResolutionAlgorithm",
        "EpochAdvanceAlgorithm",
        "MemoryCompactionAlgorithm",
        "QuotaEnforcementAlgorithm",
        "SupportMinimizationAlgorithm",
        "BatchUpdateOptimizer",
        "IntegrationHealth",
        "RuntimeMemoryBridge",
        "InvalidationEngineAdapter",
        "MemoryStateExporter",
        "IncrementalUpdatePipeline",
        "IncrementalMemoryIntegration",
        "IncrementalMemoryTheorem",
        "TheoremStatus",
        "ProofStrategy",
        "TheoremStatement",
        "ProofWitness",
        "IncrementalMemoryTheoremRegistry",
    ]

    manifest = IncrementalMemoryManifest(
        version="0.1.0",
        author="jugeo-team",
        chapter_ref="theory2.tex:Ch34",
        theory_section="§34",
        required_subsystems=[
            SubsystemKind.RUNTIME_MEMORY,
            SubsystemKind.INVALIDATION,
            SubsystemKind.GEOMETRY,
            SubsystemKind.EVIDENCE,
            SubsystemKind.JUDGMENTS,
        ],
        exports=exports,
        description=(
            "Encoding layer for incremental semantic memory updates via "
            "Glue construction"
        ),
    )
    logger.debug(
        "Built IncrementalMemoryManifest %s with %d exports.",
        manifest.manifest_id,
        len(exports),
    )
    return manifest


# ---------------------------------------------------------------------------
# validate_manifest (module-level convenience wrapper)
# ---------------------------------------------------------------------------

def validate_manifest(manifest: IncrementalMemoryManifest) -> list[str]:
    """Convenience wrapper: validate a manifest using ManifestValidator.

    This module-level function creates a fresh ManifestValidator and delegates
    to its ``validate_manifest`` method.  It is the preferred entry-point for
    callers that do not need to reuse a validator instance.

    Args:
        manifest: The IncrementalMemoryManifest to validate.

    Returns:
        A list of human-readable error strings.  An empty list means the
        manifest passed all validation checks.
    """
    validator = ManifestValidator()
    return validator.validate_manifest(manifest)


# ---------------------------------------------------------------------------
# Module-level exports
# ---------------------------------------------------------------------------

__all__ = [
    # Enums
    "EncodingStatus",
    "SubsystemKind",
    # Dataclasses
    "EncodingDescriptor",
    "IncrementalMemoryManifest",
    # Classes
    "PackageRegistry",
    "ManifestValidator",
    # Functions
    "build_manifest",
    "validate_manifest",
]
