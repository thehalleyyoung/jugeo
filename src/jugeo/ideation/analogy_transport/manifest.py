"""Package manifest for jugeo.ideation.analogy_transport (theory2.tex Ch60).

This module declares the capabilities, dependencies, and metadata for the
analogy-transport package, which implements cross-regime idea transport via
structure-preserving analogies as described in Chapter 60 of theory2.tex.

Module layout::

    PackageCapability    – enumeration of package capabilities
    PackageManifest      – frozen dataclass describing the package
    ManifestValidator    – validates PackageManifest instances
    PackageRegistry      – registry of multiple package manifests
    CapabilityQuery      – query object for capability matching
    ManifestSerializer   – JSON/dict serialization for manifests
    ManifestDiagnostics  – diagnostic reporting for manifests
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
# Constants
# ---------------------------------------------------------------------------

PACKAGE_NAME = "jugeo.ideation.analogy_transport"
PACKAGE_VERSION = "0.1.0"
THEORY_CHAPTER = "Ch60"
MIN_FAITHFULNESS = 0.1
DEFAULT_FAITHFULNESS_THRESHOLD = 0.7
MAX_CORRESPONDENCES = 512

# Semver pattern used by ManifestValidator
_SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+")

# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    """Return *value* clamped to the closed interval [*lo*, *hi*]."""
    return max(lo, min(hi, float(value)))


def _now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string."""
    return datetime.now(tz=timezone.utc).isoformat()


def _tokenize(text: str) -> frozenset[str]:
    """Tokenize *text* into a frozenset of lowercase alphanumeric tokens.

    Tokens shorter than 2 characters are discarded to avoid noise from
    single-character words that carry little semantic weight.
    """
    return frozenset(t for t in re.findall(r"[a-z0-9]+", text.lower()) if len(t) > 1)


def _jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    """Compute the Jaccard similarity between two token sets.

    Returns 1.0 when both sets are empty (vacuously identical), and 0.0
    when exactly one of them is empty.  Otherwise returns
    ``|a ∩ b| / |a ∪ b|``.
    """
    if not a and not b:
        return 1.0
    union = a | b
    return len(a & b) / len(union) if union else 0.0


def _safe_name(name: str) -> str:
    """Return a filesystem-safe version of *name* by replacing non-alphanumeric
    characters (other than dots, hyphens, and underscores) with underscores."""
    return re.sub(r"[^a-zA-Z0-9._-]", "_", name)


def _indent(text: str, spaces: int = 4) -> str:
    """Return *text* with each line indented by *spaces* spaces."""
    pad = " " * spaces
    return "\n".join(pad + line for line in text.splitlines())


# ---------------------------------------------------------------------------
# PackageCapability
# ---------------------------------------------------------------------------


class PackageCapability(str, Enum):
    """Enumeration of capabilities that the analogy-transport package exposes.

    Each capability corresponds to a major algorithmic concern described in
    Chapter 60 of theory2.tex.  Capabilities are used by :class:`CapabilityQuery`
    to filter :class:`PackageManifest` instances from a :class:`PackageRegistry`.

    Members
    -------
    ANALOGY_CONSTRUCTION
        Build structure-preserving analogies between idea-spaces.
    STRUCTURE_PRESERVATION
        Verify that structural relations are faithfully preserved under
        analogy transport.
    PURPOSE_PRESERVATION
        Verify that telic/purpose attributes survive cross-regime transport.
    TRANSPORT_VERIFICATION
        End-to-end verification of a transported idea against trust rules.
    BRIDGE_FINDING
        Discover cross-regime bridges (:class:`~jugeo.ideation.federation.CrossRegimeBridge`)
        that satisfy given purpose and faithfulness constraints.
    """

    ANALOGY_CONSTRUCTION = "analogy_construction"
    STRUCTURE_PRESERVATION = "structure_preservation"
    PURPOSE_PRESERVATION = "purpose_preservation"
    TRANSPORT_VERIFICATION = "transport_verification"
    BRIDGE_FINDING = "bridge_finding"

    # ------------------------------------------------------------------
    # Convenience helpers
    # ------------------------------------------------------------------

    def label(self) -> str:
        """Return a human-friendly label for this capability.

        The label is derived from the enum member's name by replacing
        underscores with spaces and title-casing each word.

        Examples
        --------
        >>> PackageCapability.ANALOGY_CONSTRUCTION.label()
        'Analogy Construction'
        """
        return self.name.replace("_", " ").title()

    def short_code(self) -> str:
        """Return a short uppercase code built from the initials of the label.

        Examples
        --------
        >>> PackageCapability.STRUCTURE_PRESERVATION.short_code()
        'SP'
        """
        return "".join(word[0] for word in self.name.split("_"))

    def description(self) -> str:
        """Return a short prose description of what this capability provides."""
        _descriptions: dict[str, str] = {
            "analogy_construction": (
                "Constructs structure-preserving maps between source and target idea-regimes."
            ),
            "structure_preservation": (
                "Checks that structural relations (precedence, dependency, containment) "
                "are faithfully preserved when an analogy map is applied."
            ),
            "purpose_preservation": (
                "Verifies that telic / purpose attributes of an idea survive transport "
                "through a cross-regime bridge."
            ),
            "transport_verification": (
                "End-to-end verification pipeline that checks faithfulness, trust "
                "attenuation, and analogy evidence before accepting a transported idea."
            ),
            "bridge_finding": (
                "Searches the bridge registry for CrossRegimeBridge instances that "
                "satisfy requested purpose tags and minimum faithfulness constraints."
            ),
        }
        return _descriptions.get(self.value, f"Capability: {self.label()}")


