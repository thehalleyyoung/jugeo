"""
Semantic-Futures Manifest
=========================

Package manifest and descriptor layer for the *semantic_futures* sub-package
(JuGeo Theory Ch. 49 — Ideation as Search over Semantic Futures).

A :class:`SemanticFuturesManifest` records provenance metadata, the current
schema version, and the set of public symbols exported by each sub-module.
Together with :class:`ManifestValidator` and :class:`ManifestRegistry`, it
provides a light-weight catalogue that can be serialised to JSON, compared
across runs, and merged when several independently-evolved manifests need to
be unified.

Theory reference
----------------
Ch. 49 defines the ideation state

.. math::

   I = (S_{\\text{now}},\\, P,\\, F,\\, B,\\, A)

The manifest layer is *meta-theory*: it records which parts of the
formalisation are present in a given deployment.

Mathematical objects
~~~~~~~~~~~~~~~~~~~~
* :class:`FutureSpaceDescriptor` — encodes the geometry of the space
  :math:`\\mathcal{F}` in which semantic futures live (dimension,
  metric, topology).
* :func:`merge_manifests` — merges two manifests by taking the union
  of their export lists, resolving version conflicts by preferring the
  higher version.

Usage
-----
::

    from jugeo.ideation.semantic_futures.manifest import (
        SemanticFuturesManifest,
        ManifestRegistry,
        ManifestValidator,
        create_default_manifest,
    )

    manifest = create_default_manifest()
    validator = ManifestValidator()
    validator.assert_valid(manifest)

    registry = ManifestRegistry()
    registry.register(manifest)
    print(registry.list_all())

Module invariants
-----------------
* Every manifest carries a :attr:`SemanticFuturesManifest.package_id` that is
  a valid UUID4 string.
* Version strings follow ``MAJOR.MINOR.PATCH`` semver conventions.
* Export lists may be empty but never ``None``.
"""

from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

__all__ = [
    "SemanticFuturesManifest",
    "FutureSpaceDescriptor",
    "ManifestValidator",
    "ManifestRegistry",
    "create_default_manifest",
    "validate_manifest",
    "merge_manifests",
]

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Internal version helpers
# ---------------------------------------------------------------------------

_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")


def _parse_version(v: str) -> tuple[int, int, int]:
    """Parse a ``MAJOR.MINOR.PATCH`` version string into an integer triple.

    Parameters
    ----------
    v:
        A semantic-version string such as ``"1.2.3"``.

    Returns
    -------
    tuple[int, int, int]
        The ``(major, minor, patch)`` components.

    Raises
    ------
    ValueError
        If *v* does not match ``MAJOR.MINOR.PATCH``.

    Examples
    --------
    >>> _parse_version("2.0.1")
    (2, 0, 1)
    """
    if not _VERSION_RE.match(v):
        raise ValueError(
            f"Version string {v!r} does not match MAJOR.MINOR.PATCH format."
        )
    major, minor, patch = v.split(".")
    return int(major), int(minor), int(patch)


def _version_tuple(v: str) -> tuple[int, int, int]:
    """Return the integer triple for *v*, identical to :func:`_parse_version`.

    Provided as a convenience alias so callers that need purely the tuple form
    can use a distinct name.

    Parameters
    ----------
    v:
        Version string in ``MAJOR.MINOR.PATCH`` form.

    Returns
    -------
    tuple[int, int, int]
    """
    return _parse_version(v)


def _newer_version(a: str, b: str) -> str:
    """Return the lexicographically newer of two semver version strings.

    Comparison is performed component-wise (``major`` → ``minor`` → ``patch``)
    so ``"1.10.0"`` correctly beats ``"1.9.0"``.

    Parameters
    ----------
    a:
        First version string.
    b:
        Second version string.

    Returns
    -------
    str
        Whichever of *a* or *b* is the higher version.  If they are equal,
        *a* is returned.

    Examples
    --------
    >>> _newer_version("1.2.3", "1.2.4")
    '1.2.4'
    >>> _newer_version("2.0.0", "1.99.99")
    '2.0.0'
    """
    return a if _version_tuple(a) >= _version_tuple(b) else b


