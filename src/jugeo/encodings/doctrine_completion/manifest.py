"""
Manifest definitions for the doctrine_completion encoding package.

This module is part of JuGeo's copilot-assisted encoding of theory2.tex Chapter 37:
Implementation-complete thesis doctrine — every claim has implementation evidence.

It provides manifest and descriptor classes for cataloguing the doctrine_completion
package's exports, versioning information, and doctrine registries.

Chapter reference: Ch37 — Implementation-Complete Thesis Doctrine.

copilot
"""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

from .models import (
    DoctrineStatement,
    EvidenceKind,
    StatementStatus,
    ClaimType,
)

__all__ = [
    "DoctrineCompletionManifest",
    "DoctrineDescriptor",
    "DoctrineRegistry",
    "build_manifest",
    "validate_manifest",
]


# ---------------------------------------------------------------------------
# DoctrineCompletionManifest
# ---------------------------------------------------------------------------


@dataclass
class DoctrineCompletionManifest:
    """Manifest describing a doctrine_completion encoding package.

    A DoctrineCompletionManifest captures the metadata needed to identify,
    version, and validate a specific instantiation of the doctrine_completion
    encoding.  It lists the exported symbols, the chapter reference, and
    provides validation utilities.

    This manifest is used by the integration layer to ensure that all
    expected exports are present and that the encoding matches the
    specification from theory2.tex Ch37.

    Attributes:
        manifest_id: Unique identifier (uuid4).
        version: Semantic version string for this encoding.
        author: Author or organisation that produced this encoding.
        chapter_ref: Theory chapter reference, defaults to "Ch37".
        theory_section: Fine-grained section within the chapter.
        doctrine_name: Human-readable name of the doctrine encoded.
        exports: List of exported symbol names.
        created_at: Unix timestamp of manifest creation.
        description: Prose description of what this manifest covers.
        tags: Searchable tags for categorisation.
    """

    manifest_id: str
    version: str
    author: str
    chapter_ref: str = "Ch37"
    theory_section: str = ""
    doctrine_name: str = ""
    exports: list[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    description: str = ""
    tags: list[str] = field(default_factory=list)

    # ------------------------------------------------------------------
    # Construction helpers
    # ------------------------------------------------------------------

    @classmethod
    def create(
        cls,
        version: str,
        author: str,
        doctrine_name: str,
        theory_section: str = "",
        exports: Optional[list[str]] = None,
        description: str = "",
        tags: Optional[list[str]] = None,
        chapter_ref: str = "Ch37",
    ) -> DoctrineCompletionManifest:
        """Factory method that auto-generates a UUID and current timestamp.

        Args:
            version: Semantic version string.
            author: Author identifier.
            doctrine_name: Name of the doctrine being encoded.
            theory_section: Optional sub-section reference within Ch37.
            exports: Initial list of exported symbol names.
            description: Human-readable prose description.
            tags: Searchable tags.
            chapter_ref: Chapter reference string (default "Ch37").

        Returns:
            A new DoctrineCompletionManifest with generated ID.
        """
        return cls(
            manifest_id=str(uuid.uuid4()),
            version=version,
            author=author,
            chapter_ref=chapter_ref,
            theory_section=theory_section,
            doctrine_name=doctrine_name,
            exports=list(exports or []),
            created_at=time.time(),
            description=description,
            tags=list(tags or []),
        )

    # ------------------------------------------------------------------
    # Core methods
    # ------------------------------------------------------------------

    def to_json(self) -> str:
        """Serialise this manifest to a JSON string.

        Produces a fully round-trippable JSON representation of all
        manifest fields.

        Returns:
            JSON-encoded string.
        """
        data = {
            "manifest_id": self.manifest_id,
            "version": self.version,
            "author": self.author,
            "chapter_ref": self.chapter_ref,
            "theory_section": self.theory_section,
            "doctrine_name": self.doctrine_name,
            "exports": self.exports,
            "created_at": self.created_at,
            "description": self.description,
            "tags": self.tags,
        }
        return json.dumps(data, indent=2)

    @classmethod
    def from_json(cls, data: str) -> DoctrineCompletionManifest:
        """Deserialise a DoctrineCompletionManifest from a JSON string.

        Args:
            data: JSON string produced by to_json().

        Returns:
            A reconstructed DoctrineCompletionManifest instance.
        """
        obj = json.loads(data)
        return cls(
            manifest_id=obj["manifest_id"],
            version=obj["version"],
            author=obj["author"],
            chapter_ref=obj.get("chapter_ref", "Ch37"),
            theory_section=obj.get("theory_section", ""),
            doctrine_name=obj.get("doctrine_name", ""),
            exports=obj.get("exports", []),
            created_at=obj.get("created_at", time.time()),
            description=obj.get("description", ""),
            tags=obj.get("tags", []),
        )

    def validate(self) -> tuple[bool, list[str]]:
        """Validate that all required manifest fields are non-empty.

        Checks:
        - manifest_id, version, author, chapter_ref must be non-empty strings.
        - doctrine_name must be non-empty.
        - exports must be a non-empty list.
        - version must match basic semver pattern (digits and dots).

        Returns:
            A (is_valid, errors) tuple.
        """
        errors: list[str] = []
        if not self.manifest_id:
            errors.append("manifest_id must not be empty")
        if not self.version:
            errors.append("version must not be empty")
        elif not all(c.isdigit() or c in ".+-" for c in self.version):
            # Loose check: allow digits, dots, dashes, plus signs
            errors.append(f"version '{self.version}' does not look like a semver string")
        if not self.author:
            errors.append("author must not be empty")
        if not self.chapter_ref:
            errors.append("chapter_ref must not be empty")
        if not self.doctrine_name:
            errors.append("doctrine_name must not be empty")
        if not self.exports:
            errors.append("exports must contain at least one entry")
        return (len(errors) == 0, errors)

    def summarize(self) -> str:
        """Return a human-readable one-line summary of this manifest.

        Includes the manifest ID prefix, doctrine name, version, author,
        and chapter reference.

        Returns:
            Concise summary string.
        """
        return (
            f"[MANIFEST {self.manifest_id[:8]}] '{self.doctrine_name}' "
            f"v{self.version} by {self.author} ({self.chapter_ref}) "
            f"| {len(self.exports)} exports"
        )

    def add_export(self, name: str) -> None:
        """Add a new symbol to the exports list if not already present.

        Args:
            name: Symbol name to add to the exports list.
        """
        if name not in self.exports:
            self.exports.append(name)

    def get_chapter_ref(self) -> str:
        """Return the chapter reference string.

        Returns:
            The chapter_ref field value (e.g. "Ch37").
        """
        return self.chapter_ref


# ---------------------------------------------------------------------------
# DoctrineDescriptor
# ---------------------------------------------------------------------------


@dataclass
class DoctrineDescriptor:
    """Descriptor for a doctrine within the implementation-complete thesis.

    A DoctrineDescriptor captures the identity, requirements, and metadata
    for a single doctrine.  Descriptors are stored in a DoctrineRegistry
    and are used to validate that sufficient evidence has been collected.

    Attributes:
        doctrine_id: Unique identifier (uuid4).
        name: Human-readable doctrine name.
        description: Prose description of the doctrine's purpose.
        grounding_requirements: List of evidence kind names that must be present.
        created_at: Unix timestamp of creation.
        priority: Integer priority (lower = higher priority).
        tags: Searchable tags.
    """

    doctrine_id: str
    name: str
    description: str
    grounding_requirements: list[str]
    created_at: float
    priority: int
    tags: list[str] = field(default_factory=list)

    @classmethod
    def create(
        cls,
        name: str,
        description: str,
        grounding_requirements: list[str],
        priority: int = 5,
        tags: Optional[list[str]] = None,
    ) -> DoctrineDescriptor:
        """Factory method with auto-generated ID and current timestamp.

        Args:
            name: Doctrine name.
            description: Prose description.
            grounding_requirements: Required evidence kind names.
            priority: Integer priority (default 5).
            tags: Optional searchable tags.

        Returns:
            A new DoctrineDescriptor instance.
        """
        return cls(
            doctrine_id=str(uuid.uuid4()),
            name=name,
            description=description,
            grounding_requirements=list(grounding_requirements),
            created_at=time.time(),
            priority=priority,
            tags=list(tags or []),
        )

    def to_json(self) -> str:
        """Serialise to JSON string.

        Returns:
            JSON-encoded string of all descriptor fields.
        """
        data = {
            "doctrine_id": self.doctrine_id,
            "name": self.name,
            "description": self.description,
            "grounding_requirements": self.grounding_requirements,
            "created_at": self.created_at,
            "priority": self.priority,
            "tags": self.tags,
        }
        return json.dumps(data, indent=2)

    @classmethod
    def from_json(cls, data: str) -> DoctrineDescriptor:
        """Deserialise from a JSON string.

        Args:
            data: JSON string produced by to_json().

        Returns:
            A reconstructed DoctrineDescriptor.
        """
        obj = json.loads(data)
        return cls(
            doctrine_id=obj["doctrine_id"],
            name=obj["name"],
            description=obj["description"],
            grounding_requirements=obj.get("grounding_requirements", []),
            created_at=obj.get("created_at", time.time()),
            priority=obj.get("priority", 5),
            tags=obj.get("tags", []),
        )

    def is_satisfied(self, evidence_kinds: list[str]) -> bool:
        """Return True if all grounding requirements are present in evidence_kinds.

        Args:
            evidence_kinds: List of evidence kind name strings currently available.

        Returns:
            True when every required kind is in the evidence_kinds list.
        """
        available_set = set(evidence_kinds)
        return all(req in available_set for req in self.grounding_requirements)

    def get_unsatisfied_requirements(self, evidence_kinds: list[str]) -> list[str]:
        """Return the list of requirements not present in evidence_kinds.

        Args:
            evidence_kinds: List of evidence kind name strings currently available.

        Returns:
            List of requirement strings that are still missing.
        """
        available_set = set(evidence_kinds)
        return [req for req in self.grounding_requirements if req not in available_set]

    def summarize(self) -> str:
        """Return a human-readable one-line summary of this descriptor.

        Returns:
            Concise summary string including name, priority, and requirement count.
        """
        req_count = len(self.grounding_requirements)
        return (
            f"[DESCRIPTOR {self.doctrine_id[:8]}] '{self.name}' "
            f"priority={self.priority} requirements={req_count} "
            f"tags={self.tags}"
        )


# ---------------------------------------------------------------------------
# DoctrineRegistry
# ---------------------------------------------------------------------------


class DoctrineRegistry:
    """Registry for managing a collection of DoctrineDescriptors.

    The DoctrineRegistry provides CRUD operations for DoctrineDescriptor
    objects and supports querying by tag, counting, and bulk validation.
    It is used by the integration layer to enumerate all known doctrines
    and verify that they are satisfied by collected evidence.

    Example usage::

        registry = DoctrineRegistry()
        descriptor = DoctrineDescriptor.create(
            name="Implementation Completeness",
            description="Every claim has implementation evidence.",
            grounding_requirements=["code", "test"],
        )
        registry.register(descriptor)
        found = registry.lookup(descriptor.doctrine_id)
        print(found.summarize())
    """

    def __init__(self) -> None:
        """Initialise an empty registry with a generated ID and timestamp.

        The internal store is a plain dict mapping doctrine_id -> descriptor.
        The registry_id is generated via uuid4 and the created_at timestamp
        records the moment of instantiation.
        """
        self._store: dict[str, DoctrineDescriptor] = {}
        self._registry_id: str = str(uuid.uuid4())
        self._created_at: float = time.time()

    @property
    def registry_id(self) -> str:
        """Return the unique registry identifier.

        Returns:
            UUID string for this registry instance.
        """
        return self._registry_id

    def register(self, descriptor: DoctrineDescriptor) -> None:
        """Register a descriptor in the registry.

        If a descriptor with the same doctrine_id already exists, it is
        overwritten with the new descriptor.

        Args:
            descriptor: The DoctrineDescriptor to register.
        """
        self._store[descriptor.doctrine_id] = descriptor

    def lookup(self, doctrine_id: str) -> DoctrineDescriptor:
        """Look up a descriptor by its doctrine_id.

        Args:
            doctrine_id: The ID to look up.

        Returns:
            The matching DoctrineDescriptor.

        Raises:
            KeyError: If no descriptor with the given ID is registered.
        """
        if doctrine_id not in self._store:
            raise KeyError(
                f"No DoctrineDescriptor with id='{doctrine_id}' found in registry {self._registry_id}"
            )
        return self._store[doctrine_id]

    def list_all(self) -> list[DoctrineDescriptor]:
        """Return all registered descriptors sorted by priority then name.

        Returns:
            Sorted list of DoctrineDescriptor instances.
        """
        return sorted(self._store.values(), key=lambda d: (d.priority, d.name))

    def list_by_tag(self, tag: str) -> list[DoctrineDescriptor]:
        """Return all descriptors that have the given tag.

        Args:
            tag: Tag string to filter by.

        Returns:
            List of matching DoctrineDescriptors.
        """
        return [d for d in self._store.values() if tag in d.tags]

    def remove(self, doctrine_id: str) -> None:
        """Remove a descriptor from the registry.

        Args:
            doctrine_id: ID of the descriptor to remove.

        Raises:
            KeyError: If no such descriptor is registered.
        """
        if doctrine_id not in self._store:
            raise KeyError(f"Cannot remove: doctrine_id='{doctrine_id}' not found")
        del self._store[doctrine_id]

    def count(self) -> int:
        """Return the number of registered descriptors.

        Returns:
            Integer count of descriptors in the registry.
        """
        return len(self._store)

    def to_json(self) -> str:
        """Serialise the entire registry to a JSON string.

        Returns:
            JSON-encoded string of registry metadata and all descriptors.
        """
        data = {
            "registry_id": self._registry_id,
            "created_at": self._created_at,
            "descriptors": [json.loads(d.to_json()) for d in self._store.values()],
        }
        return json.dumps(data, indent=2)

    @classmethod
    def from_json(cls, data: str) -> DoctrineRegistry:
        """Deserialise a DoctrineRegistry from a JSON string.

        Args:
            data: JSON string produced by to_json().

        Returns:
            A populated DoctrineRegistry instance.
        """
        obj = json.loads(data)
        registry = cls()
        registry._registry_id = obj.get("registry_id", registry._registry_id)
        registry._created_at = obj.get("created_at", registry._created_at)
        for desc_obj in obj.get("descriptors", []):
            registry.register(DoctrineDescriptor.from_json(json.dumps(desc_obj)))
        return registry

    def validate_all(self) -> dict[str, tuple[bool, list[str]]]:
        """Validate all registered descriptors.

        Runs basic field validation on each descriptor (non-empty name,
        description, and at least one grounding requirement).

        Returns:
            Dict mapping doctrine_id to (is_valid, errors) tuples.
        """
        results: dict[str, tuple[bool, list[str]]] = {}
        for did, desc in self._store.items():
            errors: list[str] = []
            if not desc.name:
                errors.append("name must not be empty")
            if not desc.description:
                errors.append("description must not be empty")
            if not desc.grounding_requirements:
                errors.append("grounding_requirements must not be empty")
            results[did] = (len(errors) == 0, errors)
        return results

    def summarize(self) -> str:
        """Return a human-readable summary of the registry.

        Returns:
            Concise summary including registry ID, count, and priority breakdown.
        """
        counts_by_priority: dict[int, int] = {}
        for desc in self._store.values():
            counts_by_priority[desc.priority] = counts_by_priority.get(desc.priority, 0) + 1
        priority_str = ", ".join(
            f"p{p}:{c}" for p, c in sorted(counts_by_priority.items())
        )
        return (
            f"[REGISTRY {self._registry_id[:8]}] "
            f"{self.count()} descriptors [{priority_str}]"
        )


# ---------------------------------------------------------------------------
# Module-level functions
# ---------------------------------------------------------------------------


def build_manifest(
    doctrine_name: str,
    author: str,
    version: str,
    theory_section: str,
    exports: list[str],
    description: str = "",
    tags: Optional[list[str]] = None,
    chapter_ref: str = "Ch37",
) -> DoctrineCompletionManifest:
    """Build and return a new DoctrineCompletionManifest.

    Convenience wrapper around DoctrineCompletionManifest.create().

    Args:
        doctrine_name: Name of the doctrine being encoded.
        author: Author identifier.
        version: Semantic version string.
        theory_section: Section reference within Ch37.
        exports: List of exported symbol names.
        description: Optional prose description.
        tags: Optional searchable tags.
        chapter_ref: Chapter reference (default "Ch37").

    Returns:
        A new DoctrineCompletionManifest with a generated UUID.
    """
    manifest = DoctrineCompletionManifest.create(
        version=version,
        author=author,
        doctrine_name=doctrine_name,
        theory_section=theory_section,
        exports=exports,
        description=description or (
            f"Implementation-complete thesis doctrine manifest for '{doctrine_name}' "
            f"from {chapter_ref} section '{theory_section}'."
        ),
        tags=tags or [],
        chapter_ref=chapter_ref,
    )
    return manifest


def validate_manifest(manifest: DoctrineCompletionManifest) -> tuple[bool, list[str]]:
    """Validate a DoctrineCompletionManifest and return (is_valid, errors).

    Delegates to DoctrineCompletionManifest.validate() and additionally
    checks that the chapter_ref matches the expected "Ch37" value for
    this package.

    Args:
        manifest: The manifest to validate.

    Returns:
        A (bool, list[str]) tuple where the bool is True if valid.
    """
    is_valid, errors = manifest.validate()
    if manifest.chapter_ref != "Ch37":
        errors.append(
            f"chapter_ref must be 'Ch37' for this package, got '{manifest.chapter_ref}'"
        )
        is_valid = False
    return (is_valid, errors)