# ---------------------------------------------------------------------------
# PackageManifest
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PackageManifest:
    """Frozen description of a jugeo package and its declared capabilities.

    Parameters
    ----------
    name:
        Fully-qualified package name, e.g. ``jugeo.ideation.analogy_transport``.
        Must start with ``"jugeo."``.
    version:
        PEP-440 / semver version string, e.g. ``"0.1.0"``.
    capabilities:
        Frozenset of :class:`PackageCapability` members this package exports.
    theory_chapter:
        The chapter of *theory2.tex* that this package implements, e.g. ``"Ch60"``.
    description:
        Human-readable description of the package's purpose.
    author:
        Author or team name.
    dependencies:
        Tuple of fully-qualified names of packages this manifest depends on.
    created_at:
        ISO-8601 creation timestamp (UTC).
    """

    name: str
    version: str
    capabilities: frozenset[PackageCapability]
    theory_chapter: str
    description: str
    author: str
    dependencies: tuple[str, ...]
    created_at: str

    # ------------------------------------------------------------------
    # Capability helpers
    # ------------------------------------------------------------------

    def has_capability(self, cap: PackageCapability) -> bool:
        """Return ``True`` iff *cap* is in this manifest's capability set.

        Parameters
        ----------
        cap:
            The :class:`PackageCapability` to test.

        Returns
        -------
        bool
            ``True`` when *cap* ∈ ``self.capabilities``, ``False`` otherwise.

        Examples
        --------
        >>> m = _make_minimal_manifest()
        >>> m.has_capability(PackageCapability.ANALOGY_CONSTRUCTION)
        True
        """
        if not isinstance(cap, PackageCapability):
            raise TypeError(
                f"has_capability expects a PackageCapability, got {type(cap).__name__!r}"
            )
        return cap in self.capabilities

    def capability_names(self) -> tuple[str, ...]:
        """Return a sorted tuple of capability value strings.

        The tuple is sorted lexicographically by the string *value* of each
        :class:`PackageCapability` member (not the enum name).  This provides
        a stable, deterministic ordering suitable for hashing and display.

        Returns
        -------
        tuple[str, ...]
            Sorted capability values, e.g.
            ``('analogy_construction', 'bridge_finding', ...)``.
        """
        return tuple(sorted(cap.value for cap in self.capabilities))

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Serialize this manifest to a plain Python dictionary.

        The result contains only JSON-compatible primitive types so that it
        can be passed directly to :func:`json.dumps`.  The ``capabilities``
        field is serialized as a sorted list of value strings; the
        ``dependencies`` field as a list.

        Returns
        -------
        dict[str, Any]
            A dictionary with keys matching each field name of this dataclass.

        Examples
        --------
        >>> d = _DEFAULT_MANIFEST.to_dict()
        >>> isinstance(d["capabilities"], list)
        True
        """
        return {
            "name": self.name,
            "version": self.version,
            "capabilities": list(self.capability_names()),
            "theory_chapter": self.theory_chapter,
            "description": self.description,
            "author": self.author,
            "dependencies": list(self.dependencies),
            "created_at": self.created_at,
        }

    # ------------------------------------------------------------------
    # Human-readable output
    # ------------------------------------------------------------------

    def summary(self) -> str:
        """Return a compact one-line human-readable summary of this manifest.

        The summary follows the pattern::

            <name> v<version> [<cap1>, <cap2>, ...] (<theory_chapter>)

        Where capability names are abbreviated short codes (see
        :meth:`PackageCapability.short_code`).

        Returns
        -------
        str
            A non-empty summary string.

        Examples
        --------
        >>> s = _DEFAULT_MANIFEST.summary()
        >>> "jugeo.ideation.analogy_transport" in s
        True
        """
        cap_codes = sorted(cap.short_code() for cap in self.capabilities)
        caps_str = ", ".join(cap_codes) if cap_codes else "(none)"
        dep_count = len(self.dependencies)
        return (
            f"{self.name} v{self.version} [{caps_str}] "
            f"({self.theory_chapter}) deps={dep_count} by {self.author!r}"
        )

    # ------------------------------------------------------------------
    # Dunder helpers
    # ------------------------------------------------------------------

    def __repr__(self) -> str:
        cap_names = self.capability_names()
        return (
            f"PackageManifest(name={self.name!r}, version={self.version!r}, "
            f"capabilities={cap_names!r}, theory_chapter={self.theory_chapter!r})"
        )

    def __str__(self) -> str:
        return self.summary()


# ---------------------------------------------------------------------------
# ManifestValidator
# ---------------------------------------------------------------------------


class ManifestValidator:
    """Validates :class:`PackageManifest` instances against a set of rules.

    Validation rules
    ----------------
    1. ``name`` must be a non-empty string that starts with ``"jugeo."``.
    2. ``version`` must match the semver-like pattern ``^\\d+\\.\\d+\\.\\d+``.
    3. ``capabilities`` must contain at least one member.
    4. ``dependencies`` elements must all be strings.
    5. ``theory_chapter`` must be a non-empty string.
    6. ``description`` must be a non-empty string.
    7. ``author`` must be a non-empty string.
    8. ``created_at`` must be parseable as an ISO-8601 datetime.

    All checks are non-raising; errors are accumulated and returned as a list
    of human-readable strings.  Use :meth:`assert_valid` if you want an
    exception raised immediately on the first batch of failures.
    """

    # ------------------------------------------------------------------
    # Core validation
    # ------------------------------------------------------------------

    def validate(self, manifest: PackageManifest) -> list[str]:
        """Run all validation rules against *manifest*.

        Parameters
        ----------
        manifest:
            The :class:`PackageManifest` to validate.

        Returns
        -------
        list[str]
            A (possibly empty) list of error messages.  An empty list means
            the manifest is valid.
        """
        errors: list[str] = []

        # Rule 1 – name
        if not manifest.name:
            errors.append("'name' must be a non-empty string.")
        elif not manifest.name.startswith("jugeo."):
            errors.append(
                f"'name' must start with 'jugeo.'; got {manifest.name!r}."
            )

        # Rule 2 – version
        if not manifest.version:
            errors.append("'version' must be a non-empty string.")
        elif not _SEMVER_RE.match(manifest.version):
            errors.append(
                f"'version' must match '^\\d+\\.\\d+\\.\\d+'; got {manifest.version!r}."
            )

        # Rule 3 – capabilities
        if not manifest.capabilities:
            errors.append(
                "'capabilities' must contain at least one PackageCapability member."
            )
        else:
            # Verify each member is actually a PackageCapability instance
            for cap in manifest.capabilities:
                if not isinstance(cap, PackageCapability):
                    errors.append(
                        f"Unexpected capability type {type(cap).__name__!r}; "
                        f"expected PackageCapability."
                    )

        # Rule 4 – dependencies
        for idx, dep in enumerate(manifest.dependencies):
            if not isinstance(dep, str):
                errors.append(
                    f"'dependencies[{idx}]' must be a str; got {type(dep).__name__!r}."
                )
            elif not dep.strip():
                errors.append(
                    f"'dependencies[{idx}]' must not be blank or whitespace-only."
                )

        # Rule 5 – theory_chapter
        if not manifest.theory_chapter or not manifest.theory_chapter.strip():
            errors.append("'theory_chapter' must be a non-empty string.")

        # Rule 6 – description
        if not manifest.description or not manifest.description.strip():
            errors.append("'description' must be a non-empty string.")

        # Rule 7 – author
        if not manifest.author or not manifest.author.strip():
            errors.append("'author' must be a non-empty string.")

        # Rule 8 – created_at parseable
        try:
            datetime.fromisoformat(manifest.created_at)
        except (ValueError, TypeError):
            errors.append(
                f"'created_at' must be a valid ISO-8601 string; "
                f"got {manifest.created_at!r}."
            )

        # Faithfulness invariant (package-level constant cross-check)
        if not (0.0 <= MIN_FAITHFULNESS <= 1.0):
            errors.append(
                f"Global MIN_FAITHFULNESS={MIN_FAITHFULNESS!r} is outside [0, 1]; "
                "this indicates a configuration error."
            )

        return errors

    def is_valid(self, manifest: PackageManifest) -> bool:
        """Return ``True`` iff *manifest* passes all validation rules.

        This is a convenience wrapper around :meth:`validate` that discards
        the error list and returns a plain boolean.

        Parameters
        ----------
        manifest:
            The manifest to check.

        Returns
        -------
        bool
        """
        return len(self.validate(manifest)) == 0

    def assert_valid(self, manifest: PackageManifest) -> None:
        """Raise :exc:`ValueError` if *manifest* fails any validation rule.

        The exception message is a newline-joined concatenation of all error
        strings returned by :meth:`validate`.

        Parameters
        ----------
        manifest:
            The manifest to validate.

        Raises
        ------
        ValueError
            When one or more validation rules are violated.
        """
        errors = self.validate(manifest)
        if errors:
            bullet_list = "\n  • ".join(errors)
            raise ValueError(
                f"PackageManifest validation failed for {manifest.name!r}:\n"
                f"  • {bullet_list}"
            )


# ---------------------------------------------------------------------------
# PackageRegistry
# ---------------------------------------------------------------------------


class PackageRegistry:
    """A mutable registry that stores and indexes :class:`PackageManifest` objects.

    Manifests are keyed by their ``name`` field.  Re-registering a manifest
    with the same name silently replaces the previous entry; use
    :meth:`contains` to check before registering if uniqueness matters.

    Typical usage::

        registry = PackageRegistry()
        registry.register(_DEFAULT_MANIFEST)
        m = registry.get(PACKAGE_NAME)
        order = registry.resolve_load_order(PACKAGE_NAME)
    """

    def __init__(self) -> None:
        self._registry: dict[str, PackageManifest] = {}

    # ------------------------------------------------------------------
    # Mutation
    # ------------------------------------------------------------------

    def register(self, manifest: PackageManifest) -> None:
        """Add *manifest* to the registry, keyed by ``manifest.name``.

        If a manifest with the same name already exists it is replaced.

        Parameters
        ----------
        manifest:
            A :class:`PackageManifest` instance to register.

        Raises
        ------
        TypeError
            If *manifest* is not a :class:`PackageManifest`.
        """
        if not isinstance(manifest, PackageManifest):
            raise TypeError(
                f"Expected PackageManifest, got {type(manifest).__name__!r}."
            )
        self._registry[manifest.name] = manifest

    def unregister(self, name: str) -> bool:
        """Remove the manifest with the given *name*.

        Parameters
        ----------
        name:
            The package name to remove.

        Returns
        -------
        bool
            ``True`` if an entry was removed, ``False`` if not found.
        """
        if name in self._registry:
            del self._registry[name]
            return True
        return False

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def contains(self, name: str) -> bool:
        """Return ``True`` iff a manifest named *name* is registered."""
        return name in self._registry

    def get(self, name: str) -> PackageManifest | None:
        """Return the manifest for *name*, or ``None`` if not registered.

        Parameters
        ----------
        name:
            The fully-qualified package name.

        Returns
        -------
        PackageManifest | None
        """
        return self._registry.get(name)

    def all_manifests(self) -> list[PackageManifest]:
        """Return all registered manifests as a list.

        The list is ordered by insertion order (Python dict guarantee).

        Returns
        -------
        list[PackageManifest]
        """
        return list(self._registry.values())

    def find_by_capability(self, cap: PackageCapability) -> list[PackageManifest]:
        """Return all manifests that have *cap* in their capability set.

        Parameters
        ----------
        cap:
            The :class:`PackageCapability` to filter by.

        Returns
        -------
        list[PackageManifest]
            Possibly empty list of matching manifests.
        """
        if not isinstance(cap, PackageCapability):
            raise TypeError(
                f"find_by_capability expects PackageCapability, "
                f"got {type(cap).__name__!r}."
            )
        return [m for m in self._registry.values() if cap in m.capabilities]

    # ------------------------------------------------------------------
    # Dependency helpers
    # ------------------------------------------------------------------

    def dependency_graph(self) -> dict[str, list[str]]:
        """Build a dependency graph for all registered manifests.

        Returns a dict mapping each manifest's name to the list of its direct
        dependencies that are *also* registered in this registry.  Dependencies
        that are not registered are included in the list but flagged with a
        ``"[missing]"`` prefix so callers can detect incomplete graphs.

        Returns
        -------
        dict[str, list[str]]
            e.g. ``{"a": ["b", "c"], "b": [], "c": ["b"]}``
        """
        graph: dict[str, list[str]] = {}
        for name, manifest in self._registry.items():
            deps: list[str] = []
            for dep in manifest.dependencies:
                if dep in self._registry:
                    deps.append(dep)
                else:
                    deps.append(f"[missing]{dep}")
            graph[name] = deps
        return graph

    def resolve_load_order(self, name: str) -> list[str]:
        """Return a topologically-sorted load order for *name* and its deps.

        Uses an iterative DFS with a visited / on-stack set to detect cycles.
        When a cycle is detected the cyclic node is appended with a
        ``"[cycle]"`` annotation and the traversal continues so that callers
        receive a best-effort ordering rather than an exception.

        Parameters
        ----------
        name:
            The root package whose transitive dependencies should be resolved.

        Returns
        -------
        list[str]
            A list where dependencies appear *before* the packages that need
            them.  The *name* itself appears last.
        """
        if name not in self._registry:
            return [name]

        result: list[str] = []
        visited: set[str] = set()
        on_stack: set[str] = set()

        def _dfs(node: str) -> None:
            if node in on_stack:
                # Cycle detected; append annotated node and return
                result.append(f"[cycle]{node}")
                return
            if node in visited:
                return
            on_stack.add(node)
            manifest = self._registry.get(node)
            if manifest is not None:
                for dep in manifest.dependencies:
                    # Only recurse into deps known to the registry
                    if dep in self._registry:
                        _dfs(dep)
            on_stack.discard(node)
            visited.add(node)
            result.append(node)

        _dfs(name)
        return result

    # ------------------------------------------------------------------
    # Dunder
    # ------------------------------------------------------------------

    def __len__(self) -> int:
        return len(self._registry)

    def __contains__(self, name: object) -> bool:
        return name in self._registry

    def __repr__(self) -> str:
        names = list(self._registry.keys())
        return f"PackageRegistry(manifests={names!r})"


# ---------------------------------------------------------------------------
# CapabilityQuery
# ---------------------------------------------------------------------------


@dataclass
class CapabilityQuery:
    """A query object for filtering and scoring :class:`PackageManifest` objects.

    Parameters
    ----------
    required_capabilities:
        All of these must be present in a manifest for :meth:`matches` to
        return ``True``.
    optional_capabilities:
        These contribute to the :meth:`score` but are not required.
    min_faithfulness:
        A threshold in [0, 1] that can be used by callers to impose a
        minimum score.  Stored but not enforced internally by this class.

    Examples
    --------
    >>> q = CapabilityQuery(
    ...     required_capabilities=frozenset({PackageCapability.BRIDGE_FINDING}),
    ...     optional_capabilities=frozenset({PackageCapability.TRANSPORT_VERIFICATION}),
    ... )
    >>> q.matches(_DEFAULT_MANIFEST)
    True
    """

    required_capabilities: frozenset[PackageCapability]
    optional_capabilities: frozenset[PackageCapability]
    min_faithfulness: float = 0.0

    # ------------------------------------------------------------------
    # Post-init validation
    # ------------------------------------------------------------------

    def __post_init__(self) -> None:
        self.min_faithfulness = _clamp(self.min_faithfulness)
        if not isinstance(self.required_capabilities, frozenset):
            object.__setattr__(
                self,
                "required_capabilities",
                frozenset(self.required_capabilities),
            )
        if not isinstance(self.optional_capabilities, frozenset):
            object.__setattr__(
                self,
                "optional_capabilities",
                frozenset(self.optional_capabilities),
            )

    # ------------------------------------------------------------------
    # Matching & scoring
    # ------------------------------------------------------------------

    def matches(self, manifest: PackageManifest) -> bool:
        """Return ``True`` iff *manifest* satisfies all required capabilities.

        A manifest matches this query when every :class:`PackageCapability` in
        ``required_capabilities`` is present in ``manifest.capabilities``.
        Optional capabilities have no effect on this predicate.

        Parameters
        ----------
        manifest:
            The manifest to test.

        Returns
        -------
        bool
        """
        if not self.required_capabilities:
            # Vacuously true – every manifest satisfies an empty requirement set
            return True
        missing = self.required_capabilities - manifest.capabilities
        return len(missing) == 0

    def score(self, manifest: PackageManifest) -> float:
        """Compute a [0, 1] relevance score for *manifest*.

        The score is computed as::

            required_bonus  = 1.0  if all required caps present, else 0.0
            optional_ratio  = |optional ∩ manifest.caps| / |optional|
                              (0.0 when optional set is empty)
            score = 0.6 * required_bonus + 0.4 * optional_ratio

        This gives manifests that satisfy requirements a strong advantage,
        while optional capabilities provide a secondary ranking signal.

        Parameters
        ----------
        manifest:
            The manifest to score.

        Returns
        -------
        float
            A value in [0.0, 1.0].
        """
        required_bonus = 1.0 if self.matches(manifest) else 0.0

        if self.optional_capabilities:
            matched_optional = self.optional_capabilities & manifest.capabilities
            optional_ratio = len(matched_optional) / len(self.optional_capabilities)
        else:
            optional_ratio = 0.0

        raw = 0.6 * required_bonus + 0.4 * optional_ratio
        return _clamp(raw)

    def filter_registry(self, registry: PackageRegistry) -> list[PackageManifest]:
        """Return all manifests in *registry* that :meth:`matches` returns True for.

        Results are sorted by :meth:`score` in descending order.

        Parameters
        ----------
        registry:
            The :class:`PackageRegistry` to search.

        Returns
        -------
        list[PackageManifest]
            Manifests passing the required-capability filter, sorted by score.
        """
        candidates = [m for m in registry.all_manifests() if self.matches(m)]
        return sorted(candidates, key=self.score, reverse=True)

    def __repr__(self) -> str:
        req_names = sorted(c.value for c in self.required_capabilities)
        opt_names = sorted(c.value for c in self.optional_capabilities)
        return (
            f"CapabilityQuery(required={req_names!r}, "
            f"optional={opt_names!r}, "
            f"min_faithfulness={self.min_faithfulness!r})"
        )


# ---------------------------------------------------------------------------
# ManifestSerializer
# ---------------------------------------------------------------------------


class ManifestSerializer:
    """Serializes and deserializes :class:`PackageManifest` objects.

    Supported formats
    -----------------
    * JSON  – via :meth:`to_json` / :meth:`from_json`
    * dict  – via :meth:`to_dict` / :meth:`from_dict`

    All serialization is lossless: a round-trip through JSON or dict
    produces an object that compares equal to the original.

    Notes
    -----
    The ``capabilities`` field is serialized as a sorted list of value
    strings and deserialized back to a :class:`frozenset` of
    :class:`PackageCapability` members.  Unknown capability values encountered
    during deserialization are silently skipped with a warning logged to
    ``stderr``.
    """

    # ------------------------------------------------------------------
    # JSON round-trip
    # ------------------------------------------------------------------

    def to_json(self, manifest: PackageManifest) -> str:
        """Serialize *manifest* to a JSON string.

        The output is a compact (no unnecessary whitespace) yet human-
        readable JSON string with 2-space indentation.

        Parameters
        ----------
        manifest:
            The manifest to serialize.

        Returns
        -------
        str
            A valid JSON string.
        """
        d = self.to_dict(manifest)
        # Ensure deterministic output by sorting dict keys
        return json.dumps(d, indent=2, sort_keys=True, ensure_ascii=False)

    def from_json(self, payload: str) -> PackageManifest:
        """Deserialize *payload* (a JSON string) to a :class:`PackageManifest`.

        Parameters
        ----------
        payload:
            A JSON string previously produced by :meth:`to_json` or
            compatible with the serialization schema.

        Returns
        -------
        PackageManifest

        Raises
        ------
        json.JSONDecodeError
            If *payload* is not valid JSON.
        KeyError
            If a required key is missing from the parsed JSON object.
        """
        raw = json.loads(payload)
        if not isinstance(raw, dict):
            raise ValueError(
                f"Expected a JSON object at top level, got {type(raw).__name__!r}."
            )
        return self.from_dict(raw)

    # ------------------------------------------------------------------
    # Dict round-trip
    # ------------------------------------------------------------------

    def to_dict(self, manifest: PackageManifest) -> dict[str, Any]:
        """Convert *manifest* to a plain-Python dictionary.

        This is identical to :meth:`PackageManifest.to_dict` but exposed here
        so callers can use the serializer as a single façade for all
        serialization needs.

        Parameters
        ----------
        manifest:
            The manifest to convert.

        Returns
        -------
        dict[str, Any]
        """
        return manifest.to_dict()

    def from_dict(self, d: dict[str, Any]) -> PackageManifest:
        """Reconstruct a :class:`PackageManifest` from a plain dictionary.

        Parameters
        ----------
        d:
            A dictionary with the same keys as produced by :meth:`to_dict`.

        Returns
        -------
        PackageManifest

        Raises
        ------
        KeyError
            If a required key is absent.
        ValueError
            If a ``capabilities`` entry is not a known :class:`PackageCapability`
            value string.
        """
        # Build a lookup for fast capability deserialization
        cap_by_value: dict[str, PackageCapability] = {
            c.value: c for c in PackageCapability
        }

        raw_caps: list[str] = d["capabilities"]
        capabilities: frozenset[PackageCapability] = frozenset()
        unknown_caps: list[str] = []
        resolved_caps: set[PackageCapability] = set()
        for raw_cap in raw_caps:
            if raw_cap in cap_by_value:
                resolved_caps.add(cap_by_value[raw_cap])
            else:
                unknown_caps.append(raw_cap)
        if unknown_caps:
            import sys as _sys
            print(
                f"[ManifestSerializer] Warning: unknown capabilities skipped: "
                f"{unknown_caps!r}",
                file=_sys.stderr,
            )
        capabilities = frozenset(resolved_caps)

        return PackageManifest(
            name=d["name"],
            version=d["version"],
            capabilities=capabilities,
            theory_chapter=d["theory_chapter"],
            description=d["description"],
            author=d["author"],
            dependencies=tuple(d["dependencies"]),
            created_at=d["created_at"],
        )


# ---------------------------------------------------------------------------
# ManifestDiagnostics
# ---------------------------------------------------------------------------


class ManifestDiagnostics:
    """Produces human-readable diagnostic reports for :class:`PackageManifest` objects.

    This class is intended for use in debugging, CI summaries, and development
    tooling.  All methods are pure (no side effects on manifests or registries).
    """

    def __init__(self) -> None:
        self._validator = ManifestValidator()

    # ------------------------------------------------------------------
    # Single-manifest diagnostics
    # ------------------------------------------------------------------

    def report(self, manifest: PackageManifest) -> str:
        """Return a multi-line diagnostic report for *manifest*.

        The report includes version, capabilities (with descriptions),
        dependencies, theory chapter, author, and validation status.

        Parameters
        ----------
        manifest:
            The manifest to report on.

        Returns
        -------
        str
            A non-empty, human-readable multi-line string.
        """
        lines: list[str] = []
        sep = "─" * 60
        lines.append(sep)
        lines.append(f"  Package : {manifest.name}")
        lines.append(f"  Version : {manifest.version}")
        lines.append(f"  Chapter : {manifest.theory_chapter}")
        lines.append(f"  Author  : {manifest.author}")
        lines.append(f"  Created : {manifest.created_at}")
        lines.append(f"  Desc    : {manifest.description}")
        lines.append("")
        lines.append("  Capabilities:")
        for cap in sorted(manifest.capabilities, key=lambda c: c.value):
            code = cap.short_code()
            desc = cap.description()
            lines.append(f"    [{code}] {cap.value}")
            lines.append(f"         {desc}")
        lines.append("")
        lines.append("  Dependencies:")
        if manifest.dependencies:
            for dep in sorted(manifest.dependencies):
                lines.append(f"    • {dep}")
        else:
            lines.append("    (none)")
        lines.append("")
        errors = self._validator.validate(manifest)
        if errors:
            lines.append("  Validation FAILED:")
            for err in errors:
                lines.append(f"    ✗ {err}")
        else:
            lines.append("  Validation: OK ✓")
        lines.append(sep)
        return "\n".join(lines)

    def capability_summary(self, manifest: PackageManifest) -> dict[str, bool]:
        """Return a dict mapping each :class:`PackageCapability` value to ``True``
        if the manifest has that capability, ``False`` otherwise.

        This provides a quick at-a-glance capability presence table.

        Parameters
        ----------
        manifest:
            The manifest to summarize.

        Returns
        -------
        dict[str, bool]
            Keys are capability value strings; values are presence booleans.

        Examples
        --------
        >>> diag = ManifestDiagnostics()
        >>> summary = diag.capability_summary(_DEFAULT_MANIFEST)
        >>> summary["analogy_construction"]
        True
        """
        return {cap.value: cap in manifest.capabilities for cap in PackageCapability}

    # ------------------------------------------------------------------
    # Registry-level diagnostics
    # ------------------------------------------------------------------

    def dependency_report(self, registry: PackageRegistry) -> str:
        """Return a human-readable summary of the dependency graph in *registry*.

        For each registered manifest the report lists its direct dependencies,
        noting which are registered (``✓``) and which are missing (``✗``).

        Parameters
        ----------
        registry:
            The :class:`PackageRegistry` to inspect.

        Returns
        -------
        str
            A multi-line string.  Returns ``"(empty registry)"`` when the
            registry contains no manifests.
        """
        manifests = registry.all_manifests()
        if not manifests:
            return "(empty registry)"

        lines: list[str] = ["Dependency Report", "=" * 40]
        for manifest in sorted(manifests, key=lambda m: m.name):
            lines.append(f"\n{manifest.name} v{manifest.version}")
            if manifest.dependencies:
                for dep in sorted(manifest.dependencies):
                    present = dep in registry
                    mark = "✓" if present else "✗"
                    lines.append(f"  {mark} {dep}")
            else:
                lines.append("  (no dependencies)")
        lines.append("")
        return "\n".join(lines)

    def validate_all(self, registry: PackageRegistry) -> dict[str, list[str]]:
        """Validate every manifest in *registry* and return a map of errors.

        Parameters
        ----------
        registry:
            The :class:`PackageRegistry` to validate.

        Returns
        -------
        dict[str, list[str]]
            Maps each package name to its list of validation errors.  Packages
            with no errors map to an empty list.
        """
        result: dict[str, list[str]] = {}
        for manifest in registry.all_manifests():
            errors = self._validator.validate(manifest)
            result[manifest.name] = errors
        return result

    def score_report(
        self, query: CapabilityQuery, registry: PackageRegistry
    ) -> str:
        """Return a scored report of all manifests in *registry* for *query*.

        Manifests are listed in descending score order with their match status
        and numeric score.

        Parameters
        ----------
        query:
            The :class:`CapabilityQuery` to score against.
        registry:
            The registry to inspect.

        Returns
        -------
        str
        """
        manifests = registry.all_manifests()
        if not manifests:
            return "(empty registry)"

        scored = sorted(
            ((query.score(m), m) for m in manifests),
            key=lambda t: t[0],
            reverse=True,
        )
        lines: list[str] = [
            f"Score Report for query: {query!r}",
            "-" * 50,
        ]
        for score, manifest in scored:
            matches_mark = "✓" if query.matches(manifest) else "✗"
            lines.append(
                f"  {matches_mark} [{score:.3f}] {manifest.name} v{manifest.version}"
            )
        lines.append("")
        return "\n".join(lines)

    def __repr__(self) -> str:
        return "ManifestDiagnostics()"


# ---------------------------------------------------------------------------
# Module-level default manifest
# ---------------------------------------------------------------------------

_DEFAULT_MANIFEST = PackageManifest(
    name=PACKAGE_NAME,
    version=PACKAGE_VERSION,
    capabilities=frozenset(PackageCapability),
    theory_chapter=THEORY_CHAPTER,
    description="Analogy-based cross-regime idea transport (Ch60).",
    author="jugeo",
    dependencies=(
        "jugeo.ideation.ideas",
        "jugeo.ideation.federation",
        "jugeo.evidence.trust",
        "jugeo.ideation.novelty",
    ),
    created_at=_now_iso(),
)