def _merge_export_lists(
    a: dict[str, list[str]] | tuple[str, ...] | list[str],
    b: dict[str, list[str]] | tuple[str, ...] | list[str],
) -> dict[str, list[str]] | list[str]:
    """Merge two submodule-export dictionaries.

    For each submodule key present in either *a* or *b*, the resulting list is
    the *union* of the two export lists, preserving first-seen order and
    eliminating duplicates.

    Parameters
    ----------
    a:
        Export map from the first manifest.
    b:
        Export map from the second manifest.

    Returns
    -------
    dict[str, list[str]]
        Merged export map.

    Examples
    --------
    >>> _merge_export_lists({"m": ["X", "Y"]}, {"m": ["Y", "Z"], "n": ["W"]})
    {'m': ['X', 'Y', 'Z'], 'n': ['W']}
    """
    if not isinstance(a, dict) and not isinstance(b, dict):
        return list(dict.fromkeys([*(str(x) for x in a), *(str(x) for x in b)]))

    a_map = a if isinstance(a, dict) else {"package": [str(x) for x in a]}
    b_map = b if isinstance(b, dict) else {"package": [str(x) for x in b]}
    merged: dict[str, list[str]] = {}
    all_keys = set(a_map) | set(b_map)
    for key in sorted(all_keys):
        seen: dict[str, None] = {}
        for sym in a_map.get(key, []) + b_map.get(key, []):
            seen[sym] = None
        merged[key] = list(seen)
    return merged


def _iso_now() -> str:
    """Return the current UTC time as an ISO-8601 string."""
    return datetime.now(tz=UTC).isoformat()


def _validate_uuid(value: str) -> bool:
    """Return ``True`` if *value* is a valid UUID string in any form."""
    try:
        uuid.UUID(value)
        return True
    except ValueError:
        return False


# ---------------------------------------------------------------------------
# FutureSpaceDescriptor
# ---------------------------------------------------------------------------


