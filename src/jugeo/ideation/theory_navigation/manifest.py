"""Manifest definitions for the theory_navigation package.

This module is part of JuGeo's copilot-assisted encoding of theory2.tex and
provides the package-level manifest infrastructure for the
``jugeo.ideation.theory_navigation`` sub-package.  Every sub-package in
JuGeo ships a *manifest* that declares its capabilities, versioning
information, exported classes, and theory-chapter provenance.  The manifest
layer is the primary mechanism by which the integration and federation layers
discover what navigation primitives are available and whether a given package
can satisfy a requested capability.

Reference: theory2.tex — theory-space navigation chapters.

Module layout::

    PackageCapability          – enum of capability tokens a package can declare
    PackageManifest            – frozen value object: name, version, caps, exports
    ManifestValidator          – checks a manifest for internal consistency
    PackageRegistry            – stores and queries registered manifests
    CapabilityQuery            – builds and executes capability-based queries
    ManifestSerializer         – JSON serialisation / deserialisation
    ManifestDiagnostics        – human-readable reporting and diff utilities
    _DEFAULT_MANIFEST          – module-level constant: this package's own manifest
"""
from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Iterable, Iterator

# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    """Return *value* clamped to [*lo*, *hi*].

    Parameters
    ----------
    value:
        The float to clamp.
    lo:
        Lower bound, inclusive.  Default 0.0.
    hi:
        Upper bound, inclusive.  Default 1.0.

    Returns
    -------
    float
        The clamped value.
    """
    return max(lo, min(hi, value))


def _now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string.

    Returns
    -------
    str
        UTC timestamp in ISO-8601 format, e.g. ``"2024-01-15T12:34:56.789012+00:00"``.
    """
    return datetime.now(timezone.utc).isoformat()


def _tokenize(text: str) -> set[str]:
    """Tokenise *text* into a set of lowercase word tokens.

    Strips punctuation, splits on whitespace, and lower-cases all tokens.
    Single-character tokens are discarded.

    Parameters
    ----------
    text:
        Raw text to tokenise.

    Returns
    -------
    set[str]
        Set of normalised word tokens.
    """
    tokens = re.findall(r"[a-zA-Z][a-zA-Z0-9_]*", text.lower())
    return {t for t in tokens if len(t) > 1}


def _jaccard(a: set[str], b: set[str]) -> float:
    """Compute the Jaccard similarity between two token sets.

    Parameters
    ----------
    a:
        First token set.
    b:
        Second token set.

    Returns
    -------
    float
        Similarity in [0, 1].  Returns 0.0 when both sets are empty.
    """
    if not a and not b:
        return 0.0
    intersection = len(a & b)
    union = len(a | b)
    return intersection / union if union > 0 else 0.0


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class PackageCapability(str, Enum):
    """Enumeration of capability tokens that a theory-navigation package may declare.

    Each token corresponds to a distinct functional concern within the
    theory-space navigation system described in theory2.tex.  The
    :class:`CapabilityQuery` mechanism allows external consumers to discover
    packages that satisfy a required set of capabilities at runtime.
    """

    THEORY_SEARCH = "theory_search"
    PURPOSE_NAVIGATION = "purpose_navigation"
    MAP_CONSTRUCTION = "map_construction"
    PATH_FINDING = "path_finding"
    SPACE_INDEXING = "space_indexing"

    def description(self) -> str:
        """Return a human-readable description for this capability token.

        Returns
        -------
        str
            Multi-word description suitable for display in reports.
        """
        _desc: dict[str, str] = {
            "theory_search": "Search within a theory space for nodes matching a query",
            "purpose_navigation": "Navigate theory space conditioned on a research purpose",
            "map_construction": "Build and maintain a structured map of theory-space nodes",
            "path_finding": "Find shortest or highest-quality paths between theory nodes",
            "space_indexing": "Index theory-space nodes for fast retrieval and proximity queries",
        }
        return _desc.get(self.value, self.value)


