r"""Pack loading, validation, and registration for the JuGeo kernel.

Domain packs are the unit of mathematical content in JuGeo.  Each pack bundles
definitions, theorems, laws, and adapters that belong to a single algebraic or
geometric domain (e.g.\ ``group-theory``, ``topological-spaces``).  Before a
pack's capabilities become available to the evidence channels the pack must be
*discovered*, *dependency-resolved*, *validated*, and *registered* with the
kernel's service registry.

This module implements the full lifecycle:

1. **Discovery** — scan directories, entry-points, and the pack federation
   registry to locate available packs (``PackDiscoverer``).
2. **Dependency resolution** — build the dependency DAG, topological-sort it,
   and detect cycles or missing requirements (``PackDependencyResolver``).
3. **Validation** — check every descriptor against the JuGeo schema, verify
   bridge compatibility, and ensure the trust ceiling does not exceed the
   jurisdiction ceiling (``PackValidator``).
4. **Registration** — insert the pack into the live ``PackRegistry`` and wire
   it into the kernel so that evidence channels can route requests to it.
5. **Lifecycle** — initialise, activate, deactivate, and dispose of packs with
   full health-check support (``PackLifecycle``).

Theory alignment
----------------
Sections 248–256 of ``preliminaries/theory2.tex`` describe how domain packs
form a *presheaf* over the site of mathematical structures.  Pack loading
realises the *stalk construction* at a coordinate: only the packs whose
jurisdiction covers that coordinate are activated.

Public types
------------
``PackDescriptor``, ``PackLoader``, ``PackDiscoverer``,
``PackDependencyResolver``, ``PackValidator``, ``PackRegistry``,
``PackVersionManager``, ``PackConfiguration``, ``PackLifecycle``,
``PackLoadingHistory``, ``PackSerializer``.

Legacy helpers ``PackLoadRequest``, ``PackLoadResult``, and ``load_pack`` are
retained for backward compatibility with existing call-sites.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from collections import defaultdict, deque
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import (
    Any,
    Callable,
    Iterator,
    Mapping,
    Sequence,
)

from jugeo.evidence.trust import TrustTier
from jugeo.packs.authority import PackAuthority, authorize_pack
from jugeo.packs.catalog import PackCatalog
from jugeo.packs.catalog import PackDescriptor as CatalogDescriptor

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class PackStatus(str, Enum):
    """Lifecycle status of a loaded pack."""

    DISCOVERED = "discovered"
    RESOLVED = "resolved"
    VALIDATED = "validated"
    REGISTERED = "registered"
    ACTIVE = "active"
    INACTIVE = "inactive"
    FAILED = "failed"
    DISPOSED = "disposed"


class LoadEventKind(str, Enum):
    """Kind of event recorded in the loading history."""

    DISCOVER = "discover"
    RESOLVE = "resolve"
    VALIDATE = "validate"
    REGISTER = "register"
    ACTIVATE = "activate"
    DEACTIVATE = "deactivate"
    UNLOAD = "unload"
    FAILURE = "failure"
    RETRY = "retry"


# ---------------------------------------------------------------------------
# 1. PackDescriptor
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class PackDescriptor:
    """Immutable descriptor for a loadable domain pack.

    A ``PackDescriptor`` carries all metadata required by the loading pipeline
    *before* the pack's Python entry-point is imported.  It is typically
    deserialised from a ``pack.json`` manifest that ships alongside the pack
    source.

    Attributes
    ----------
    pack_id : str
        Globally unique identifier (UUID-4 string).
    name : str
        Human-readable pack name (e.g. ``"group-theory"``).
    version : str
        Semantic-version string (``"major.minor.patch"``).
    description : str
        One-paragraph summary shown in pack listings and copilot suggestions.
    author : str
        Pack author or maintaining organisation.
    dependencies : tuple[str, ...]
        Pack IDs this pack depends on at load time.
    provides : tuple[str, ...]
        Capability tokens this pack makes available (e.g. ``"group-actions"``).
    requires : tuple[str, ...]
        Capability tokens this pack needs from other packs.
    trust_ceiling : TrustTier
        Maximum trust tier that evidence produced by this pack may carry.
    entry_point : str
        Dotted Python import path to the pack's initialisation module.
    is_builtin : bool
        ``True`` when the pack ships with JuGeo core and bypasses external
        discovery.
    metadata : Mapping[str, Any]
        Free-form metadata for tooling, copilot indexing, and diagnostics.
    """

    pack_id: str
    name: str
    version: str
    description: str = ""
    author: str = ""
    dependencies: tuple[str, ...] = ()
    provides: tuple[str, ...] = ()
    requires: tuple[str, ...] = ()
    trust_ceiling: TrustTier = TrustTier.PROPOSAL
    entry_point: str = ""
    is_builtin: bool = False
    metadata: Mapping[str, Any] = field(default_factory=dict)

    # -- helpers -------------------------------------------------------------

    def satisfies(self, capability: str) -> bool:
        """Return ``True`` if this pack provides *capability*."""
        return capability in self.provides

    def needs(self, capability: str) -> bool:
        """Return ``True`` if this pack requires *capability*."""
        return capability in self.requires

    def depends_on(self, other_id: str) -> bool:
        """Return ``True`` if *other_id* is a declared dependency."""
        return other_id in self.dependencies

    def version_tuple(self) -> tuple[int, ...]:
        """Parse ``version`` into an integer tuple for comparison."""
        parts: list[int] = []
        for segment in self.version.split("."):
            try:
                parts.append(int(segment))
            except ValueError:
                parts.append(0)
        return tuple(parts)

    def to_dict(self) -> dict[str, Any]:
        """Serialise the descriptor to a plain dictionary."""
        return {
            "pack_id": self.pack_id,
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "author": self.author,
            "dependencies": list(self.dependencies),
            "provides": list(self.provides),
            "requires": list(self.requires),
            "trust_ceiling": self.trust_ceiling.value
            if hasattr(self.trust_ceiling, "value")
            else str(self.trust_ceiling),
            "entry_point": self.entry_point,
            "is_builtin": self.is_builtin,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> PackDescriptor:
        """Construct a descriptor from a mapping (e.g. parsed JSON)."""
        raw_ceiling = data.get("trust_ceiling", "UNVERIFIED")
        if isinstance(raw_ceiling, TrustTier):
            ceiling = raw_ceiling
        else:
            ceiling = TrustTier.PROPOSAL if str(raw_ceiling).upper() == "UNVERIFIED" else TrustTier[str(raw_ceiling).upper()]
        return cls(
            pack_id=str(data.get("pack_id", uuid.uuid4().hex)),
            name=str(data["name"]),
            version=str(data.get("version", "0.0.0")),
            description=str(data.get("description", "")),
            author=str(data.get("author", "")),
            dependencies=tuple(data.get("dependencies", ())),
            provides=tuple(data.get("provides", ())),
            requires=tuple(data.get("requires", ())),
            trust_ceiling=ceiling,
            entry_point=str(data.get("entry_point", "")),
            is_builtin=bool(data.get("is_builtin", False)),
            metadata=dict(data.get("metadata", {})),
        )


# ---------------------------------------------------------------------------
# 2. PackDiscoverer
# ---------------------------------------------------------------------------

class PackDiscoverer:
    """Discovers available packs from multiple sources.

    Sources include the file system (``scan_directory``), Python entry-points
    (``scan_entry_points``), and an external federation registry
    (``scan_registry``).  Results are merged, deduplicated, and filtered for
    compatibility before being handed to the loader.
    """

    def __init__(self, *, compatible_version: str = "0.0.0") -> None:
        self._compatible_version = compatible_version
        self._sources: dict[str, list[PackDescriptor]] = defaultdict(list)
        self._scan_count: int = 0

    # -- scanning -----------------------------------------------------------

    def scan_directory(self, directory: Path) -> list[PackDescriptor]:
        """Scan *directory* for ``pack.json`` manifests and return descriptors.

        Each immediate subdirectory that contains a ``pack.json`` file is
        treated as a candidate pack.  The manifest is parsed and validated at
        the descriptor level only (full schema validation is deferred to
        ``PackValidator``).
        """
        found: list[PackDescriptor] = []
        if not directory.is_dir():
            logger.warning("Pack directory does not exist: %s", directory)
            return found
        for child in sorted(directory.iterdir()):
            manifest = child / "pack.json"
            if not manifest.is_file():
                continue
            try:
                with manifest.open("r", encoding="utf-8") as fh:
                    raw = json.load(fh)
                desc = PackDescriptor.from_dict(raw)
                found.append(desc)
                logger.debug("Discovered pack %s at %s", desc.name, child)
            except (json.JSONDecodeError, KeyError, TypeError) as exc:
                logger.warning("Skipping malformed manifest %s: %s", manifest, exc)
        self._sources["directory"].extend(found)
        self._scan_count += 1
        return found

    def scan_entry_points(self, group: str = "jugeo.packs") -> list[PackDescriptor]:
        """Scan installed Python packages for entry-points in *group*.

        Each entry-point is expected to resolve to a callable that returns a
        ``Mapping`` parseable by ``PackDescriptor.from_dict``.  This allows
        third-party packages to advertise JuGeo packs via standard Python
        packaging metadata.
        """
        found: list[PackDescriptor] = []
        try:
            from importlib.metadata import entry_points as _ep  # noqa: WPS433
            eps = _ep(group=group)
        except Exception:  # noqa: BLE001
            logger.debug("Entry-point scanning unavailable or empty for group=%s", group)
            return found
        for ep in eps:
            try:
                factory = ep.load()
                raw = factory()
                desc = PackDescriptor.from_dict(raw)
                found.append(desc)
                logger.debug("Discovered entry-point pack %s via %s", desc.name, ep.name)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Failed to load entry-point %s: %s", ep.name, exc)
        self._sources["entry_points"].extend(found)
        self._scan_count += 1
        return found

    def scan_registry(self, registry_url: str) -> list[PackDescriptor]:
        """Query an external federation registry at *registry_url*.

        The registry is expected to return a JSON array of pack descriptor
        objects.  Network errors are logged but do not raise—discovery must
        be resilient because copilot-assisted workflows should not block on
        registry downtime.
        """
        found: list[PackDescriptor] = []
        logger.info("Registry scanning from %s (stub — network not called)", registry_url)
        self._sources["registry"].extend(found)
        self._scan_count += 1
        return found

    def merge_sources(self) -> list[PackDescriptor]:
        """Merge all previously scanned sources into a single list."""
        merged: list[PackDescriptor] = []
        for source_name in ("directory", "entry_points", "registry"):
            merged.extend(self._sources.get(source_name, []))
        return merged

    def filter_compatible(self, descriptors: Sequence[PackDescriptor]) -> list[PackDescriptor]:
        """Return only descriptors whose version is >= *compatible_version*."""
        min_version = _parse_version(self._compatible_version)
        return [d for d in descriptors if d.version_tuple() >= min_version]

    def deduplicate(self, descriptors: Sequence[PackDescriptor]) -> list[PackDescriptor]:
        """Remove duplicate pack IDs, keeping the highest version of each.

        When two descriptors share the same ``pack_id``, the one with the
        higher semantic version wins.  If versions are equal the first
        occurrence is retained.
        """
        best: dict[str, PackDescriptor] = {}
        for desc in descriptors:
            existing = best.get(desc.pack_id)
            if existing is None or desc.version_tuple() > existing.version_tuple():
                best[desc.pack_id] = desc
        return list(best.values())

    @property
    def scan_count(self) -> int:
        """Number of scan operations performed so far."""
        return self._scan_count


# ---------------------------------------------------------------------------
# 3. PackDependencyResolver
# ---------------------------------------------------------------------------

class PackDependencyResolver:
    """Resolves ordering and conflicts among pack dependencies.

    The resolver builds a directed acyclic graph from declared dependencies,
    topologically sorts it, and detects cycles, missing packs, and capability
    conflicts.  Resolution results drive the order in which ``PackLoader``
    initialises packs.
    """

    def __init__(self) -> None:
        self._graph: dict[str, set[str]] = defaultdict(set)
        self._descriptors: dict[str, PackDescriptor] = {}

    # -- public API ---------------------------------------------------------

    def resolve(self, descriptors: Sequence[PackDescriptor]) -> list[PackDescriptor]:
        """Resolve *descriptors* and return them in safe load-order.

        Raises ``ValueError`` if cycles are detected or required packs are
        missing.
        """
        self._descriptors = {d.pack_id: d for d in descriptors}
        self.build_dependency_graph(descriptors)
        cycles = self.detect_cycles()
        if cycles:
            msg = "; ".join(" -> ".join(c) for c in cycles)
            raise ValueError(f"Dependency cycles detected: {msg}")
        missing = self.find_missing()
        if missing:
            pairs = ", ".join(f"{pid} requires {dep}" for pid, dep in missing)
            raise ValueError(f"Missing dependencies: {pairs}")
        order = self.topological_sort()
        return [self._descriptors[pid] for pid in order if pid in self._descriptors]

    def build_dependency_graph(self, descriptors: Sequence[PackDescriptor]) -> dict[str, set[str]]:
        """Build an adjacency-list graph from declared dependencies."""
        self._graph.clear()
        for desc in descriptors:
            self._graph.setdefault(desc.pack_id, set())
            for dep_id in desc.dependencies:
                self._graph[desc.pack_id].add(dep_id)
                self._graph.setdefault(dep_id, set())
        return dict(self._graph)

    def topological_sort(self) -> list[str]:
        """Return pack IDs in topological order (dependencies first).

        Uses Kahn's algorithm so that the result is deterministic for a given
        input graph (ties broken lexicographically).
        """
        in_degree: dict[str, int] = {node: 0 for node in self._graph}
        for node, deps in self._graph.items():
            for dep in deps:
                in_degree.setdefault(dep, 0)
                in_degree[node] = in_degree.get(node, 0)
            for dep in deps:
                # dep must be loaded before node, so edge is dep -> node
                pass
        # Recompute using forward edges: dep -> [dependents]
        forward: dict[str, list[str]] = defaultdict(list)
        in_deg: dict[str, int] = {n: 0 for n in self._graph}
        for node, deps in self._graph.items():
            in_deg[node] = len(deps)
            for dep in deps:
                forward[dep].append(node)
                in_deg.setdefault(dep, 0)
        queue: list[str] = sorted(n for n, d in in_deg.items() if d == 0)
        order: list[str] = []
        while queue:
            current = queue.pop(0)
            order.append(current)
            for dependent in sorted(forward.get(current, [])):
                in_deg[dependent] -= 1
                if in_deg[dependent] == 0:
                    queue.append(dependent)
                    queue.sort()
        if len(order) != len(in_deg):
            logger.error("Topological sort incomplete — possible cycle")
        return order

    def detect_cycles(self) -> list[list[str]]:
        """Detect cycles in the dependency graph using DFS.

        Returns a list of cycles, each represented as a list of pack IDs
        forming a loop.
        """
        WHITE, GREY, BLACK = 0, 1, 2  # noqa: N806
        colour: dict[str, int] = {n: WHITE for n in self._graph}
        parent: dict[str, str | None] = {n: None for n in self._graph}
        cycles: list[list[str]] = []

        def _dfs(node: str) -> None:
            colour[node] = GREY
            for dep in sorted(self._graph.get(node, ())):
                if dep not in colour:
                    colour[dep] = WHITE
                if colour.get(dep) == WHITE:
                    parent[dep] = node
                    _dfs(dep)
                elif colour.get(dep) == GREY:
                    cycle = [dep, node]
                    cur = node
                    while parent.get(cur) is not None and parent[cur] != dep:
                        cur = parent[cur]  # type: ignore[assignment]
                        cycle.append(cur)
                    cycles.append(list(reversed(cycle)))
            colour[node] = BLACK

        for node in sorted(self._graph):
            if colour.get(node, WHITE) == WHITE:
                _dfs(node)
        return cycles

    def find_missing(self) -> list[tuple[str, str]]:
        """Return ``(pack_id, missing_dependency)`` pairs."""
        known = set(self._descriptors)
        missing: list[tuple[str, str]] = []
        for pid, deps in self._graph.items():
            for dep in sorted(deps):
                if dep not in known:
                    missing.append((pid, dep))
        return missing

    def find_conflicts(self, descriptors: Sequence[PackDescriptor]) -> list[tuple[str, str, str]]:
        """Find capability conflicts where two packs provide the same token.

        Returns ``(capability, pack_a, pack_b)`` triples.
        """
        providers: dict[str, list[str]] = defaultdict(list)
        for desc in descriptors:
            for cap in desc.provides:
                providers[cap].append(desc.pack_id)
        conflicts: list[tuple[str, str, str]] = []
        for cap, pids in providers.items():
            if len(pids) > 1:
                for i in range(len(pids)):
                    for j in range(i + 1, len(pids)):
                        conflicts.append((cap, pids[i], pids[j]))
        return conflicts

    def suggest_resolution(self, conflicts: Sequence[tuple[str, str, str]]) -> list[str]:
        """Return human-readable suggestions for resolving *conflicts*.

        Copilot can present these suggestions directly to the user.
        """
        suggestions: list[str] = []
        for cap, a, b in conflicts:
            name_a = self._descriptors.get(a)
            name_b = self._descriptors.get(b)
            label_a = name_a.name if name_a else a
            label_b = name_b.name if name_b else b
            suggestions.append(
                f"Capability '{cap}' is provided by both '{label_a}' and "
                f"'{label_b}'.  Remove one or set an explicit priority in "
                f"the pack configuration."
            )
        return suggestions


# ---------------------------------------------------------------------------
# 4. PackValidator
# ---------------------------------------------------------------------------

class PackValidator:
    """Validates pack descriptors and their relationships.

    Validation is intentionally strict: silent mis-configuration in a
    trust-sensitive system is worse than a loud failure at load time.
    """

    # Required top-level fields for a valid descriptor.
    REQUIRED_FIELDS: frozenset[str] = frozenset({"pack_id", "name", "version"})
    # Maximum allowed length for string fields.
    MAX_FIELD_LENGTH: int = 512

    def __init__(self, *, jurisdiction_ceiling: TrustTier | None = None) -> None:
        self._jurisdiction_ceiling = jurisdiction_ceiling
        self._errors: list[str] = []

    @property
    def errors(self) -> list[str]:
        """Accumulated validation errors since the last reset."""
        return list(self._errors)

    def reset(self) -> None:
        """Clear accumulated errors."""
        self._errors.clear()

    # -- individual checks --------------------------------------------------

    def validate_descriptor(self, desc: PackDescriptor) -> bool:
        """Validate basic field constraints on *desc*.

        Checks that required fields are non-empty and that string lengths do
        not exceed ``MAX_FIELD_LENGTH``.
        """
        ok = True
        if not desc.pack_id:
            self._errors.append(f"Pack descriptor missing pack_id")
            ok = False
        if not desc.name:
            self._errors.append(f"Pack descriptor {desc.pack_id!r} missing name")
            ok = False
        if not desc.version:
            self._errors.append(f"Pack {desc.name!r} missing version")
            ok = False
        for field_name in ("name", "version", "description", "author", "entry_point"):
            value = getattr(desc, field_name, "")
            if len(str(value)) > self.MAX_FIELD_LENGTH:
                self._errors.append(
                    f"Pack {desc.name!r}: field '{field_name}' exceeds "
                    f"{self.MAX_FIELD_LENGTH} characters"
                )
                ok = False
        return ok

    def validate_schema(self, raw: Mapping[str, Any]) -> bool:
        """Validate a raw mapping against the pack descriptor schema.

        This is a lightweight check performed *before* constructing a
        ``PackDescriptor`` to catch gross structural errors early.
        """
        ok = True
        for key in self.REQUIRED_FIELDS:
            if key not in raw or not raw[key]:
                self._errors.append(f"Schema: missing required field '{key}'")
                ok = False
        if "dependencies" in raw and not isinstance(raw["dependencies"], (list, tuple)):
            self._errors.append("Schema: 'dependencies' must be a list")
            ok = False
        if "provides" in raw and not isinstance(raw["provides"], (list, tuple)):
            self._errors.append("Schema: 'provides' must be a list")
            ok = False
        if "requires" in raw and not isinstance(raw["requires"], (list, tuple)):
            self._errors.append("Schema: 'requires' must be a list")
            ok = False
        if "version" in raw:
            parts = str(raw["version"]).split(".")
            if len(parts) < 2:
                self._errors.append(
                    f"Schema: version '{raw['version']}' should follow semver (major.minor.patch)"
                )
                ok = False
        return ok

    def validate_bridge_compatibility(
        self,
        desc: PackDescriptor,
        available_bridges: Sequence[str],
    ) -> bool:
        """Check that every bridge slot declared in *desc.metadata* exists.

        Bridge slots are optional metadata entries that name transport
        theorems connecting this pack to others.  If a slot references a
        bridge that is not available, the pack cannot function correctly.
        """
        ok = True
        bridge_slots: Sequence[str] = desc.metadata.get("bridge_slots", ())
        for slot in bridge_slots:
            if slot not in available_bridges:
                self._errors.append(
                    f"Pack {desc.name!r}: bridge slot '{slot}' not found "
                    f"in available bridges"
                )
                ok = False
        return ok

    def validate_trust_ceiling(self, desc: PackDescriptor) -> bool:
        """Ensure the pack's trust ceiling does not exceed the jurisdiction.

        A pack must not claim a trust ceiling higher than what the hosting
        jurisdiction allows.  This prevents silent trust promotion, which
        theory2.tex §251 explicitly forbids.
        """
        if self._jurisdiction_ceiling is None:
            return True
        try:
            desc_ord = _trust_ordinal(desc.trust_ceiling)
            jur_ord = _trust_ordinal(self._jurisdiction_ceiling)
        except (AttributeError, TypeError):
            self._errors.append(
                f"Pack {desc.name!r}: unable to compare trust ceilings"
            )
            return False
        if desc_ord > jur_ord:
            self._errors.append(
                f"Pack {desc.name!r}: trust ceiling "
                f"{desc.trust_ceiling} exceeds jurisdiction ceiling "
                f"{self._jurisdiction_ceiling}"
            )
            return False
        return True

    def validate_jurisdiction(
        self,
        desc: PackDescriptor,
        allowed_coordinates: Sequence[str],
    ) -> bool:
        """Check that the pack is authorised for the given coordinates.

        A pack's metadata may declare ``jurisdiction_coordinates``.  If the
        intersection with *allowed_coordinates* is empty the pack is not
        permitted to operate.
        """
        declared: Sequence[str] = desc.metadata.get("jurisdiction_coordinates", ())
        if not declared:
            # No jurisdiction restriction — pack is globally available.
            return True
        overlap = set(declared) & set(allowed_coordinates)
        if not overlap:
            self._errors.append(
                f"Pack {desc.name!r}: no jurisdiction overlap with "
                f"allowed coordinates {allowed_coordinates}"
            )
            return False
        return True

    def full_validation(
        self,
        desc: PackDescriptor,
        *,
        available_bridges: Sequence[str] = (),
        allowed_coordinates: Sequence[str] = (),
    ) -> bool:
        """Run all validation checks on *desc* and return overall result."""
        results = [
            self.validate_descriptor(desc),
            self.validate_trust_ceiling(desc),
            self.validate_bridge_compatibility(desc, available_bridges),
            self.validate_jurisdiction(desc, allowed_coordinates),
        ]
        return all(results)


# ---------------------------------------------------------------------------
# 5. PackRegistry
# ---------------------------------------------------------------------------

class PackRegistry:
    """In-memory registry of loaded and active packs.

    The registry provides lookup by ID, name, and capability token.  It is
    the canonical source of truth for which packs are currently available to
    the kernel and to copilot-assisted evidence routing.
    """

    def __init__(self) -> None:
        self._packs: dict[str, PackDescriptor] = {}
        self._status: dict[str, PackStatus] = {}
        self._capability_index: dict[str, set[str]] = defaultdict(set)
        self._requirement_index: dict[str, set[str]] = defaultdict(set)

    # -- mutators -----------------------------------------------------------

    def register(self, desc: PackDescriptor, *, status: PackStatus = PackStatus.REGISTERED) -> None:
        """Add *desc* to the registry.

        If a pack with the same ``pack_id`` is already registered it is
        replaced, and indices are rebuilt.
        """
        self._packs[desc.pack_id] = desc
        self._status[desc.pack_id] = status
        for cap in desc.provides:
            self._capability_index[cap].add(desc.pack_id)
        for req in desc.requires:
            self._requirement_index[req].add(desc.pack_id)
        logger.info("Registered pack %s (%s) as %s", desc.name, desc.pack_id, status.value)

    def unregister(self, pack_id: str) -> PackDescriptor | None:
        """Remove the pack with *pack_id* and return its descriptor (or ``None``)."""
        desc = self._packs.pop(pack_id, None)
        self._status.pop(pack_id, None)
        if desc is not None:
            for cap in desc.provides:
                self._capability_index.get(cap, set()).discard(pack_id)
            for req in desc.requires:
                self._requirement_index.get(req, set()).discard(pack_id)
            logger.info("Unregistered pack %s", desc.name)
        return desc

    # -- queries ------------------------------------------------------------

    def get_pack(self, pack_id: str) -> PackDescriptor | None:
        """Return the descriptor for *pack_id* or ``None``."""
        return self._packs.get(pack_id)

    def get_status(self, pack_id: str) -> PackStatus | None:
        """Return the current lifecycle status for *pack_id*."""
        return self._status.get(pack_id)

    def set_status(self, pack_id: str, status: PackStatus) -> None:
        """Update the lifecycle status for *pack_id*."""
        if pack_id in self._packs:
            self._status[pack_id] = status

    def list_packs(self, *, status_filter: PackStatus | None = None) -> list[PackDescriptor]:
        """Return all registered descriptors, optionally filtered by status."""
        if status_filter is None:
            return list(self._packs.values())
        return [
            d for d in self._packs.values()
            if self._status.get(d.pack_id) == status_filter
        ]

    def packs_providing(self, capability: str) -> list[PackDescriptor]:
        """Return all packs that provide *capability*."""
        ids = self._capability_index.get(capability, set())
        return [self._packs[pid] for pid in ids if pid in self._packs]

    def packs_requiring(self, capability: str) -> list[PackDescriptor]:
        """Return all packs that require *capability*."""
        ids = self._requirement_index.get(capability, set())
        return [self._packs[pid] for pid in ids if pid in self._packs]

    def dependency_graph(self) -> dict[str, list[str]]:
        """Return the adjacency-list dependency graph for all registered packs."""
        graph: dict[str, list[str]] = {}
        for pid, desc in self._packs.items():
            graph[pid] = [d for d in desc.dependencies if d in self._packs]
        return graph

    def __len__(self) -> int:
        return len(self._packs)

    def __contains__(self, pack_id: str) -> bool:
        return pack_id in self._packs


# ---------------------------------------------------------------------------
# 6. PackVersionManager
# ---------------------------------------------------------------------------

class PackVersionManager:
    """Utilities for comparing and managing pack versions.

    All version strings follow semantic versioning (``major.minor.patch``).
    Comparison respects the standard precedence rules.
    """

    @staticmethod
    def compare_versions(a: str, b: str) -> int:
        """Compare two version strings.

        Returns a negative number if *a < b*, zero if equal, positive if
        *a > b*.
        """
        ta = _parse_version(a)
        tb = _parse_version(b)
        if ta < tb:
            return -1
        if ta > tb:
            return 1
        return 0

    @staticmethod
    def is_compatible(required: str, available: str) -> bool:
        """Check backward-compatible semver match.

        *available* is compatible with *required* when the major versions
        match and *available >= required*.
        """
        r = _parse_version(required)
        a = _parse_version(available)
        if not r or not a:
            return False
        return a[0] == r[0] and a >= r

    @staticmethod
    def find_latest(versions: Sequence[str]) -> str | None:
        """Return the highest version string from *versions*."""
        if not versions:
            return None
        return max(versions, key=_parse_version)

    @staticmethod
    def find_matching(
        requirement: str,
        candidates: Sequence[PackDescriptor],
    ) -> list[PackDescriptor]:
        """Return candidates whose version is compatible with *requirement*."""
        return [
            c for c in candidates
            if PackVersionManager.is_compatible(requirement, c.version)
        ]

    @staticmethod
    def upgrade_path(current: str, available: Sequence[str]) -> list[str]:
        """Compute an ordered upgrade path from *current* to the latest.

        Returns all versions strictly greater than *current*, sorted
        ascending, that share the same major version.
        """
        cur = _parse_version(current)
        if not cur:
            return []
        candidates = []
        for v in available:
            pv = _parse_version(v)
            if pv > cur and pv[0] == cur[0]:
                candidates.append((pv, v))
        candidates.sort()
        return [v for _, v in candidates]

    @staticmethod
    def migration_needed(from_version: str, to_version: str) -> bool:
        """Return ``True`` if migrating from *from_version* to *to_version*
        crosses a major-version boundary (breaking change).
        """
        fv = _parse_version(from_version)
        tv = _parse_version(to_version)
        if not fv or not tv:
            return True
        return fv[0] != tv[0]


# ---------------------------------------------------------------------------
# 7. PackConfiguration
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class PackConfiguration:
    """Per-pack configuration container.

    Configuration is layered: pack defaults → user config → runtime
    overrides.  Each layer is a flat ``str → Any`` mapping.  This class
    manages merging and validation.

    Attributes
    ----------
    pack_id : str
        Identifier of the pack this configuration belongs to.
    defaults : dict[str, Any]
        Default values shipped with the pack.
    user_config : dict[str, Any]
        Values set by the user (e.g. from ``~/.jugeo/pack-config.toml``).
    overrides : dict[str, Any]
        Runtime overrides applied programmatically (e.g. by copilot).
    """

    pack_id: str
    defaults: dict[str, Any] = field(default_factory=dict)
    user_config: dict[str, Any] = field(default_factory=dict)
    overrides: dict[str, Any] = field(default_factory=dict)

    def load_config(self, path: Path) -> None:
        """Load user configuration from a JSON file at *path*.

        If the file does not exist or is malformed, ``user_config`` is left
        empty and a warning is logged.
        """
        if not path.is_file():
            logger.debug("No config file at %s for pack %s", path, self.pack_id)
            return
        try:
            with path.open("r", encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, dict):
                self.user_config = data
                logger.debug(
                    "Loaded %d config keys for pack %s", len(data), self.pack_id
                )
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Failed to load config for pack %s: %s", self.pack_id, exc)

    def validate_config(self, schema: Mapping[str, type]) -> list[str]:
        """Validate the effective configuration against *schema*.

        *schema* maps key names to expected Python types.  Returns a list of
        error messages (empty on success).
        """
        effective = self.merge_with_defaults()
        errors: list[str] = []
        for key, expected_type in schema.items():
            if key not in effective:
                errors.append(f"Missing required config key '{key}'")
                continue
            if not isinstance(effective[key], expected_type):
                errors.append(
                    f"Config key '{key}': expected {expected_type.__name__}, "
                    f"got {type(effective[key]).__name__}"
                )
        return errors

    def merge_with_defaults(self) -> dict[str, Any]:
        """Return the effective configuration after merging all layers.

        Priority (highest wins): overrides > user_config > defaults.
        """
        merged: dict[str, Any] = {}
        merged.update(self.defaults)
        merged.update(self.user_config)
        merged.update(self.overrides)
        return merged

    def apply_overrides(self, overrides: Mapping[str, Any]) -> None:
        """Apply additional runtime *overrides*."""
        self.overrides.update(overrides)

    def serialize(self) -> str:
        """Serialise the effective configuration to a JSON string."""
        return json.dumps(self.merge_with_defaults(), indent=2, default=str)

    def get(self, key: str, default: Any = None) -> Any:
        """Retrieve a single effective config value."""
        return self.merge_with_defaults().get(key, default)

    def keys(self) -> list[str]:
        """Return all effective config keys."""
        return list(self.merge_with_defaults().keys())


# ---------------------------------------------------------------------------
# 8. PackLifecycle
# ---------------------------------------------------------------------------

class PackLifecycle:
    """Manages the runtime lifecycle of a loaded pack.

    Every pack transitions through: ``initialise → activate →
    (deactivate ↔ activate)* → dispose``.  Health checks may be run at
    any point after initialisation.
    """

    def __init__(self, descriptor: PackDescriptor, registry: PackRegistry) -> None:
        self._descriptor = descriptor
        self._registry = registry
        self._initialised: bool = False
        self._active: bool = False
        self._disposed: bool = False
        self._health_errors: list[str] = []
        self._init_time: float | None = None

    @property
    def descriptor(self) -> PackDescriptor:
        """The descriptor for the pack this lifecycle manages."""
        return self._descriptor

    def initialize(self) -> bool:
        """Initialise the pack (import entry-point, run setup hooks).

        Returns ``True`` on success.  A pack that fails initialisation is
        marked ``FAILED`` in the registry.
        """
        if self._disposed:
            logger.error("Cannot initialise disposed pack %s", self._descriptor.name)
            return False
        if self._initialised:
            logger.debug("Pack %s already initialised", self._descriptor.name)
            return True
        start = time.monotonic()
        try:
            if self._descriptor.entry_point:
                logger.debug(
                    "Would import entry-point %s for pack %s",
                    self._descriptor.entry_point,
                    self._descriptor.name,
                )
            self._initialised = True
            self._init_time = time.monotonic() - start
            self._registry.set_status(self._descriptor.pack_id, PackStatus.VALIDATED)
            logger.info(
                "Initialised pack %s in %.3fs", self._descriptor.name, self._init_time
            )
            return True
        except Exception as exc:  # noqa: BLE001
            self._health_errors.append(f"Initialisation failed: {exc}")
            self._registry.set_status(self._descriptor.pack_id, PackStatus.FAILED)
            logger.error("Failed to initialise pack %s: %s", self._descriptor.name, exc)
            return False

    def activate(self) -> bool:
        """Activate the pack, making its capabilities available.

        Activation connects the pack's evidence producers to the kernel's
        channel router so that copilot and solver queries can reach it.
        """
        if not self._initialised:
            logger.error("Cannot activate uninitialised pack %s", self._descriptor.name)
            return False
        if self._disposed:
            logger.error("Cannot activate disposed pack %s", self._descriptor.name)
            return False
        self._active = True
        self._registry.set_status(self._descriptor.pack_id, PackStatus.ACTIVE)
        logger.info("Activated pack %s", self._descriptor.name)
        return True

    def deactivate(self) -> bool:
        """Deactivate the pack without disposing it.

        The pack's capabilities are withdrawn from evidence routing but its
        internal state is preserved, allowing rapid re-activation.
        """
        if not self._active:
            logger.debug("Pack %s is not active", self._descriptor.name)
            return True
        self._active = False
        self._registry.set_status(self._descriptor.pack_id, PackStatus.INACTIVE)
        logger.info("Deactivated pack %s", self._descriptor.name)
        return True

    def dispose(self) -> None:
        """Dispose of the pack, releasing all resources.

        After disposal the pack cannot be re-activated; it must be fully
        reloaded.
        """
        if self._active:
            self.deactivate()
        self._disposed = True
        self._initialised = False
        self._registry.set_status(self._descriptor.pack_id, PackStatus.DISPOSED)
        logger.info("Disposed pack %s", self._descriptor.name)

    def health_check(self) -> tuple[bool, list[str]]:
        """Run a health check and return ``(healthy, errors)``.

        A pack is healthy when it is initialised, not disposed, and has no
        recorded health errors.
        """
        errors: list[str] = list(self._health_errors)
        if self._disposed:
            errors.append("Pack is disposed")
        if not self._initialised:
            errors.append("Pack is not initialised")
        healthy = len(errors) == 0
        return healthy, errors

    def is_healthy(self) -> bool:
        """Shorthand returning ``True`` if the pack passes health checks."""
        ok, _ = self.health_check()
        return ok

    @property
    def init_time(self) -> float | None:
        """Time in seconds taken by the last successful initialisation."""
        return self._init_time


# ---------------------------------------------------------------------------
# 9. PackLoadingHistory
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class _LoadEvent:
    """Single loading event record."""

    timestamp: float
    pack_id: str
    pack_name: str
    kind: LoadEventKind
    duration_ms: float
    detail: str


class PackLoadingHistory:
    """Audit trail of pack loading events.

    Every significant loading action (discovery, resolution, validation,
    registration, activation, failure, retry) is recorded with timing
    information.  The history can be queried to diagnose slow start-ups or
    repeated failures.  The ``copilot_loading_summary`` method formats the
    history for display in copilot-assisted diagnostics sessions.
    """

    def __init__(self) -> None:
        self._events: list[_LoadEvent] = []

    def record(
        self,
        pack_id: str,
        pack_name: str,
        kind: LoadEventKind,
        *,
        duration_ms: float = 0.0,
        detail: str = "",
    ) -> None:
        """Append a loading event to the history."""
        self._events.append(
            _LoadEvent(
                timestamp=time.time(),
                pack_id=pack_id,
                pack_name=pack_name,
                kind=kind,
                duration_ms=duration_ms,
                detail=detail,
            )
        )

    def loading_order(self) -> list[str]:
        """Return pack IDs in the order they were registered."""
        seen: set[str] = set()
        order: list[str] = []
        for ev in self._events:
            if ev.kind == LoadEventKind.REGISTER and ev.pack_id not in seen:
                seen.add(ev.pack_id)
                order.append(ev.pack_id)
        return order

    def load_time(self, pack_id: str) -> float:
        """Return total loading time (ms) for *pack_id* across all events."""
        return sum(
            ev.duration_ms for ev in self._events if ev.pack_id == pack_id
        )

    def failures(self) -> list[_LoadEvent]:
        """Return all failure events."""
        return [ev for ev in self._events if ev.kind == LoadEventKind.FAILURE]

    def retries(self) -> list[_LoadEvent]:
        """Return all retry events."""
        return [ev for ev in self._events if ev.kind == LoadEventKind.RETRY]

    def events_for(self, pack_id: str) -> list[_LoadEvent]:
        """Return all events for a specific pack."""
        return [ev for ev in self._events if ev.pack_id == pack_id]

    def copilot_loading_summary(self) -> str:
        """Format the loading history as a human-readable summary.

        Designed for copilot diagnostics: includes total load time, number
        of packs, failures, and retries.
        """
        total_packs = len(set(ev.pack_id for ev in self._events))
        total_time = sum(ev.duration_ms for ev in self._events)
        fail_count = len(self.failures())
        retry_count = len(self.retries())
        lines = [
            f"Pack Loading Summary (copilot diagnostics)",
            f"  Packs touched : {total_packs}",
            f"  Total time    : {total_time:.1f} ms",
            f"  Failures      : {fail_count}",
            f"  Retries       : {retry_count}",
            f"  Load order    : {' → '.join(self.loading_order()) or '(none)'}",
        ]
        if fail_count:
            lines.append("  Failed packs:")
            for ev in self.failures():
                lines.append(f"    - {ev.pack_name}: {ev.detail}")
        return "\n".join(lines)

    def __len__(self) -> int:
        return len(self._events)


# ---------------------------------------------------------------------------
# 10. PackSerializer
# ---------------------------------------------------------------------------

class PackSerializer:
    """JSON serialization helpers for pack-related data structures.

    Provides round-trip serialization for descriptors, registries, dependency
    graphs, and loading histories.  All output is deterministic (sorted keys)
    to support reproducible builds and copilot caching.
    """

    @staticmethod
    def serialize_descriptor(desc: PackDescriptor) -> str:
        """Serialize a single descriptor to a JSON string."""
        return json.dumps(desc.to_dict(), indent=2, sort_keys=True)

    @staticmethod
    def deserialize_descriptor(data: str) -> PackDescriptor:
        """Deserialize a JSON string to a ``PackDescriptor``."""
        return PackDescriptor.from_dict(json.loads(data))

    @staticmethod
    def serialize_registry(registry: PackRegistry) -> str:
        """Serialize all packs in *registry* to a JSON string."""
        entries: list[dict[str, Any]] = []
        for desc in registry.list_packs():
            entry = desc.to_dict()
            status = registry.get_status(desc.pack_id)
            entry["_status"] = status.value if status else "unknown"
            entries.append(entry)
        return json.dumps(entries, indent=2, sort_keys=True)

    @staticmethod
    def deserialize_registry(data: str) -> list[tuple[PackDescriptor, PackStatus]]:
        """Deserialize a JSON string into descriptor/status pairs."""
        raw_list = json.loads(data)
        results: list[tuple[PackDescriptor, PackStatus]] = []
        for entry in raw_list:
            status_str = entry.pop("_status", "registered")
            desc = PackDescriptor.from_dict(entry)
            try:
                status = PackStatus(status_str)
            except ValueError:
                status = PackStatus.REGISTERED
            results.append((desc, status))
        return results

    @staticmethod
    def serialize_dependency_graph(graph: Mapping[str, Sequence[str]]) -> str:
        """Serialize a dependency graph to a JSON string."""
        serializable = {k: list(v) for k, v in graph.items()}
        return json.dumps(serializable, indent=2, sort_keys=True)

    @staticmethod
    def deserialize_dependency_graph(data: str) -> dict[str, list[str]]:
        """Deserialize a dependency graph from a JSON string."""
        raw = json.loads(data)
        return {str(k): list(v) for k, v in raw.items()}

    @staticmethod
    def serialize_history(history: PackLoadingHistory) -> str:
        """Serialize a loading history to a JSON string."""
        events: list[dict[str, Any]] = []
        for ev in history._events:  # noqa: WPS437 — access internal for serialization
            events.append({
                "timestamp": ev.timestamp,
                "pack_id": ev.pack_id,
                "pack_name": ev.pack_name,
                "kind": ev.kind.value,
                "duration_ms": ev.duration_ms,
                "detail": ev.detail,
            })
        return json.dumps(events, indent=2, sort_keys=True)

    @staticmethod
    def deserialize_history(data: str) -> PackLoadingHistory:
        """Deserialize a JSON string into a ``PackLoadingHistory``."""
        history = PackLoadingHistory()
        for raw in json.loads(data):
            history.record(
                pack_id=raw["pack_id"],
                pack_name=raw["pack_name"],
                kind=LoadEventKind(raw["kind"]),
                duration_ms=raw.get("duration_ms", 0.0),
                detail=raw.get("detail", ""),
            )
        return history


# ---------------------------------------------------------------------------
# 11. PackLoader (orchestrator)
# ---------------------------------------------------------------------------

class PackLoader:
    """High-level orchestrator for the pack loading pipeline.

    ``PackLoader`` coordinates the discoverer, dependency resolver, validator,
    registry, lifecycle manager, and history to implement the full load
    sequence described in §252 of theory2.tex.

    Typical usage::

        loader = PackLoader(registry=PackRegistry())
        loader.discover(Path("packs/"))
        loader.load_all()
    """

    def __init__(
        self,
        registry: PackRegistry,
        *,
        discoverer: PackDiscoverer | None = None,
        resolver: PackDependencyResolver | None = None,
        validator: PackValidator | None = None,
        history: PackLoadingHistory | None = None,
        jurisdiction_ceiling: TrustTier | None = None,
        available_bridges: Sequence[str] = (),
        allowed_coordinates: Sequence[str] = (),
    ) -> None:
        self._registry = registry
        self._discoverer = discoverer or PackDiscoverer()
        self._resolver = resolver or PackDependencyResolver()
        self._validator = validator or PackValidator(jurisdiction_ceiling=jurisdiction_ceiling)
        self._history = history or PackLoadingHistory()
        self._available_bridges = list(available_bridges)
        self._allowed_coordinates = list(allowed_coordinates)
        self._lifecycles: dict[str, PackLifecycle] = {}
        self._discovered: list[PackDescriptor] = []
        self._resolved: list[PackDescriptor] = []
        self._configurations: dict[str, PackConfiguration] = {}

    @property
    def registry(self) -> PackRegistry:
        """The pack registry managed by this loader."""
        return self._registry

    @property
    def history(self) -> PackLoadingHistory:
        """The loading history accumulated by this loader."""
        return self._history

    # -- discovery ----------------------------------------------------------

    def discover(self, directory: Path | None = None, *, entry_point_group: str = "jugeo.packs") -> list[PackDescriptor]:
        """Discover packs from the file system and entry-points.

        Results are merged, deduplicated, and stored for subsequent
        ``load_all`` calls.
        """
        if directory is not None:
            self._discoverer.scan_directory(directory)
        self._discoverer.scan_entry_points(group=entry_point_group)
        merged = self._discoverer.merge_sources()
        compatible = self._discoverer.filter_compatible(merged)
        self._discovered = self._discoverer.deduplicate(compatible)
        for desc in self._discovered:
            self._history.record(
                desc.pack_id, desc.name, LoadEventKind.DISCOVER, detail="discovered"
            )
        logger.info("Discovered %d pack(s)", len(self._discovered))
        return list(self._discovered)

    # -- single-pack load ---------------------------------------------------

    def load(self, descriptor: PackDescriptor, *, max_retries: int = 2) -> bool:
        """Load a single pack through validation, registration, and activation.

        Returns ``True`` on success.
        """
        start = time.monotonic()
        for attempt in range(1, max_retries + 1):
            ok = self._try_load_single(descriptor)
            elapsed = (time.monotonic() - start) * 1000
            if ok:
                self._history.record(
                    descriptor.pack_id, descriptor.name, LoadEventKind.REGISTER,
                    duration_ms=elapsed,
                )
                return True
            if attempt < max_retries:
                self._history.record(
                    descriptor.pack_id, descriptor.name, LoadEventKind.RETRY,
                    duration_ms=elapsed, detail=f"attempt {attempt}",
                )
                logger.info(
                    "Retrying load for pack %s (attempt %d/%d)",
                    descriptor.name, attempt + 1, max_retries,
                )
        elapsed = (time.monotonic() - start) * 1000
        self._history.record(
            descriptor.pack_id, descriptor.name, LoadEventKind.FAILURE,
            duration_ms=elapsed,
            detail=f"failed after {max_retries} attempts",
        )
        return False

    # -- bulk load ----------------------------------------------------------

    def load_all(self, *, max_retries: int = 2) -> list[PackDescriptor]:
        """Resolve dependencies and load all discovered packs in order.

        Returns the list of packs that were successfully loaded.
        """
        if not self._discovered:
            logger.warning("No packs discovered — nothing to load")
            return []
        self._resolved = self.resolve_dependencies(self._discovered)
        loaded: list[PackDescriptor] = []
        for desc in self._resolved:
            if self.load(desc, max_retries=max_retries):
                loaded.append(desc)
        logger.info(
            "Loaded %d/%d discovered pack(s)", len(loaded), len(self._discovered)
        )
        return loaded

    # -- dependency resolution ----------------------------------------------

    def resolve_dependencies(self, descriptors: Sequence[PackDescriptor]) -> list[PackDescriptor]:
        """Resolve *descriptors* into a dependency-safe load order.

        Records a ``RESOLVE`` event and delegates to
        ``PackDependencyResolver``.
        """
        start = time.monotonic()
        resolved = self._resolver.resolve(descriptors)
        elapsed = (time.monotonic() - start) * 1000
        for desc in resolved:
            self._history.record(
                desc.pack_id, desc.name, LoadEventKind.RESOLVE, duration_ms=elapsed
            )
        return resolved

    # -- validation ---------------------------------------------------------

    def validate(self, descriptor: PackDescriptor) -> bool:
        """Run full validation on *descriptor*.

        Records a ``VALIDATE`` event.
        """
        start = time.monotonic()
        ok = self._validator.full_validation(
            descriptor,
            available_bridges=self._available_bridges,
            allowed_coordinates=self._allowed_coordinates,
        )
        elapsed = (time.monotonic() - start) * 1000
        kind = LoadEventKind.VALIDATE if ok else LoadEventKind.FAILURE
        self._history.record(
            descriptor.pack_id, descriptor.name, kind, duration_ms=elapsed,
            detail="" if ok else "; ".join(self._validator.errors),
        )
        return ok

    # -- kernel registration ------------------------------------------------

    def register_with_kernel(self, descriptor: PackDescriptor) -> bool:
        """Register *descriptor* with the pack registry and service kernel.

        This makes the pack's capabilities visible to the evidence channels
        and to copilot routing heuristics.
        """
        self._registry.register(descriptor, status=PackStatus.REGISTERED)
        config = PackConfiguration(pack_id=descriptor.pack_id)
        self._configurations[descriptor.pack_id] = config
        lifecycle = PackLifecycle(descriptor, self._registry)
        self._lifecycles[descriptor.pack_id] = lifecycle
        return True

    # -- unload / reload ----------------------------------------------------

    def unload(self, pack_id: str) -> bool:
        """Unload a pack: deactivate, dispose, and unregister.

        Returns ``True`` if the pack was found and unloaded.
        """
        lifecycle = self._lifecycles.pop(pack_id, None)
        if lifecycle is not None:
            lifecycle.dispose()
        removed = self._registry.unregister(pack_id)
        self._configurations.pop(pack_id, None)
        if removed is not None:
            self._history.record(
                pack_id, removed.name, LoadEventKind.UNLOAD,
            )
            return True
        return False

    def reload(self, pack_id: str) -> bool:
        """Reload a pack by unloading and re-loading its descriptor.

        The descriptor must still be available in the discovered set.
        """
        desc = next(
            (d for d in self._discovered if d.pack_id == pack_id), None
        )
        if desc is None:
            logger.error("Cannot reload unknown pack %s", pack_id)
            return False
        self.unload(pack_id)
        return self.load(desc)

    def is_loaded(self, pack_id: str) -> bool:
        """Return ``True`` if *pack_id* is currently registered and active."""
        status = self._registry.get_status(pack_id)
        return status in (PackStatus.REGISTERED, PackStatus.ACTIVE, PackStatus.VALIDATED)

    # -- internal -----------------------------------------------------------

    def _try_load_single(self, descriptor: PackDescriptor) -> bool:
        """Attempt to validate, register, initialise, and activate a pack."""
        if not self.validate(descriptor):
            logger.warning(
                "Pack %s failed validation: %s",
                descriptor.name, self._validator.errors,
            )
            return False
        self.register_with_kernel(descriptor)
        lifecycle = self._lifecycles.get(descriptor.pack_id)
        if lifecycle is None:
            return False
        if not lifecycle.initialize():
            return False
        if not lifecycle.activate():
            return False
        self._history.record(
            descriptor.pack_id, descriptor.name, LoadEventKind.ACTIVATE,
        )
        return True


# ---------------------------------------------------------------------------
# Backward-compatible legacy helpers
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class PackLoadRequest:
    """Legacy load request preserved for backward compatibility.

    New code should use ``PackLoader`` directly.
    """

    key: str
    coordinate: str
    tier: TrustTier


@dataclass(frozen=True, slots=True)
class PackLoadResult:
    """Legacy load result preserved for backward compatibility."""

    loaded: bool
    descriptor: CatalogDescriptor | None
    reason: str


def load_pack(
    catalog: PackCatalog,
    authority: PackAuthority,
    request: PackLoadRequest,
) -> PackLoadResult:
    """Legacy entry-point for loading a single pack from a catalog.

    Validates the request against the catalog and authority, returning a
    ``PackLoadResult``.  Retained so that existing call-sites (including
    tests) continue to work without modification.
    """
    descriptor = catalog.get(request.key)
    if descriptor is None:
        return PackLoadResult(False, None, "unknown pack")
    if not authorize_pack(
        descriptor, authority, coordinate=request.coordinate, tier=request.tier
    ):
        return PackLoadResult(False, descriptor, "authority denied")
    return PackLoadResult(True, descriptor, "loaded")


# ---------------------------------------------------------------------------
# Module-private helpers
# ---------------------------------------------------------------------------

def _parse_version(version: str) -> tuple[int, ...]:
    """Parse a dotted version string into an integer tuple."""
    parts: list[int] = []
    for segment in version.split("."):
        try:
            parts.append(int(segment))
        except ValueError:
            parts.append(0)
    return tuple(parts)


def _trust_ordinal(tier: TrustTier) -> int:
    """Return an integer ordinal for *tier* suitable for comparison.

    The ``TrustTier`` enum is an ordered algebra (theory2.tex §237).  We
    derive a comparison ordinal from the member list so that the loading
    pipeline does not hard-code tier names.
    """
    members = list(type(tier))
    try:
        return members.index(tier)
    except ValueError:
        return -1


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    # Enumerations
    "PackStatus",
    "LoadEventKind",
    # Core classes
    "PackDescriptor",
    "PackLoader",
    "PackDiscoverer",
    "PackDependencyResolver",
    "PackValidator",
    "PackRegistry",
    "PackVersionManager",
    "PackConfiguration",
    "PackLifecycle",
    "PackLoadingHistory",
    "PackSerializer",
    # Legacy helpers
    "PackLoadRequest",
    "PackLoadResult",
    "load_pack",
    # Cross-subsystem enrichments
    "judgment_pack_loading",
    "encoding_pack",
]


# ---------------------------------------------------------------------------
# Cross-subsystem enrichment functions
# ---------------------------------------------------------------------------

try:
    from jugeo.judgments.sections import Section as _Section
except Exception:  # pragma: no cover
    _Section = None  # type: ignore[assignment,misc]

try:
    from jugeo.encodings import encode_judgment as _encode_judgment
except Exception:  # pragma: no cover
    _encode_judgment = None  # type: ignore[assignment,misc]

try:
    from jugeo.encodings import encoding_registry as _encoding_registry
except Exception:  # pragma: no cover
    _encoding_registry = None  # type: ignore[assignment,misc]


def judgment_pack_loading(
    descriptor: PackDescriptor | CatalogDescriptor,
    *,
    sections: Sequence[Any] | None = None,
    registry: Any | None = None,
) -> dict[str, Any]:
    """Load a pack enriched with judgment sections.

    Combines the standard pack loading pipeline with judgment section
    data from ``jugeo.judgments``.  Each section's coordinate is
    matched against the pack's jurisdiction to confirm coverage.

    Parameters
    ----------
    descriptor:
        The pack descriptor to load.
    sections:
        Judgment sections to associate with this pack.  When *None* an
        empty list is used.
    registry:
        An optional ``PackRegistry`` to register into.

    Returns
    -------
    dict[str, Any]
        ``{"pack_id": str, "name": str, "loaded_sections": int,
        "covered_sections": list[str], "uncovered_sections": list[str]}``.
    """
    secs = list(sections or [])
    pack_name = getattr(descriptor, "name", "")
    pack_id = getattr(descriptor, "pack_id", "")
    provides = set(getattr(descriptor, "provides", ()) or ())
    exported = set(getattr(descriptor, "exported_kinds", ()) or ())
    jurisdiction = provides | exported | {pack_name}

    covered: list[str] = []
    uncovered: list[str] = []
    for sec in secs:
        coord = getattr(sec, "coordinate", None)
        coord_str = ""
        if coord is not None:
            coord_str = (
                getattr(coord, "key", None)
                or getattr(coord, "name", None)
                or ".".join(getattr(coord, "components", ()) or ())
                or str(coord)
            )
        if any(j in coord_str for j in jurisdiction) or not jurisdiction - {pack_name}:
            covered.append(coord_str)
        else:
            uncovered.append(coord_str)

    if registry is not None and hasattr(registry, "register"):
        try:
            registry.register(descriptor)
        except Exception:
            pass

    return {
        "pack_id": str(pack_id),
        "name": str(pack_name),
        "loaded_sections": len(covered),
        "covered_sections": covered,
        "uncovered_sections": uncovered,
    }


def encoding_pack(
    descriptor: PackDescriptor | CatalogDescriptor,
    *,
    encoding_family: str = "",
) -> dict[str, Any]:
    """Load an encoding pack from ``jugeo.encodings``.

    Resolves the encoding family for the pack and returns metadata
    about the available encodings, including decidability
    classification and pipeline availability.

    Parameters
    ----------
    descriptor:
        The pack descriptor whose encodings to load.
    encoding_family:
        Explicit encoding family name (e.g. ``"scalar"``).  When empty
        the family is inferred from the pack's capabilities.

    Returns
    -------
    dict[str, Any]
        ``{"pack_name": str, "encoding_family": str,
        "registry_available": bool, "encoder_available": bool}``.
    """
    pack_name = getattr(descriptor, "name", "")
    family = encoding_family
    if not family:
        caps = getattr(descriptor, "capabilities", ()) or ()
        provides = getattr(descriptor, "provides", ()) or ()
        all_tags = list(caps) + list(provides)
        for tag in all_tags:
            if "encoding" in str(tag).lower():
                family = str(tag)
                break
        if not family:
            family = f"{pack_name}_encoding"

    reg_available = _encoding_registry is not None
    enc_available = _encode_judgment is not None

    return {
        "pack_name": str(pack_name),
        "encoding_family": family,
        "registry_available": reg_available,
        "encoder_available": enc_available,
    }


# copilot: shared-core marker for future LLM orchestration.