@dataclass(frozen=True, init=False)
class FutureSpaceDescriptor:
    """Geometric descriptor for the semantic-future space :math:`\\mathcal{F}`.

    Encodes the essential geometric and topological properties of the space in
    which semantic futures live, as introduced in Ch. 49.  The descriptor is
    intentionally abstract so that it can represent both finite combinatorial
    spaces and continuous metric spaces.

    Attributes
    ----------
    dimension_count:
        Number of dimensions in :math:`\\mathcal{F}`.  Use ``-1`` to indicate
        infinite or unknown dimensionality.
    metric_type:
        Human-readable name of the metric used (e.g. ``"cosine"``,
        ``"euclidean"``, ``"Hamming"``).
    topology_description:
        Free-text description of the topological properties (e.g.
        ``"connected compact Riemannian manifold"``).
    is_compact:
        Whether :math:`\\mathcal{F}` is topologically compact.  Compactness
        guarantees the existence of an optimal future when the value function is
        continuous (Theorem 49.1).
    is_connected:
        Whether :math:`\\mathcal{F}` is path-connected.
    notes:
        Additional notes or caveats about the geometric model.

    Examples
    --------
    ::

        desc = FutureSpaceDescriptor(
            dimension_count=512,
            metric_type="cosine",
            topology_description="high-dimensional unit hypersphere",
            is_compact=True,
            is_connected=True,
            notes="Embedding space from transformer encoder.",
        )
        assert desc.is_finite_dimensional()
    """

    dimension_count: int
    metric_type: str
    topology_description: str
    is_compact: bool
    is_connected: bool
    notes: str

    def __init__(
        self,
        dimension_count: int | None = None,
        metric_type: str = "euclidean",
        topology_description: str = "",
        is_compact: bool = False,
        is_connected: bool = True,
        notes: str = "",
        *,
        name: str | None = None,
        dimensions: int | None = None,
        coordinate_names: tuple[str, ...] | list[str] = (),
    ) -> None:
        resolved_dimensions = dimension_count if dimension_count is not None else (dimensions if dimensions is not None else len(tuple(coordinate_names)))
        resolved_name = name or "semantic-space"
        resolved_notes = notes or resolved_name
        resolved_topology = topology_description or (", ".join(coordinate_names) if coordinate_names else resolved_name)
        object.__setattr__(self, "dimension_count", int(resolved_dimensions))
        object.__setattr__(self, "metric_type", metric_type)
        object.__setattr__(self, "topology_description", resolved_topology)
        object.__setattr__(self, "is_compact", bool(is_compact))
        object.__setattr__(self, "is_connected", bool(is_connected))
        object.__setattr__(self, "notes", resolved_notes)
        if self.dimension_count < -1:
            raise ValueError(
                "dimension_count must be >= -1 (-1 means infinite/unknown)."
            )
        if not self.metric_type:
            raise ValueError("metric_type must be a non-empty string.")

    @property
    def name(self) -> str:
        return self.notes

    @property
    def dimensions(self) -> int:
        return self.dimension_count

    @property
    def coordinate_names(self) -> tuple[str, ...]:
        if self.topology_description and "," in self.topology_description:
            return tuple(part.strip() for part in self.topology_description.split(",") if part.strip())
        return tuple(f"dim_{i}" for i in range(max(self.dimension_count, 0)))

    @property
    def is_finite_dimensional(self) -> bool:
        """Return ``True`` if :attr:`dimension_count` is a finite positive integer.

        Returns
        -------
        bool
            ``True`` when ``dimension_count >= 1``.
        """
        return self.dimension_count >= 1

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dictionary suitable for JSON encoding.

        Returns
        -------
        dict[str, Any]
        """
        return {
            "name": self.name,
            "dimensions": self.dimensions,
            "coordinate_names": list(self.coordinate_names),
            "dimension_count": self.dimension_count,
            "metric_type": self.metric_type,
            "topology_description": self.topology_description,
            "is_compact": self.is_compact,
            "is_connected": self.is_connected,
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FutureSpaceDescriptor:
        """Deserialise from a plain dictionary.

        Parameters
        ----------
        data:
            Dictionary previously produced by :meth:`to_dict`.

        Returns
        -------
        FutureSpaceDescriptor
        """
        return cls(
            dimension_count=int(data.get("dimension_count", data.get("dimensions", 0))),
            metric_type=str(data.get("metric_type", "euclidean")),
            topology_description=str(data.get("topology_description", "")),
            is_compact=bool(data.get("is_compact", False)),
            is_connected=bool(data.get("is_connected", True)),
            notes=str(data.get("notes", "")),
            name=data.get("name"),
            coordinate_names=data.get("coordinate_names", ()),
        )


# ---------------------------------------------------------------------------
# SemanticFuturesManifest
# ---------------------------------------------------------------------------


@dataclass(frozen=True, init=False)
class SemanticFuturesManifest:
    """Package manifest for the *semantic_futures* sub-package.

    Records provenance metadata, schema version, and the public symbols
    exported by each sub-module.  Manifests are immutable value objects that
    can be serialised, validated, stored in a :class:`ManifestRegistry`, and
    merged with :func:`merge_manifests`.

    Attributes
    ----------
    version:
        Schema version in ``MAJOR.MINOR.PATCH`` form.
    package_id:
        UUID4 string that uniquely identifies this manifest instance.
    theory_chapter:
        Reference to the JuGeo theory chapter (e.g. ``"Ch. 49"``).
    submodule_exports:
        Mapping from sub-module name to the list of public symbols it exports.
    created_at:
        UTC timestamp of manifest creation.
    description:
        Free-text description of the manifest's purpose or provenance.
    tags:
        Arbitrary labels attached to the manifest for filtering.

    Examples
    --------
    ::

        m = SemanticFuturesManifest(
            version="1.0.0",
            package_id=str(uuid.uuid4()),
            theory_chapter="Ch. 49",
            submodule_exports={"models": ["SemanticFuture", "IdeationState"]},
            created_at=datetime.now(tz=UTC),
            description="Default manifest.",
            tags=["default"],
        )
        assert ManifestValidator().is_valid(m)
    """

    version: str
    package_id: str
    theory_chapter: str
    submodule_exports: dict[str, list[str]]
    created_at: datetime
    description: str
    tags: list[str]

    def __init__(
        self,
        version: str,
        package_id: str | None = None,
        theory_chapter: str = "49",
        submodule_exports: dict[str, list[str]] | None = None,
        created_at: datetime | None = None,
        description: str = "",
        tags: list[str] | tuple[str, ...] | None = None,
        *,
        name: str | None = None,
        exports: tuple[str, ...] | list[str] | None = None,
    ) -> None:
        export_list = [str(item) for item in (exports or ())]
        export_map = (
            {str(k): [str(item) for item in v] for k, v in submodule_exports.items()}
            if submodule_exports is not None
            else {"package": export_list}
        )
        object.__setattr__(self, "version", version)
        object.__setattr__(self, "package_id", package_id or str(uuid.uuid4()))
        object.__setattr__(self, "theory_chapter", theory_chapter or "49")
        object.__setattr__(self, "submodule_exports", export_map)
        object.__setattr__(self, "created_at", created_at or datetime.now(tz=UTC))
        object.__setattr__(self, "description", description)
        object.__setattr__(self, "tags", list(tags or []))
        object.__setattr__(self, "_legacy_name", "semantic-futures" if name is None else str(name))

    @property
    def name(self) -> str:
        return self._legacy_name

    @property
    def exports(self) -> tuple[str, ...]:
        flattened: list[str] = []
        for values in self.submodule_exports.values():
            for value in values:
                if value not in flattened:
                    flattened.append(value)
        return tuple(flattened)

    def validate(self) -> list[str]:
        """Run all validation checks and return a list of error messages.

        An empty list means the manifest is valid.

        Returns
        -------
        list[str]
            Human-readable error descriptions, one per violation.
        """
        return ManifestValidator().validate(self)

    def __str__(self) -> str:
        n_exports = sum(len(v) for v in self.submodule_exports.values())
        n_modules = len(self.submodule_exports)
        return (
            f"SemanticFuturesManifest("
            f"name={self.name!r}, "
            f"version={self.version}, "
            f"id={self.package_id[:8]}…, "
            f"chapter={self.theory_chapter!r}, "
            f"modules={n_modules}, "
            f"exports={n_exports})"
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dictionary.

        Returns
        -------
        dict[str, Any]
        """
        return {
            "name": self.name,
            "exports": list(self.exports),
            "version": self.version,
            "package_id": self.package_id,
            "theory_chapter": self.theory_chapter,
            "submodule_exports": {
                k: list(v) for k, v in self.submodule_exports.items()
            },
            "created_at": self.created_at.isoformat(),
            "description": self.description,
            "tags": list(self.tags),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SemanticFuturesManifest:
        """Deserialise from a plain dictionary.

        Parameters
        ----------
        data:
            Dictionary previously produced by :meth:`to_dict`.

        Returns
        -------
        SemanticFuturesManifest
        """
        raw_ts = data["created_at"]
        if isinstance(raw_ts, datetime):
            created_at = raw_ts
        else:
            created_at = datetime.fromisoformat(str(raw_ts))
        return cls(
            version=str(data["version"]),
            package_id=str(data.get("package_id", str(uuid.uuid4()))),
            theory_chapter=str(data.get("theory_chapter", "49")),
            submodule_exports={
                k: list(v)
                for k, v in data.get("submodule_exports", {"package": data["exports"]}).items()
            } if ("submodule_exports" in data or "exports" in data) else (_ for _ in ()).throw(KeyError("exports")),
            created_at=created_at if "created_at" in data else datetime.now(tz=UTC),
            description=str(data.get("description", "")),
            tags=list(data.get("tags", [])),
            name=str(data["name"]),
            exports=data.get("exports"),
        )


# ---------------------------------------------------------------------------
# ManifestValidator
# ---------------------------------------------------------------------------


class ManifestValidator:
    """Validates :class:`SemanticFuturesManifest` instances.

    Checks cover version format, UUID validity, and the structural
    requirements of the export map.  Each check produces a human-readable
    error string so that callers can surface actionable diagnostics.

    Examples
    --------
    ::

        validator = ManifestValidator()
        errors = validator.validate(manifest)
        if errors:
            for e in errors:
                print("ERROR:", e)
    """

    def validate(self, manifest: SemanticFuturesManifest) -> list[str]:
        """Validate *manifest* and return all discovered errors.

        Parameters
        ----------
        manifest:
            The manifest to check.

        Returns
        -------
        list[str]
            A list of human-readable error messages.  An empty list means the
            manifest passed all checks.
        """
        errors: list[str] = []

        if not _VERSION_RE.match(manifest.version):
            errors.append(
                f"version {manifest.version!r} does not match MAJOR.MINOR.PATCH."
            )

        if not manifest.name.strip():
            errors.append("name must not be blank.")

        if not isinstance(manifest.submodule_exports, dict):
            errors.append("submodule_exports must be a dict.")
        elif not manifest.exports:
            errors.append("exports must not be empty.")

        if manifest.description and not manifest.description.strip():
            errors.append("description must not be blank.")

        _log.debug(
            "Validated manifest %s: %d error(s).",
            manifest.package_id[:8],
            len(errors),
        )
        return errors

    def is_valid(self, manifest: SemanticFuturesManifest) -> bool:
        """Return ``True`` if *manifest* has no validation errors.

        Parameters
        ----------
        manifest:
            The manifest to check.

        Returns
        -------
        bool
        """
        return len(self.validate(manifest)) == 0

    def assert_valid(self, manifest: SemanticFuturesManifest) -> None:
        """Raise :class:`ValueError` if *manifest* has any validation errors.

        Parameters
        ----------
        manifest:
            The manifest to check.

        Raises
        ------
        ValueError
            If one or more validation checks fail; the message lists all
            errors separated by newlines.
        """
        errors = self.validate(manifest)
        if errors:
            joined = "\n  ".join(errors)
            raise ValueError(
                f"Manifest {manifest.package_id!r} is invalid:\n  {joined}"
            )


# ---------------------------------------------------------------------------
# ManifestRegistry
# ---------------------------------------------------------------------------


class ManifestRegistry:
    """In-process registry that stores manifests keyed by :attr:`package_id`.

    Supports registration, retrieval, removal, and merging of all stored
    manifests into a single consolidated manifest.

    Attributes
    ----------
    _store:
        Internal mapping from UUID string to :class:`SemanticFuturesManifest`.

    Examples
    --------
    ::

        registry = ManifestRegistry()
        registry.register(manifest_a)
        registry.register(manifest_b)
        merged = registry.merge_all()
    """

    def __init__(self) -> None:
        self._store: dict[str, SemanticFuturesManifest] = {}

    def register(self, manifest: SemanticFuturesManifest) -> None:
        """Register *manifest* in the registry.

        If a manifest with the same :attr:`package_id` already exists it is
        silently overwritten and a warning is emitted.

        Parameters
        ----------
        manifest:
            Manifest to register.
        """
        if manifest.name in self._store:
            raise ValueError(manifest.name)
        self._store[manifest.name] = manifest
        _log.debug("Registered manifest %s.", manifest.name)

    def get(self, package_id: str) -> SemanticFuturesManifest | None:
        """Retrieve a manifest by its UUID string.

        Parameters
        ----------
        package_id:
            The UUID string of the desired manifest.

        Returns
        -------
        SemanticFuturesManifest | None
            The stored manifest, or ``None`` if not found.
        """
        return self._store.get(package_id)

    def list_all(self) -> list[SemanticFuturesManifest]:
        """Return all registered manifests in insertion order.

        Returns
        -------
        list[SemanticFuturesManifest]
        """
        return list(self._store.values())

    def remove(self, package_id: str) -> bool:
        """Remove the manifest identified by *package_id*.

        Parameters
        ----------
        package_id:
            UUID string of the manifest to remove.

        Returns
        -------
        bool
            ``True`` if the manifest was found and removed; ``False`` otherwise.
        """
        if package_id not in self._store:
            raise KeyError(package_id)
        del self._store[package_id]
        _log.debug("Removed manifest %s from registry.", package_id)
        return True

    def merge_all(self) -> SemanticFuturesManifest:
        """Merge all registered manifests into a single consolidated manifest.

        Uses :func:`merge_manifests` pairwise, reducing the collection to one
        manifest that contains the union of all exports and the highest version
        among all participants.

        Returns
        -------
        SemanticFuturesManifest
            A fresh manifest whose :attr:`package_id` is a newly generated
            UUID, whose version is the maximum of all registered versions, and
            whose :attr:`submodule_exports` is the union of all export maps.

        Raises
        ------
        ValueError
            If the registry is empty.
        """
        manifests = self.list_all()
        if not manifests:
            return None
        result = manifests[0]
        for other in manifests[1:]:
            result = merge_manifests(result, other)
        _log.info(
            "merge_all() produced manifest %s from %d source(s).",
            result.name,
            len(manifests),
        )
        return result

    def __len__(self) -> int:
        return len(self._store)

    def __repr__(self) -> str:
        return f"ManifestRegistry(size={len(self._store)})"


# ---------------------------------------------------------------------------
# Module-level factory and utility functions
# ---------------------------------------------------------------------------


def create_default_manifest() -> SemanticFuturesManifest:
    """Create and return the canonical default manifest for *semantic_futures*.

    The default manifest declares the three primary sub-modules—
    ``manifest``, ``models``, and ``theorems``—with their standard public
    symbol lists.  The version is ``"1.0.0"`` and a fresh UUID is assigned
    on each call.

    Returns
    -------
    SemanticFuturesManifest
        A freshly constructed default manifest.

    Examples
    --------
    ::

        manifest = create_default_manifest()
        print(manifest)
    """
    manifest = SemanticFuturesManifest(
        version="1.0.0",
        package_id=str(uuid.uuid4()),
        theory_chapter="Ch. 49",
        submodule_exports={
            "manifest": [
                "SemanticFuturesManifest",
                "FutureSpaceDescriptor",
                "ManifestValidator",
                "ManifestRegistry",
                "create_default_manifest",
                "validate_manifest",
                "merge_manifests",
            ],
            "models": [
                "SemanticFuture",
                "FutureState",
                "PurposeFunction",
                "FutureValuation",
                "IdeationState",
                "FutureFilter",
                "FutureRanker",
                "FutureComparator",
                "FutureTag",
            ],
            "theorems": [
                "TheoremStatement",
                "TheoremHypothesis",
                "TheoremCatalog",
                "TheoremVerifier",
                "TheoremDifficulty",
                "THEOREM_CATALOG",
            ],
        },
        created_at=datetime.now(tz=UTC),
        description=(
            "Default manifest for the jugeo.ideation.semantic_futures "
            "sub-package (JuGeo Theory Ch. 49)."
        ),
        tags=["default", "ch49", "semantic-futures"],
    )
    _log.debug("Created default manifest %s.", manifest.package_id[:8])
    return manifest


def validate_manifest(manifest: SemanticFuturesManifest) -> bool:
    """Return ``True`` if *manifest* passes all validation checks.

    A convenience wrapper around :class:`ManifestValidator`.

    Parameters
    ----------
    manifest:
        Manifest to validate.

    Returns
    -------
    bool
        ``True`` if and only if no validation errors were found.
    """
    return ManifestValidator().validate(manifest)


def merge_manifests(
    a: SemanticFuturesManifest, b: SemanticFuturesManifest
) -> SemanticFuturesManifest:
    """Merge two manifests into a single unified manifest.

    The merge policy is:

    1. **version** — the higher of the two versions is adopted.
    2. **submodule_exports** — the union of both export maps (see
       :func:`_merge_export_lists`).
    3. **theory_chapter** — taken from *a* (assumed authoritative).
    4. **description** — a generated string noting the merge provenance.
    5. **tags** — the union of both tag lists.
    6. **package_id** — a newly generated UUID4.
    7. **created_at** — current UTC time.

    Parameters
    ----------
    a:
        First manifest.
    b:
        Second manifest.

    Returns
    -------
    SemanticFuturesManifest
        A new manifest that reconciles *a* and *b*.

    Examples
    --------
    ::

        merged = merge_manifests(manifest_a, manifest_b)
        assert merged.version >= manifest_a.version
    """
    merged_version = _newer_version(a.version, b.version)
    merged_exports = _merge_export_lists(
        a.submodule_exports, b.submodule_exports
    )
    merged_tags = list(dict.fromkeys(list(a.tags) + list(b.tags)))
    merged_description = a.description or b.description
    result = SemanticFuturesManifest(
        name=a.name,
        version=merged_version,
        package_id=str(uuid.uuid4()),
        theory_chapter=a.theory_chapter,
        submodule_exports=merged_exports,
        created_at=datetime.now(tz=UTC),
        description=merged_description,
        tags=merged_tags,
    )
    _log.info(
        "merge_manifests: produced %s (v%s) from %s and %s.",
        result.package_id[:8],
        result.version,
        a.package_id[:8],
        b.package_id[:8],
    )
    return result