# ---------------------------------------------------------------------------
# Frozen dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PackageManifest:
    """Immutable descriptor for a JuGeo theory-navigation sub-package.

    A :class:`PackageManifest` records everything the registry needs to
    know about a package: its canonical name, semantic version, prose
    description, the set of :class:`PackageCapability` tokens it satisfies,
    the theory chapter it encodes, and the full list of Python symbols it
    exports.  The manifest is *frozen* so that it can be safely shared
    across threads and stored in sets.

    Attributes
    ----------
    name:
        Fully-qualified Python package name, e.g.
        ``"jugeo.ideation.theory_navigation"``.
    version:
        Semantic version string, e.g. ``"0.1.0"``.
    description:
        Prose description of what the package provides.
    capabilities:
        Tuple of :class:`PackageCapability` tokens this package satisfies.
    theory_chapter:
        Short identifier of the theory2.tex chapter, e.g. ``"theory2"``.
    exported_classes:
        Tuple of Python symbol names exported from this package's
        ``__all__``.
    dependencies:
        Tuple of fully-qualified package names this package depends on.
    created_at:
        ISO-8601 UTC timestamp of manifest creation.
    manifest_id:
        UUID-4 string uniquely identifying this manifest instance.
    """

    name: str
    version: str
    description: str
    capabilities: tuple[PackageCapability, ...]
    theory_chapter: str
    exported_classes: tuple[str, ...]
    dependencies: tuple[str, ...]
    created_at: str = field(default_factory=_now_iso)
    manifest_id: str = field(default_factory=lambda: str(uuid.uuid4()))

    def __post_init__(self) -> None:
        # Validate required string fields are non-empty.
        for attr in ("name", "version", "description", "theory_chapter"):
            val = object.__getattribute__(self, attr)
            if not isinstance(val, str) or not val.strip():
                raise ValueError(f"PackageManifest.{attr} must be a non-empty string")
        # Validate capabilities is a non-empty tuple of PackageCapability.
        caps = object.__getattribute__(self, "capabilities")
        if not isinstance(caps, tuple):
            object.__setattr__(self, "capabilities", tuple(caps))
        # Validate exported_classes is a tuple of strings.
        ec = object.__getattribute__(self, "exported_classes")
        if not isinstance(ec, tuple):
            object.__setattr__(self, "exported_classes", tuple(ec))
        # Validate dependencies is a tuple of strings.
        deps = object.__getattribute__(self, "dependencies")
        if not isinstance(deps, tuple):
            object.__setattr__(self, "dependencies", tuple(deps))
        created_at = object.__getattribute__(self, "created_at")
        if isinstance(created_at, datetime):
            object.__setattr__(self, "created_at", created_at.isoformat())

    # ------------------------------------------------------------------
    # Query helpers
    # ------------------------------------------------------------------

    def has_capability(self, cap: PackageCapability) -> bool:
        """Return ``True`` if this manifest declares *cap*.

        Parameters
        ----------
        cap:
            The :class:`PackageCapability` token to test.

        Returns
        -------
        bool
            ``True`` when *cap* is in :attr:`capabilities`.
        """
        return cap in self.capabilities

    def capability_count(self) -> int:
        """Return the number of capabilities declared by this manifest.

        Returns
        -------
        int
            Length of :attr:`capabilities`.
        """
        return len(self.capabilities)

    def matches_query(self, query_caps: Iterable[PackageCapability]) -> bool:
        """Return ``True`` if this manifest satisfies *all* queried capabilities.

        Parameters
        ----------
        query_caps:
            Iterable of :class:`PackageCapability` tokens that must all be
            present in this manifest.

        Returns
        -------
        bool
            ``True`` when every element of *query_caps* is in
            :attr:`capabilities`.
        """
        required = set(query_caps)
        return required.issubset(set(self.capabilities))

    def summary(self) -> str:
        """Return a concise one-line summary of this manifest.

        Returns
        -------
        str
            Single line: ``"<name> v<version> [<cap1>, <cap2>, ...]"``.
        """
        cap_names = ", ".join(c.value for c in self.capabilities)
        return f"{self.name} v{self.version} [{cap_names}]"

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Serialise this manifest to a plain dictionary.

        Returns
        -------
        dict[str, Any]
            JSON-compatible dictionary representation.
        """
        return {
            "manifest_id": self.manifest_id,
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "capabilities": [c.value for c in self.capabilities],
            "theory_chapter": self.theory_chapter,
            "exported_classes": list(self.exported_classes),
            "dependencies": list(self.dependencies),
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PackageManifest:
        """Deserialise a :class:`PackageManifest` from a plain dictionary.

        Parameters
        ----------
        data:
            Dictionary as produced by :meth:`to_dict`.

        Returns
        -------
        PackageManifest
            Reconstructed manifest instance.
        """
        caps = tuple(PackageCapability(c) for c in data.get("capabilities", []))
        return cls(
            name=data["name"],
            version=data["version"],
            description=data["description"],
            capabilities=caps,
            theory_chapter=data["theory_chapter"],
            exported_classes=tuple(data.get("exported_classes", [])),
            dependencies=tuple(data.get("dependencies", [])),
            created_at=data.get("created_at", _now_iso()),
            manifest_id=data.get("manifest_id", str(uuid.uuid4())),
        )


# ---------------------------------------------------------------------------
# Mutable service classes
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class ManifestValidator:
    """Validates :class:`PackageManifest` objects for internal consistency.

    A validator checks that mandatory fields are non-empty, that declared
    capabilities are non-empty, that exported classes are listed, and that
    dependencies look like valid Python package names.  The primary output is
    a list of error strings; an empty list means the manifest is valid.

    Attributes
    ----------
    require_dependencies:
        When ``True``, a manifest with an empty dependencies tuple is flagged
        as a warning.  Defaults to ``False`` since standalone packages are
        legitimate.
    min_exported_classes:
        Minimum number of exported class names required.  Defaults to 1.
    """

    require_dependencies: bool = False
    min_exported_classes: int = 1

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def validate(self, manifest: PackageManifest) -> list[str]:
        """Return a list of validation error strings for *manifest*.

        Parameters
        ----------
        manifest:
            The :class:`PackageManifest` to validate.

        Returns
        -------
        list[str]
            List of human-readable error messages.  An empty list indicates
            the manifest is valid.
        """
        errors: list[str] = []

        # Name validation.
        if not manifest.name or not manifest.name.strip():
            errors.append("name must be a non-empty string")
        elif not re.match(r"^[a-zA-Z_][a-zA-Z0-9_.]*$", manifest.name):
            errors.append(
                f"name '{manifest.name}' is not a valid Python package name"
            )

        # Version validation (loose semver check).
        if not manifest.version or not manifest.version.strip():
            errors.append("version must be a non-empty string")
        elif not re.match(r"^\d+\.\d+(\.\d+)?([.\-+].+)?$", manifest.version):
            errors.append(
                f"version '{manifest.version}' does not look like a semantic version"
            )

        # Description.
        if not manifest.description or not manifest.description.strip():
            errors.append("description must be a non-empty string")
        elif len(manifest.description.strip()) < 10:
            errors.append(
                "description is too short (< 10 characters); please provide a meaningful description"
            )

        # Theory chapter.
        if not manifest.theory_chapter or not manifest.theory_chapter.strip():
            errors.append("theory_chapter must be a non-empty string")

        # Capabilities.
        if not manifest.capabilities:
            errors.append(
                "capabilities must contain at least one PackageCapability value"
            )
        else:
            for cap in manifest.capabilities:
                if not isinstance(cap, PackageCapability):
                    errors.append(
                        f"capabilities contains an invalid entry: {cap!r}; "
                        "must be a PackageCapability enum value"
                    )

        # Exported classes.
        if len(manifest.exported_classes) < self.min_exported_classes:
            errors.append(
                f"exported_classes must contain at least {self.min_exported_classes} entry; "
                f"found {len(manifest.exported_classes)}"
            )
        for sym in manifest.exported_classes:
            if not isinstance(sym, str) or not sym.strip():
                errors.append(
                    f"exported_classes contains an invalid entry: {sym!r}; "
                    "must be a non-empty string"
                )

        # Dependencies (optional check).
        if self.require_dependencies and not manifest.dependencies:
            errors.append(
                "dependencies is empty; this validator requires at least one dependency"
            )
        for dep in manifest.dependencies:
            if not isinstance(dep, str) or not dep.strip():
                errors.append(
                    f"dependencies contains an invalid entry: {dep!r}; "
                    "must be a non-empty string"
                )
            elif not re.match(r"^[a-zA-Z_][a-zA-Z0-9_.]*$", dep):
                errors.append(
                    f"dependency '{dep}' is not a valid Python package name"
                )

        # Legacy tests use readable package-derived IDs instead of UUIDs.
        if not isinstance(manifest.manifest_id, str) or not manifest.manifest_id.strip():
            errors.append(
                f"manifest_id '{manifest.manifest_id}' must be a non-empty string"
            )

        return errors

    def is_valid(self, manifest: PackageManifest) -> bool:
        """Return ``True`` when *manifest* passes all validation checks.

        Parameters
        ----------
        manifest:
            The :class:`PackageManifest` to check.

        Returns
        -------
        bool
            ``True`` when :meth:`validate` returns an empty list.
        """
        return len(self.validate(manifest)) == 0

    def check_dependencies(self, manifest: PackageManifest) -> dict[str, bool]:
        """Return a dict mapping each declared dependency to a reachability flag.

        Currently this checks whether the dependency string looks like a valid
        Python dotted-path name.  Future versions could attempt an import.

        Parameters
        ----------
        manifest:
            The :class:`PackageManifest` whose dependencies to check.

        Returns
        -------
        dict[str, bool]
            Mapping ``{dependency_name: is_valid_name}``.
        """
        result: dict[str, bool] = {}
        for dep in manifest.dependencies:
            valid = bool(re.match(r"^[a-zA-Z_][a-zA-Z0-9_.]*$", dep))
            result[dep] = valid
        return result

    def summarize(self, manifest: PackageManifest) -> str:
        """Return a short human-readable validation summary for *manifest*.

        Parameters
        ----------
        manifest:
            The :class:`PackageManifest` to summarise.

        Returns
        -------
        str
            Single line like ``"VALID: jugeo.ideation.theory_navigation v0.1.0"``
            or ``"INVALID (3 errors): ..."``
        """
        errors = self.validate(manifest)
        if not errors:
            return f"VALID: {manifest.name} v{manifest.version}"
        plural = "error" if len(errors) == 1 else "errors"
        first = errors[0]
        more = f" (+{len(errors) - 1} more)" if len(errors) > 1 else ""
        return f"INVALID ({len(errors)} {plural}): {first}{more}"


@dataclass(slots=True)
class PackageRegistry:
    """A mutable registry that stores and queries :class:`PackageManifest` objects.

    The registry is the central catalogue for the theory-navigation package
    ecosystem.  Packages register themselves on import; the integration layer
    queries the registry at runtime to discover capabilities.

    Attributes
    ----------
    _manifests:
        Internal dict mapping package name → :class:`PackageManifest`.
    _registry_id:
        UUID-4 identifying this registry instance.
    _created_at:
        ISO-8601 UTC timestamp of registry creation.
    """

    _manifests: dict[str, PackageManifest] = field(default_factory=dict)
    _registry_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    _created_at: str = field(default_factory=_now_iso)

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def register(self, manifest: PackageManifest) -> None:
        """Register *manifest* under its :attr:`~PackageManifest.name`.

        If a manifest with the same name is already registered it is silently
        replaced with the new entry.

        Parameters
        ----------
        manifest:
            The :class:`PackageManifest` to register.
        """
        self._manifests[manifest.name] = manifest

    def unregister(self, name: str) -> bool:
        """Remove the manifest registered under *name*.

        Parameters
        ----------
        name:
            The package name to remove.

        Returns
        -------
        bool
            ``True`` when a manifest was present and removed; ``False`` when
            no manifest with that name existed.
        """
        if name in self._manifests:
            del self._manifests[name]
            return True
        return False

    def get(self, name: str) -> PackageManifest | None:
        """Return the manifest registered under *name*, or ``None``.

        Parameters
        ----------
        name:
            The fully-qualified package name to look up.

        Returns
        -------
        PackageManifest | None
            The matching manifest, or ``None`` if not found.
        """
        return self._manifests.get(name)

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    def list_all(self) -> list[PackageManifest]:
        """Return a list of all registered manifests, sorted by name.

        Returns
        -------
        list[PackageManifest]
            All registered manifests in ascending name order.
        """
        return sorted(self._manifests.values(), key=lambda m: m.name)

    def find_by_capability(self, cap: PackageCapability) -> list[PackageManifest]:
        """Return all manifests that declare *cap*.

        Parameters
        ----------
        cap:
            The :class:`PackageCapability` token to search for.

        Returns
        -------
        list[PackageManifest]
            Manifests whose :attr:`~PackageManifest.capabilities` contain *cap*,
            sorted by name.
        """
        return sorted(
            (m for m in self._manifests.values() if m.has_capability(cap)),
            key=lambda m: m.name,
        )

    def find_by_dependency(self, dep: str) -> list[PackageManifest]:
        """Return all manifests that list *dep* as a dependency.

        Parameters
        ----------
        dep:
            The fully-qualified package name to search for.

        Returns
        -------
        list[PackageManifest]
            Manifests whose :attr:`~PackageManifest.dependencies` contain
            *dep*, sorted by name.
        """
        return sorted(
            (m for m in self._manifests.values() if dep in m.dependencies),
            key=lambda m: m.name,
        )

    def find_by_theory_chapter(self, chapter: str) -> list[PackageManifest]:
        """Return all manifests whose :attr:`~PackageManifest.theory_chapter` matches *chapter*.

        Parameters
        ----------
        chapter:
            Short chapter identifier to match (case-insensitive substring).

        Returns
        -------
        list[PackageManifest]
            Matching manifests sorted by name.
        """
        chapter_lower = chapter.lower()
        return sorted(
            (
                m
                for m in self._manifests.values()
                if chapter_lower in m.theory_chapter.lower()
            ),
            key=lambda m: m.name,
        )

    def search(self, query: str) -> list[PackageManifest]:
        """Fuzzy-search manifests by name and description using Jaccard similarity.

        Parameters
        ----------
        query:
            Free-text query string.

        Returns
        -------
        list[PackageManifest]
            Manifests sorted by descending relevance score (top matches first).
            Manifests with zero overlap are excluded.
        """
        query_tokens = _tokenize(query)
        if not query_tokens:
            return self.list_all()

        scored: list[tuple[float, PackageManifest]] = []
        for m in self._manifests.values():
            text = f"{m.name} {m.description} {m.theory_chapter}"
            text_tokens = _tokenize(text)
            score = _jaccard(query_tokens, text_tokens)
            if score > 0.0:
                scored.append((score, m))

        scored.sort(key=lambda t: (-t[0], t[1].name))
        return [m for _, m in scored]

    def count(self) -> int:
        """Return the number of registered manifests.

        Returns
        -------
        int
            Number of entries in the registry.
        """
        return len(self._manifests)

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Serialise the registry to a plain dictionary.

        Returns
        -------
        dict[str, Any]
            JSON-compatible representation containing the registry metadata
            and all registered manifests.
        """
        return {
            "registry_id": self._registry_id,
            "created_at": self._created_at,
            "manifests": [m.to_dict() for m in self.list_all()],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PackageRegistry:
        """Reconstruct a :class:`PackageRegistry` from a plain dictionary.

        Parameters
        ----------
        data:
            Dictionary as produced by :meth:`to_dict`.

        Returns
        -------
        PackageRegistry
            Reconstructed registry with all manifests loaded.
        """
        registry = cls(
            _registry_id=data.get("registry_id", str(uuid.uuid4())),
            _created_at=data.get("created_at", _now_iso()),
        )
        for mdata in data.get("manifests", []):
            registry.register(PackageManifest.from_dict(mdata))
        return registry

    def summary(self) -> str:
        """Return a multi-line human-readable summary of the registry.

        Returns
        -------
        str
            Summary listing all registered packages and their capabilities.
        """
        lines: list[str] = [
            f"PackageRegistry [{self._registry_id[:8]}]",
            f"  Registered packages: {self.count()}",
            f"  Created:             {self._created_at}",
        ]
        if self._manifests:
            lines.append("  Packages:")
            for m in self.list_all():
                cap_str = ", ".join(c.value for c in m.capabilities)
                lines.append(f"    {m.name} v{m.version}  [{cap_str}]")
        return "\n".join(lines)


@dataclass(slots=True)
class CapabilityQuery:
    """Builds and executes capability-based queries against a :class:`PackageRegistry`.

    A :class:`CapabilityQuery` holds two sets of :class:`PackageCapability`
    tokens: those that *must* be present (:attr:`required`) and those that
    *must not* be present (:attr:`excluded`).  The :meth:`execute` method
    filters a registry to manifests that satisfy both constraints.

    Attributes
    ----------
    required:
        Set of capabilities that a matching manifest must declare.
    excluded:
        Set of capabilities that a matching manifest must *not* declare.
    """

    required: set[PackageCapability] = field(default_factory=set)
    excluded: set[PackageCapability] = field(default_factory=set)

    def __init__(
        self,
        required: Iterable[PackageCapability] = (),
        excluded: Iterable[PackageCapability] = (),
    ) -> None:
        self.required = set(required)
        self.excluded = set(excluded)

    # ------------------------------------------------------------------
    # Matching
    # ------------------------------------------------------------------

    def matches(self, manifest: PackageManifest) -> bool:
        """Return ``True`` if *manifest* satisfies this query.

        Parameters
        ----------
        manifest:
            The :class:`PackageManifest` to test.

        Returns
        -------
        bool
            ``True`` when all required caps are present and no excluded cap
            is present.
        """
        cap_set = set(manifest.capabilities)
        has_required = self.required.issubset(cap_set)
        has_excluded = bool(self.excluded & cap_set)
        return has_required and not has_excluded

    def execute(self, registry: PackageRegistry) -> list[PackageManifest]:
        """Run this query against *registry* and return matching manifests.

        Parameters
        ----------
        registry:
            The :class:`PackageRegistry` to query.

        Returns
        -------
        list[PackageManifest]
            All manifests that satisfy this query, sorted by name.
        """
        return sorted(
            (m for m in registry.list_all() if self.matches(m)),
            key=lambda m: m.name,
        )

    def add_required(self, cap: PackageCapability) -> CapabilityQuery:
        """Return a *new* :class:`CapabilityQuery` with *cap* added to required.

        Parameters
        ----------
        cap:
            The :class:`PackageCapability` to add to the required set.

        Returns
        -------
        CapabilityQuery
            New query instance; ``self`` is not mutated.
        """
        new_required = self.required | {cap}
        return CapabilityQuery(required=new_required, excluded=self.excluded)

    def add_excluded(self, cap: PackageCapability) -> CapabilityQuery:
        """Return a *new* :class:`CapabilityQuery` with *cap* added to excluded.

        Parameters
        ----------
        cap:
            The :class:`PackageCapability` to add to the excluded set.

        Returns
        -------
        CapabilityQuery
            New query instance; ``self`` is not mutated.
        """
        new_excluded = self.excluded | {cap}
        return CapabilityQuery(required=self.required, excluded=new_excluded)

    def to_dict(self) -> dict[str, Any]:
        """Serialise this query to a plain dictionary.

        Returns
        -------
        dict[str, Any]
            JSON-compatible representation.
        """
        return {
            "required": [c.value for c in sorted(self.required, key=lambda c: c.value)],
            "excluded": [c.value for c in sorted(self.excluded, key=lambda c: c.value)],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CapabilityQuery:
        """Reconstruct a :class:`CapabilityQuery` from a plain dictionary.

        Parameters
        ----------
        data:
            Dictionary as produced by :meth:`to_dict`.

        Returns
        -------
        CapabilityQuery
            Reconstructed query instance.
        """
        return cls(
            required=[PackageCapability(c) for c in data.get("required", [])],
            excluded=[PackageCapability(c) for c in data.get("excluded", [])],
        )

    def __repr__(self) -> str:
        req = {c.value for c in self.required}
        exc = {c.value for c in self.excluded}
        return f"CapabilityQuery(required={req!r}, excluded={exc!r})"


@dataclass(slots=True)
class ManifestSerializer:
    """Serialises and deserialises :class:`PackageManifest` and :class:`PackageRegistry` objects.

    Uses the standard :mod:`json` module with indented formatting for
    human-readable output.  All serialisation is lossless: a round-trip
    through :meth:`serialize` / :meth:`deserialize` produces an equivalent
    object.

    Attributes
    ----------
    indent:
        JSON indentation level.  Defaults to 2.
    sort_keys:
        Whether to sort dictionary keys in JSON output.  Defaults to ``True``.
    """

    indent: int = 2
    sort_keys: bool = True

    # ------------------------------------------------------------------
    # Manifest round-trip
    # ------------------------------------------------------------------

    def serialize(self, manifest: PackageManifest) -> str:
        """Serialise *manifest* to a JSON string.

        Parameters
        ----------
        manifest:
            The :class:`PackageManifest` to serialise.

        Returns
        -------
        str
            Pretty-printed JSON string.
        """
        return json.dumps(
            manifest.to_dict(),
            indent=self.indent,
            sort_keys=self.sort_keys,
        )

    def deserialize(self, text: str) -> PackageManifest:
        """Deserialise a :class:`PackageManifest` from a JSON string.

        Parameters
        ----------
        text:
            JSON string as produced by :meth:`serialize`.

        Returns
        -------
        PackageManifest
            Reconstructed manifest instance.

        Raises
        ------
        json.JSONDecodeError
            When *text* is not valid JSON.
        KeyError
            When required fields are missing from the JSON data.
        """
        data = json.loads(text)
        return PackageManifest.from_dict(data)

    # ------------------------------------------------------------------
    # Registry round-trip
    # ------------------------------------------------------------------

    def serialize_registry(self, registry: PackageRegistry) -> str:
        """Serialise an entire :class:`PackageRegistry` to a JSON string.

        Parameters
        ----------
        registry:
            The :class:`PackageRegistry` to serialise.

        Returns
        -------
        str
            Pretty-printed JSON string representing the full registry.
        """
        return json.dumps(
            registry.to_dict(),
            indent=self.indent,
            sort_keys=self.sort_keys,
        )

    def deserialize_registry(self, text: str) -> PackageRegistry:
        """Deserialise a :class:`PackageRegistry` from a JSON string.

        Parameters
        ----------
        text:
            JSON string as produced by :meth:`serialize_registry`.

        Returns
        -------
        PackageRegistry
            Reconstructed registry with all manifests loaded.

        Raises
        ------
        json.JSONDecodeError
            When *text* is not valid JSON.
        """
        data = json.loads(text)
        return PackageRegistry.from_dict(data)

    def serialize_query(self, query: CapabilityQuery) -> str:
        """Serialise a :class:`CapabilityQuery` to a JSON string.

        Parameters
        ----------
        query:
            The query to serialise.

        Returns
        -------
        str
            JSON string representation.
        """
        return json.dumps(query.to_dict(), indent=self.indent, sort_keys=self.sort_keys)

    def deserialize_query(self, text: str) -> CapabilityQuery:
        """Deserialise a :class:`CapabilityQuery` from a JSON string.

        Parameters
        ----------
        text:
            JSON string as produced by :meth:`serialize_query`.

        Returns
        -------
        CapabilityQuery
            Reconstructed query.
        """
        data = json.loads(text)
        return CapabilityQuery.from_dict(data)


@dataclass(slots=True)
class ManifestDiagnostics:
    """Produces human-readable diagnostic reports for manifests and registries.

    :class:`ManifestDiagnostics` is the primary reporting surface for the
    manifest subsystem.  It produces multi-line textual reports suitable for
    display in a terminal, log file, or copilot summary.

    Attributes
    ----------
    validator:
        :class:`ManifestValidator` used for validity checks embedded in
        reports.  A default instance is created if not supplied.
    line_width:
        Target line width for wrapped descriptions.  Default 80.
    """

    validator: ManifestValidator = field(default_factory=ManifestValidator)
    line_width: int = 80

    # ------------------------------------------------------------------
    # Manifest report
    # ------------------------------------------------------------------

    def report(self, manifest: PackageManifest) -> str:
        """Produce a detailed multi-line report for *manifest*.

        The report includes all fields formatted with labels, a validity
        section, and a dependencies section.

        Parameters
        ----------
        manifest:
            The :class:`PackageManifest` to report on.

        Returns
        -------
        str
            Multi-line human-readable report string.
        """
        sep = "=" * self.line_width
        thin = "-" * self.line_width
        lines: list[str] = [
            sep,
            f"  PACKAGE MANIFEST REPORT",
            sep,
            f"  Name            : {manifest.name}",
            f"  Version         : {manifest.version}",
            f"  Manifest ID     : {manifest.manifest_id}",
            f"  Theory Chapter  : {manifest.theory_chapter}",
            f"  Created At      : {manifest.created_at}",
            thin,
            "  Description:",
        ]
        # Word-wrap description.
        desc_words = manifest.description.split()
        current_line = "    "
        for word in desc_words:
            if len(current_line) + len(word) + 1 > self.line_width - 2:
                lines.append(current_line.rstrip())
                current_line = "    " + word + " "
            else:
                current_line += word + " "
        if current_line.strip():
            lines.append(current_line.rstrip())

        lines.append(thin)
        lines.append(f"  Capabilities ({manifest.capability_count()}):")
        for cap in manifest.capabilities:
            lines.append(f"    [{cap.value:25s}]  {cap.description()}")

        lines.append(thin)
        lines.append(f"  Exported Classes ({len(manifest.exported_classes)}):")
        chunk_size = 4
        for i in range(0, len(manifest.exported_classes), chunk_size):
            chunk = manifest.exported_classes[i : i + chunk_size]
            lines.append("    " + ",  ".join(chunk))

        lines.append(thin)
        lines.append(f"  Dependencies ({len(manifest.dependencies)}):")
        dep_check = self.validator.check_dependencies(manifest)
        for dep in manifest.dependencies:
            ok = dep_check.get(dep, False)
            status = "✓" if ok else "✗"
            lines.append(f"    {status}  {dep}")

        lines.append(thin)
        errors = self.validator.validate(manifest)
        if not errors:
            lines.append("  Validation: PASSED (no errors)")
        else:
            lines.append(f"  Validation: FAILED ({len(errors)} error(s))")
            for err in errors:
                lines.append(f"    ✗ {err}")

        lines.append(sep)
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Registry report
    # ------------------------------------------------------------------

    def registry_report(self, registry: PackageRegistry) -> str:
        """Produce a summary report for an entire :class:`PackageRegistry`.

        Parameters
        ----------
        registry:
            The :class:`PackageRegistry` to report on.

        Returns
        -------
        str
            Multi-line report listing all registered packages with their
            capability counts and validation status.
        """
        sep = "=" * self.line_width
        thin = "-" * self.line_width
        lines: list[str] = [
            sep,
            "  PACKAGE REGISTRY REPORT",
            sep,
            f"  Registry ID : {registry._registry_id}",
            f"  Created At  : {registry._created_at}",
            f"  Total       : {registry.count()} package(s) registered",
            thin,
        ]

        if registry.count() == 0:
            lines.append("  (no packages registered)")
        else:
            # Capability summary.
            cap_counts: dict[PackageCapability, int] = {}
            for m in registry.list_all():
                for cap in m.capabilities:
                    cap_counts[cap] = cap_counts.get(cap, 0) + 1

            lines.append("  Capability Coverage:")
            for cap in PackageCapability:
                count = cap_counts.get(cap, 0)
                bar = "█" * min(count, 20)
                lines.append(f"    {cap.value:25s} {bar} ({count})")

            lines.append(thin)
            lines.append("  Registered Packages:")
            for m in registry.list_all():
                is_valid = self.validator.is_valid(m)
                status = "✓" if is_valid else "✗"
                cap_str = ", ".join(c.value for c in m.capabilities)
                lines.append(f"    {status} {m.name:45s} v{m.version}")
                lines.append(f"      Caps: [{cap_str}]")
                deps_str = ", ".join(m.dependencies) if m.dependencies else "none"
                lines.append(f"      Deps: {deps_str}")

        lines.append(sep)
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Diff
    # ------------------------------------------------------------------

    def diff(self, a: PackageManifest, b: PackageManifest) -> str:
        """Show differences between two :class:`PackageManifest` objects.

        Compares every field and produces a ``+``/``-``/``=`` diff-style
        report.

        Parameters
        ----------
        a:
            The "before" manifest (labelled ``A``).
        b:
            The "after" manifest (labelled ``B``).

        Returns
        -------
        str
            Multi-line diff report.  Lines starting with ``-`` indicate a
            value present in *a* but different in *b*; lines starting with
            ``+`` show the value in *b*; ``=`` means unchanged.
        """
        sep = "=" * self.line_width
        thin = "-" * self.line_width
        lines: list[str] = [
            sep,
            f"  MANIFEST DIFF  A={a.name}@{a.version}  vs  B={b.name}@{b.version}",
            sep,
        ]

        def _field_diff(label: str, va: Any, vb: Any) -> None:
            if va == vb:
                lines.append(f"  = {label:20s}: {va}")
            else:
                lines.append(f"  - {label:20s}: {va}")
                lines.append(f"  + {label:20s}: {vb}")

        _field_diff("name", a.name, b.name)
        _field_diff("version", a.version, b.version)
        _field_diff("theory_chapter", a.theory_chapter, b.theory_chapter)

        # Description diff (token-level).
        desc_a_tokens = _tokenize(a.description)
        desc_b_tokens = _tokenize(b.description)
        added_tokens = desc_b_tokens - desc_a_tokens
        removed_tokens = desc_a_tokens - desc_b_tokens
        if not added_tokens and not removed_tokens:
            lines.append(f"  = {'description':20s}: (unchanged)")
        else:
            lines.append(f"  ~ {'description':20s}: (changed)")
            if removed_tokens:
                lines.append(f"    - removed tokens: {sorted(removed_tokens)}")
            if added_tokens:
                lines.append(f"    + added tokens:   {sorted(added_tokens)}")

        # Capabilities diff.
        caps_a = set(a.capabilities)
        caps_b = set(b.capabilities)
        added_caps = caps_b - caps_a
        removed_caps = caps_a - caps_b
        if not added_caps and not removed_caps:
            lines.append(f"  = {'capabilities':20s}: (unchanged)")
        else:
            lines.append(f"  ~ {'capabilities':20s}: (changed)")
            for cap in sorted(removed_caps, key=lambda c: c.value):
                lines.append(f"    - {cap.value}")
            for cap in sorted(added_caps, key=lambda c: c.value):
                lines.append(f"    + {cap.value}")

        # Exported classes diff.
        ec_a = set(a.exported_classes)
        ec_b = set(b.exported_classes)
        added_ec = ec_b - ec_a
        removed_ec = ec_a - ec_b
        if not added_ec and not removed_ec:
            lines.append(
                f"  = {'exported_classes':20s}: (unchanged, {len(ec_a)} entries)"
            )
        else:
            lines.append(
                f"  ~ {'exported_classes':20s}: "
                f"{len(ec_a)} → {len(ec_b)} entries"
            )
            for sym in sorted(removed_ec):
                lines.append(f"    - {sym}")
            for sym in sorted(added_ec):
                lines.append(f"    + {sym}")

        # Dependencies diff.
        dep_a = set(a.dependencies)
        dep_b = set(b.dependencies)
        added_dep = dep_b - dep_a
        removed_dep = dep_a - dep_b
        if not added_dep and not removed_dep:
            lines.append(f"  = {'dependencies':20s}: (unchanged)")
        else:
            lines.append(f"  ~ {'dependencies':20s}: (changed)")
            for dep in sorted(removed_dep):
                lines.append(f"    - {dep}")
            for dep in sorted(added_dep):
                lines.append(f"    + {dep}")

        lines.append(thin)
        # Summary.
        any_diff = (
            a.name != b.name
            or a.version != b.version
            or a.theory_chapter != b.theory_chapter
            or a.description != b.description
            or caps_a != caps_b
            or ec_a != ec_b
            or dep_a != dep_b
        )
        if any_diff:
            lines.append("  Summary: manifests DIFFER")
        else:
            lines.append("  Summary: manifests are IDENTICAL (modulo id/timestamp)")
        lines.append(sep)
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Module-level constant: this package's own manifest
# ---------------------------------------------------------------------------

_DEFAULT_MANIFEST: PackageManifest = PackageManifest(
    name="jugeo.ideation.theory_navigation",
    version="0.1.0",
    description=(
        "Theory-space navigation: construction, purpose-conditioned search, "
        "path-finding, and formal theorems for navigating mathematical theory "
        "spaces in JuGeo."
    ),
    capabilities=(
        PackageCapability.THEORY_SEARCH,
        PackageCapability.PURPOSE_NAVIGATION,
        PackageCapability.MAP_CONSTRUCTION,
        PackageCapability.PATH_FINDING,
        PackageCapability.SPACE_INDEXING,
    ),
    theory_chapter="theory2",
    exported_classes=(
        "TheorySpace",
        "TheoryNode",
        "NavigationState",
        "NavigationPath",
        "PurposeCondition",
        "NodeMaturity",
        "NavigationStrategy",
        "SpaceConstructionConfig",
        "NodeExtractor",
        "EdgeBuilder",
        "SpaceIndexer",
        "SpaceConstructor",
        "IncrementalSpaceUpdater",
        "PurposeVector",
        "PurposeWeightMap",
        "PurposeConditioner",
        "HeuristicComputer",
        "PurposeAligner",
        "PurposeDriftDetector",
        "SearchNode",
        "PathFinder",
        "DiversePathFinder",
        "PurposeGuidedSearch",
        "PathEvaluator",
        "PathCache",
        "NavigationAlgorithm",
        "TheoryNavigator",
        "MapBuilder",
        "NavigationOptimizer",
        "NavigationBenchmark",
        "NavigationDiagnostics",
        "NavigationHistory",
        "IdeaNavigator",
        "FederationNavigator",
        "NoveltyNavigator",
        "NavigationFederator",
        "TrustAwareNavigator",
        "IntegratedNavigationPipeline",
        "NavigationTheorem",
        "TheoremRegistry",
        "TheoremVerifier",
        "TheoremApplications",
        "TheoremCatalog",
    ),
    dependencies=(
        "jugeo.ideation.ideas",
        "jugeo.ideation.novelty",
        "jugeo.ideation.federation",
        "jugeo.ideation.regimes",
        "jugeo.evidence.trust",
    ),
)

# ---------------------------------------------------------------------------
# __all__
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
]
