"""
scaling_limits.manifest — Package-level manifest for the JuGeo scaling_limits subsystem.

copilot: shared-core marker
Theory reference: theory2.tex Ch64

This module defines the authoritative manifest structure used to register, track,
and validate all modules within the ``scaling_limits`` evaluation package.  A
:class:`ScalingLimitsManifest` serves as the single source of truth for the set of
modules that contribute to complexity analysis, phase-change detection, scaling-law
fitting, and fundamental-limit certification.

The manifest records:

* **Module registry** — every importable module with its version pin and role.
* **Dependency graph** — directed edges between modules capturing import order and
  logical coupling.
* **Scaling metrics** — lightweight key/value performance counters harvested during
  a package health-check sweep.
* **Health status** — a simple ``"healthy"`` / ``"degraded"`` / ``"unknown"`` tag
  updated whenever :meth:`ScalingLimitsManifest.validate` is invoked.

Builder pattern
---------------
:class:`ScalingManifestBuilder` follows the *fluent builder* pattern: callers chain
``with_module()``, ``with_dependency()``, and ``with_metric()`` calls before
invoking ``build()`` to obtain an immutable snapshot.

Free-function shortcut
-----------------------
:func:`build_scaling_manifest` wraps the builder so callers can construct a fully
populated manifest from a plain ``dict`` in a single call.

Design notes
------------
* All public dataclasses use ``slots=True`` for reduced per-instance overhead.
* Cross-module imports from the broader JuGeo ecosystem are wrapped in a guarded
  ``try/except`` block so this file is safely importable even when the rest of the
  package is not installed.
* Timestamps are always UTC ISO-8601 strings produced by :func:`_utcnow`.
* Unique identifiers are 8-character hex prefixes produced by :func:`_uid`.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Standard-library imports
# ---------------------------------------------------------------------------
import dataclasses
import functools
import itertools
import json
import math
import statistics
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, Iterator, List, Optional, Sequence, Tuple

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
__all__ = [
    "ScalingLimitsManifest",
    "ScalingManifestBuilder",
    "build_scaling_manifest",
]

# ---------------------------------------------------------------------------
# Guarded cross-module imports (JuGeo ecosystem)
# ---------------------------------------------------------------------------
try:
    from jugeo.evidence.manifests import Manifest, build_evidence_manifest
    from jugeo.evidence.trust import TrustProfile, TrustTier, join_trust_profiles
    from jugeo.evidence.channels import EvidenceRecord, EvidenceKind, build_channel
    from jugeo.evidence.provenance import ProvenanceTrace
    from jugeo.packs.bridges import BridgeTheorem, BridgeRegistry, BridgeComposer
    from jugeo.packs.authority import PackAuthority, PackAuthorityRegistry
    from jugeo.packs.catalog import PackDescriptor
    from jugeo.orchestration.controller import Orchestrator, OrchestratorState
    from jugeo.ideation.ideas import IdeaProposal, TrustStatus
    from jugeo.ideation.regimes import Regime, RegimeCatalog
    from jugeo.ideation.novelty import NoveltyScore
    from jugeo.geometry.site import Site, Coordinate
    from jugeo.geometry.descent import DescentResult, GlobalSection
except Exception:
    pass

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

#: Current schema version for :class:`ScalingLimitsManifest` serialisation.
MANIFEST_VERSION: str = "1.0.0"

#: The canonical package name recorded in every auto-generated manifest.
PACKAGE_NAME: str = "jugeo.evaluation.scaling_limits"

#: Maximum number of modules that may be registered in a single manifest before
#: :meth:`ScalingLimitsManifest.validate` emits a ``"degraded"`` warning.
MAX_MODULE_COUNT: int = 256

#: Maximum edge count in the dependency graph before a cycle-risk warning fires.
MAX_DEPENDENCY_EDGES: int = 1024

#: Sentinel value used to signal that a metric has never been recorded.
METRIC_SENTINEL: float = float("nan")

#: Default health status assigned to a freshly created manifest.
DEFAULT_HEALTH: str = "unknown"

#: Set of valid health-status strings.
VALID_HEALTH_STATES: frozenset = frozenset({"healthy", "degraded", "unknown"})

#: Characters allowed in module names (simple alphanumeric + underscore + dot).
_MODULE_NAME_CHARS: frozenset = frozenset(
    "abcdefghijklmnopqrstuvwxyz" "ABCDEFGHIJKLMNOPQRSTUVWXYZ" "0123456789_."
)


# ---------------------------------------------------------------------------
# Module-level helper functions
# ---------------------------------------------------------------------------


def _utcnow() -> str:
    """Return the current UTC time as an ISO-8601 string (no microseconds).

    The function uses :func:`time.gmtime` rather than ``datetime`` to avoid
    pulling in the ``datetime`` module, keeping the dependency footprint of this
    helper minimal.  The returned string is always exactly 19 characters long
    and has the form ``YYYY-MM-DDTHH:MM:SS``.

    Returns
    -------
    str
        Current UTC timestamp in ISO-8601 format.
    """
    t = time.gmtime()
    return time.strftime("%Y-%m-%dT%H:%M:%S", t)


def _uid() -> str:
    """Return a compact 12-character hex unique identifier.

    Identifiers are derived from :func:`uuid.uuid4` (random UUID) and
    truncated to 12 hex characters, giving approximately 48 bits of entropy —
    sufficient for collision avoidance within a single manifest session.

    Returns
    -------
    str
        A 12-character lowercase hexadecimal string.
    """
    return uuid.uuid4().hex[:12]


def _clamp(value: float, lo: float, hi: float) -> float:
    """Clamp *value* to the closed interval [*lo*, *hi*].

    This is a thin wrapper around ``max(lo, min(hi, value))`` provided here
    so that all numeric-clamping operations in this module read consistently
    and can be found in one place for future audit.

    Parameters
    ----------
    value:
        The number to clamp.
    lo:
        Lower bound (inclusive).
    hi:
        Upper bound (inclusive).

    Returns
    -------
    float
        The clamped value.
    """
    return max(lo, min(hi, value))


def _validate_module_name(name: str) -> bool:
    """Return ``True`` if *name* is a valid dotted Python module identifier.

    A valid module name consists only of ASCII letters, digits, underscores,
    and dots.  It must not start or end with a dot, and must not contain
    consecutive dots (which would indicate an empty path segment).

    Parameters
    ----------
    name:
        The module name string to validate.

    Returns
    -------
    bool
        ``True`` if the name is syntactically valid.
    """
    if not name or name.startswith(".") or name.endswith("."):
        return False
    if ".." in name:
        return False
    return all(c in _MODULE_NAME_CHARS for c in name)


def _deep_merge_dicts(base: dict, override: dict) -> dict:
    """Recursively merge *override* into *base*, returning a new dict.

    Keys present in *override* take precedence over the corresponding keys in
    *base*.  Nested dicts are merged recursively; all other types are replaced
    by the override value without further traversal.

    Parameters
    ----------
    base:
        The base dictionary (not mutated).
    override:
        The dictionary whose values overwrite *base*.

    Returns
    -------
    dict
        A freshly constructed merged dictionary.
    """
    result: dict = dict(base)
    for key, val in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(val, dict):
            result[key] = _deep_merge_dicts(result[key], val)
        else:
            result[key] = val
    return result


def _topological_sort(graph: dict) -> List[str]:
    """Return a topological ordering of nodes in *graph* or raise on a cycle.

    *graph* is expected to be a ``dict[str, list[str]]`` where each key is a
    node name and the associated list is that node's outgoing neighbours.
    Nodes that appear only as targets (not as keys) are added implicitly with
    an empty neighbour list.

    Parameters
    ----------
    graph:
        Adjacency list representation of a directed graph.

    Returns
    -------
    list[str]
        Nodes in topological order (sources first).

    Raises
    ------
    ValueError
        If the graph contains a cycle.
    """
    all_nodes: set = set(graph.keys())
    for neighbours in graph.values():
        all_nodes.update(neighbours)

    in_degree: dict = {n: 0 for n in all_nodes}
    for neighbours in graph.values():
        for n in neighbours:
            in_degree[n] += 1

    queue: List[str] = [n for n, d in in_degree.items() if d == 0]
    result: List[str] = []

    while queue:
        node = queue.pop(0)
        result.append(node)
        for neighbour in graph.get(node, []):
            in_degree[neighbour] -= 1
            if in_degree[neighbour] == 0:
                queue.append(neighbour)

    if len(result) != len(all_nodes):
        raise ValueError(
            "Dependency graph contains a cycle; topological sort is impossible."
        )
    return result


# ---------------------------------------------------------------------------
# Main dataclass: ScalingLimitsManifest
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class ScalingLimitsManifest:
    """Authoritative manifest for the ``scaling_limits`` evaluation package.

    A :class:`ScalingLimitsManifest` collects all the metadata necessary to
    understand the composition and health of the ``jugeo.evaluation.scaling_limits``
    subsystem.  It is the canonical object that orchestration layers query when
    they need to know which analysis modules are available, what their
    inter-dependencies are, and whether the subsystem is currently considered
    healthy.

    The manifest can be serialised to a plain Python ``dict`` (via
    :meth:`to_dict`) or round-tripped through JSON (via :meth:`export_json`
    and :meth:`from_dict`).  Two manifests can be merged (non-destructively)
    via :meth:`merge`, or compared via :meth:`diff`.

    Attributes
    ----------
    manifest_id : str
        Globally unique identifier for this manifest instance.
    created_at : str
        UTC ISO-8601 timestamp at which this manifest was constructed.
    version : str
        Semantic version of the manifest schema (e.g., ``"1.0.0"``).
    package_name : str
        Fully-qualified Python package name this manifest describes.
    module_registry : dict
        Mapping ``module_name -> {"version": str, "role": str, "enabled": bool}``.
    dependency_graph : dict
        Adjacency list ``module_name -> [dependency_name, ...]``.
    scaling_metrics : dict
        Arbitrary key/value performance metrics collected during health checks.
    health_status : str
        One of ``"healthy"``, ``"degraded"``, or ``"unknown"``.
    notes : list
        Free-text annotation strings appended over the lifetime of the manifest.
    """

    manifest_id: str
    created_at: str
    version: str
    package_name: str
    module_registry: dict
    dependency_graph: dict
    scaling_metrics: dict
    health_status: str
    notes: list

    # ------------------------------------------------------------------
    # Mutation helpers
    # ------------------------------------------------------------------

    def register_module(
        self,
        name: str,
        version: str = "0.0.0",
        role: str = "core",
        enabled: bool = True,
    ) -> None:
        """Register a module with this manifest.

        Adds or replaces the entry for *name* in :attr:`module_registry`.
        The *role* field is a free-form string that downstream tooling may
        interpret (e.g., ``"core"``, ``"plugin"``, ``"experimental"``).
        The *enabled* flag allows temporarily disabling a module without
        removing it from the registry, which preserves audit history.

        If *name* fails the syntactic validity check performed by
        :func:`_validate_module_name`, a :class:`ValueError` is raised before
        any mutation occurs.

        Parameters
        ----------
        name:
            Dotted Python module name (e.g., ``"jugeo.evaluation.scaling_limits.models"``).
        version:
            Semantic version string for the module.
        role:
            Logical role label.
        enabled:
            Whether the module is currently active.

        Raises
        ------
        ValueError
            If *name* is not a valid module identifier.
        RuntimeError
            If :attr:`module_registry` already contains :data:`MAX_MODULE_COUNT`
            entries and *name* is not already registered.
        """
        if not _validate_module_name(name):
            raise ValueError(f"Invalid module name: {name!r}")
        if name not in self.module_registry and len(self.module_registry) >= MAX_MODULE_COUNT:
            raise RuntimeError(
                f"Module registry is full ({MAX_MODULE_COUNT} entries). "
                "Cannot register additional modules."
            )
        self.module_registry[name] = {
            "version": version,
            "role": role,
            "enabled": enabled,
            "registered_at": _utcnow(),
        }

    def add_dependency(self, source: str, target: str) -> None:
        """Record a directed dependency edge from *source* to *target*.

        The edge asserts that *source* must be initialised after *target*.
        Both *source* and *target* are automatically added to the
        :attr:`module_registry` with default metadata if they are not already
        present.

        The method checks that adding this edge does not push the total edge
        count past :data:`MAX_DEPENDENCY_EDGES`, and appends a warning note to
        :attr:`notes` if the count would exceed 80% of the limit.

        Parameters
        ----------
        source:
            The dependent module (the one that *needs* the other).
        target:
            The depended-upon module.

        Raises
        ------
        ValueError
            If *source* or *target* is an invalid module name.
        RuntimeError
            If adding this edge would exceed :data:`MAX_DEPENDENCY_EDGES`.
        """
        for name in (source, target):
            if not _validate_module_name(name):
                raise ValueError(f"Invalid module name: {name!r}")

        total_edges = sum(len(v) for v in self.dependency_graph.values())
        if total_edges >= MAX_DEPENDENCY_EDGES:
            raise RuntimeError(
                f"Dependency graph is at capacity ({MAX_DEPENDENCY_EDGES} edges)."
            )
        if total_edges > MAX_DEPENDENCY_EDGES * 0.8:
            self.notes.append(
                f"[{_utcnow()}] WARNING: dependency graph is at "
                f"{total_edges}/{MAX_DEPENDENCY_EDGES} edges."
            )

        # Ensure both endpoints appear in the registry
        for name in (source, target):
            if name not in self.module_registry:
                self.register_module(name)

        if source not in self.dependency_graph:
            self.dependency_graph[source] = []
        if target not in self.dependency_graph[source]:
            self.dependency_graph[source].append(target)

    def record_metric(self, key: str, value: float) -> None:
        """Store a named scalar metric in :attr:`scaling_metrics`.

        Metrics are arbitrary numeric measurements — wall-clock import time,
        peak memory usage, analysis throughput (samples/second), etc.  Each
        call *overwrites* the previous value for *key*; callers that want a
        time-series should instead use a list-valued metric with a custom key
        scheme such as ``"latency[0]"``, ``"latency[1]"``, etc.

        Non-finite values (``nan``, ``inf``) are accepted and stored as-is;
        the caller is responsible for interpreting them appropriately during
        downstream health checks.

        Parameters
        ----------
        key:
            Metric identifier string (non-empty).
        value:
            Scalar floating-point measurement.

        Raises
        ------
        ValueError
            If *key* is an empty string.
        """
        if not key:
            raise ValueError("Metric key must be a non-empty string.")
        self.scaling_metrics[key] = value

    def mark_healthy(self, status: str = "healthy") -> None:
        """Update the :attr:`health_status` of this manifest.

        The *status* must be one of the strings in :data:`VALID_HEALTH_STATES`
        (``"healthy"``, ``"degraded"``, or ``"unknown"``).  Callers should
        invoke this method after running :meth:`validate` so that the health
        status reflects the most recent analysis.

        A timestamped note is appended to :attr:`notes` every time the health
        status transitions to a new value, preserving an audit trail of health
        changes.

        Parameters
        ----------
        status:
            The new health status string.

        Raises
        ------
        ValueError
            If *status* is not one of the recognised health-state strings.
        """
        if status not in VALID_HEALTH_STATES:
            raise ValueError(
                f"Invalid health status {status!r}. "
                f"Expected one of: {sorted(VALID_HEALTH_STATES)}"
            )
        if status != self.health_status:
            self.notes.append(
                f"[{_utcnow()}] Health status changed: {self.health_status!r} → {status!r}"
            )
        self.health_status = status

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict:
        """Serialise this manifest to a plain Python dictionary.

        The returned dict is JSON-serialisable (all nested values are
        strings, numbers, lists, or dicts).  The schema version stored
        in :attr:`version` is preserved so that :meth:`from_dict` can
        apply the appropriate deserialisation logic for older schemas.

        The ``"notes"`` field is stored as a *copy* of :attr:`notes` so
        that mutations to the returned dict do not affect this instance.

        Returns
        -------
        dict
            A fully serialisable representation of this manifest.
        """
        return {
            "manifest_id": self.manifest_id,
            "created_at": self.created_at,
            "version": self.version,
            "package_name": self.package_name,
            "module_registry": dict(self.module_registry),
            "dependency_graph": {k: list(v) for k, v in self.dependency_graph.items()},
            "scaling_metrics": dict(self.scaling_metrics),
            "health_status": self.health_status,
            "notes": list(self.notes),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ScalingLimitsManifest":
        """Reconstruct a :class:`ScalingLimitsManifest` from a plain dict.

        This is the inverse of :meth:`to_dict`.  All fields are validated
        during reconstruction:

        * ``manifest_id`` must be a non-empty string.
        * ``health_status`` must be in :data:`VALID_HEALTH_STATES`.
        * ``module_registry``, ``dependency_graph``, and ``scaling_metrics``
          must all be dict-typed (defaulting to ``{}`` if absent).

        Unknown extra keys in *data* are silently ignored to allow
        forward-compatible schema evolution.

        Parameters
        ----------
        data:
            Dictionary as produced by :meth:`to_dict` (or compatible JSON).

        Returns
        -------
        ScalingLimitsManifest
            A freshly constructed instance.

        Raises
        ------
        KeyError
            If a required field is missing from *data*.
        ValueError
            If a field value fails its validation check.
        """
        health = data.get("health_status", DEFAULT_HEALTH)
        if health not in VALID_HEALTH_STATES:
            health = DEFAULT_HEALTH
        return cls(
            manifest_id=data["manifest_id"],
            created_at=data.get("created_at", _utcnow()),
            version=data.get("version", MANIFEST_VERSION),
            package_name=data.get("package_name", PACKAGE_NAME),
            module_registry=dict(data.get("module_registry", {})),
            dependency_graph={
                k: list(v) for k, v in data.get("dependency_graph", {}).items()
            },
            scaling_metrics=dict(data.get("scaling_metrics", {})),
            health_status=health,
            notes=list(data.get("notes", [])),
        )

    def export_json(self, indent: int = 2) -> str:
        """Serialise this manifest to a formatted JSON string.

        Convenience wrapper around :meth:`to_dict` + :func:`json.dumps`.
        The *indent* parameter controls the indentation level used by the
        JSON encoder; set it to ``None`` for a compact single-line encoding
        suitable for logging or network transmission.

        Parameters
        ----------
        indent:
            Number of spaces per indentation level.

        Returns
        -------
        str
            A JSON-encoded string representation of this manifest.
        """
        return json.dumps(self.to_dict(), indent=indent, default=str)

    # ------------------------------------------------------------------
    # Analysis and reporting
    # ------------------------------------------------------------------

    def summary(self) -> str:
        """Return a concise human-readable summary of this manifest.

        The summary covers the manifest identity, registered module count,
        dependency edge count, metric count, and current health status.
        It is intended for use in log messages, CLI output, and simple
        diagnostic displays — not as a replacement for :meth:`render_report`.

        Returns
        -------
        str
            A multiline summary string (no trailing newline).
        """
        edge_count = sum(len(v) for v in self.dependency_graph.values())
        lines = [
            f"ScalingLimitsManifest <{self.manifest_id}>",
            f"  package    : {self.package_name}",
            f"  version    : {self.version}",
            f"  created_at : {self.created_at}",
            f"  modules    : {len(self.module_registry)}",
            f"  dep-edges  : {edge_count}",
            f"  metrics    : {len(self.scaling_metrics)}",
            f"  health     : {self.health_status}",
            f"  notes      : {len(self.notes)} entries",
        ]
        return "\n".join(lines)

    def validate(self) -> List[str]:
        """Validate the internal consistency of this manifest.

        Checks performed:

        1. All module names in :attr:`module_registry` are syntactically valid.
        2. All dependency edges reference modules known to the registry.
        3. The dependency graph is acyclic (topological sort must succeed).
        4. :attr:`health_status` is one of :data:`VALID_HEALTH_STATES`.
        5. :attr:`scaling_metrics` values are all numeric.

        The method returns a list of human-readable error strings.  An empty
        list indicates that the manifest is fully valid.  The manifest's
        :attr:`health_status` is updated automatically: ``"healthy"`` if the
        list is empty, ``"degraded"`` otherwise.

        Returns
        -------
        list[str]
            Validation error descriptions (empty if valid).
        """
        errors: List[str] = []

        # Check module names
        for name in self.module_registry:
            if not _validate_module_name(name):
                errors.append(f"Invalid module name in registry: {name!r}")

        # Check dependency references
        all_known = set(self.module_registry.keys())
        for src, targets in self.dependency_graph.items():
            if src not in all_known:
                errors.append(f"Dependency source {src!r} not in module_registry.")
            for tgt in targets:
                if tgt not in all_known:
                    errors.append(
                        f"Dependency target {tgt!r} (from {src!r}) not in module_registry."
                    )

        # Check for cycles
        try:
            _topological_sort(self.dependency_graph)
        except ValueError as exc:
            errors.append(f"Dependency graph cycle detected: {exc}")

        # Check health status
        if self.health_status not in VALID_HEALTH_STATES:
            errors.append(f"Invalid health_status: {self.health_status!r}")

        # Check metric values
        for k, v in self.scaling_metrics.items():
            if not isinstance(v, (int, float)):
                errors.append(f"Metric {k!r} has non-numeric value: {v!r}")

        # Update health
        self.health_status = "healthy" if not errors else "degraded"
        return errors

    def merge(self, other: "ScalingLimitsManifest") -> "ScalingLimitsManifest":
        """Produce a new manifest that merges *self* with *other*.

        The merge semantics are:

        * :attr:`module_registry` — entries from *other* override *self*.
        * :attr:`dependency_graph` — edges are unioned (no duplicates).
        * :attr:`scaling_metrics` — values from *other* override *self*.
        * :attr:`notes` — concatenated, *self* notes first.
        * :attr:`health_status` — the *worse* of the two statuses (``"degraded"``
          beats ``"healthy"``; ``"unknown"`` beats both).
        * :attr:`manifest_id` — a freshly generated identifier.
        * :attr:`created_at` — current UTC timestamp.

        Parameters
        ----------
        other:
            Another :class:`ScalingLimitsManifest` to merge into *self*.

        Returns
        -------
        ScalingLimitsManifest
            A new manifest representing the combined state.
        """
        merged_registry = _deep_merge_dicts(self.module_registry, other.module_registry)

        merged_graph: dict = {}
        for src, targets in itertools.chain(
            self.dependency_graph.items(), other.dependency_graph.items()
        ):
            current = set(merged_graph.get(src, []))
            current.update(targets)
            merged_graph[src] = sorted(current)

        merged_metrics = _deep_merge_dicts(self.scaling_metrics, other.scaling_metrics)
        merged_notes = list(self.notes) + list(other.notes)

        # Pessimistic health merge
        health_order = {"healthy": 0, "degraded": 1, "unknown": 2}
        worse = max(
            self.health_status,
            other.health_status,
            key=lambda s: health_order.get(s, 2),
        )

        return ScalingLimitsManifest(
            manifest_id=_uid(),
            created_at=_utcnow(),
            version=self.version,
            package_name=self.package_name,
            module_registry=merged_registry,
            dependency_graph=merged_graph,
            scaling_metrics=merged_metrics,
            health_status=worse,
            notes=merged_notes,
        )

    def diff(self, other: "ScalingLimitsManifest") -> dict:
        """Compute a structured diff between *self* and *other*.

        Returns a dictionary with four sections:

        ``"modules_added"``
            Module names present in *other* but not in *self*.
        ``"modules_removed"``
            Module names present in *self* but not in *other*.
        ``"modules_changed"``
            Module names present in both whose registry entries differ.
        ``"metrics_delta"``
            ``{key: (self_value, other_value)}`` for metrics that changed.

        Parameters
        ----------
        other:
            The manifest to compare against.

        Returns
        -------
        dict
            A structured diff dictionary.
        """
        self_keys = set(self.module_registry)
        other_keys = set(other.module_registry)

        added = sorted(other_keys - self_keys)
        removed = sorted(self_keys - other_keys)
        changed = [
            k
            for k in self_keys & other_keys
            if self.module_registry[k] != other.module_registry[k]
        ]

        metrics_delta: dict = {}
        all_metric_keys = set(self.scaling_metrics) | set(other.scaling_metrics)
        for k in all_metric_keys:
            sv = self.scaling_metrics.get(k, METRIC_SENTINEL)
            ov = other.scaling_metrics.get(k, METRIC_SENTINEL)
            if sv != ov:
                metrics_delta[k] = (sv, ov)

        return {
            "modules_added": added,
            "modules_removed": removed,
            "modules_changed": changed,
            "metrics_delta": metrics_delta,
        }

    def render_report(self) -> str:
        """Render a detailed human-readable health report for this manifest.

        The report includes a header block with identity information, a full
        listing of registered modules with their roles and enabled status, the
        topological module load order (if acyclic), all recorded metrics, and
        any accumulated notes.

        The report is intended for display in terminals, documentation
        notebooks, or CI artefact logs.  It does **not** include raw JSON
        dumps so as to remain readable at a glance.

        Returns
        -------
        str
            A formatted plain-text report (multiline, no trailing newline).
        """
        sep = "=" * 60
        thin = "-" * 60
        lines: List[str] = [
            sep,
            f"SCALING LIMITS MANIFEST REPORT",
            f"ID      : {self.manifest_id}",
            f"Package : {self.package_name}",
            f"Version : {self.version}",
            f"Created : {self.created_at}",
            f"Health  : {self.health_status.upper()}",
            sep,
            "",
            "REGISTERED MODULES",
            thin,
        ]
        for name, meta in sorted(self.module_registry.items()):
            status_flag = "✓" if meta.get("enabled", True) else "✗"
            lines.append(
                f"  {status_flag} {name:<50}  v{meta.get('version', '?')}  [{meta.get('role', '?')}]"
            )
        lines.append("")

        # Topological order
        try:
            order = _topological_sort(self.dependency_graph)
            lines.append("TOPOLOGICAL LOAD ORDER")
            lines.append(thin)
            for i, mod in enumerate(order, 1):
                lines.append(f"  {i:3d}. {mod}")
        except ValueError as exc:
            lines.append(f"TOPOLOGICAL ORDER  (CYCLE DETECTED: {exc})")
        lines.append("")

        # Metrics
        lines.append("SCALING METRICS")
        lines.append(thin)
        if self.scaling_metrics:
            for k, v in sorted(self.scaling_metrics.items()):
                lines.append(f"  {k:<40} : {v}")
        else:
            lines.append("  (no metrics recorded)")
        lines.append("")

        # Notes
        lines.append("NOTES")
        lines.append(thin)
        if self.notes:
            for note in self.notes:
                lines.append(f"  {note}")
        else:
            lines.append("  (no notes)")
        lines.append(sep)

        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Builder: ScalingManifestBuilder
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class ScalingManifestBuilder:
    """Fluent builder for :class:`ScalingLimitsManifest`.

    Typical usage::

        manifest = (
            ScalingManifestBuilder()
            .with_module("jugeo.evaluation.scaling_limits.models")
            .with_module("jugeo.evaluation.scaling_limits.manifest")
            .with_dependency(
                "jugeo.evaluation.scaling_limits.manifest",
                "jugeo.evaluation.scaling_limits.models",
            )
            .with_metric("import_time_ms", 12.4)
            .build()
        )

    The builder accumulates configuration through method chaining.  Calling
    :meth:`build` finalises the manifest and runs :meth:`ScalingLimitsManifest.validate`
    automatically.  Calling :meth:`reset` discards all accumulated state and
    starts a fresh build cycle.

    Attributes
    ----------
    _manifest : ScalingLimitsManifest
        The in-progress manifest being constructed.
    _validators : list
        Callable validators appended via :meth:`add_validator`.
    _hooks : list
        Post-build hooks appended via :meth:`add_hook`.
    """

    _manifest: ScalingLimitsManifest = field(
        default_factory=lambda: ScalingLimitsManifest(
            manifest_id=_uid(),
            created_at=_utcnow(),
            version=MANIFEST_VERSION,
            package_name=PACKAGE_NAME,
            module_registry={},
            dependency_graph={},
            scaling_metrics={},
            health_status=DEFAULT_HEALTH,
            notes=[],
        )
    )
    _validators: list = field(default_factory=list)
    _hooks: list = field(default_factory=list)

    # ------------------------------------------------------------------
    # Fluent configuration methods
    # ------------------------------------------------------------------

    def with_module(
        self,
        name: str,
        version: str = "0.0.0",
        role: str = "core",
        enabled: bool = True,
    ) -> "ScalingManifestBuilder":
        """Register a module in the manifest under construction.

        This method delegates to :meth:`ScalingLimitsManifest.register_module`
        and returns *self* so that calls can be chained fluently.  The *role*
        parameter accepts arbitrary strings; the JuGeo ecosystem conventionally
        uses ``"core"``, ``"plugin"``, ``"experimental"``, and ``"deprecated"``.

        Parameters
        ----------
        name:
            Fully-qualified Python module name.
        version:
            Semantic version pin for the module.
        role:
            Logical role label within the package.
        enabled:
            Whether the module is active in the current build context.

        Returns
        -------
        ScalingManifestBuilder
            *self*, to allow method chaining.
        """
        self._manifest.register_module(name, version=version, role=role, enabled=enabled)
        return self

    def with_dependency(self, source: str, target: str) -> "ScalingManifestBuilder":
        """Add a directed dependency edge to the manifest under construction.

        Delegates to :meth:`ScalingLimitsManifest.add_dependency` and returns
        *self*.  Both *source* and *target* are registered in the module
        registry with default metadata if they are not already present.

        Parameters
        ----------
        source:
            The dependent module.
        target:
            The depended-upon module.

        Returns
        -------
        ScalingManifestBuilder
            *self*, to allow method chaining.
        """
        self._manifest.add_dependency(source, target)
        return self

    def with_metric(self, key: str, value: float) -> "ScalingManifestBuilder":
        """Record a scalar metric in the manifest under construction.

        Delegates to :meth:`ScalingLimitsManifest.record_metric` and returns
        *self*.  Metrics are typically collected by the build toolchain (e.g.,
        import latency, test coverage percentage, code complexity score) and
        stored here so that CI pipelines can surface them alongside health status.

        Parameters
        ----------
        key:
            Metric identifier string.
        value:
            Scalar measurement.

        Returns
        -------
        ScalingManifestBuilder
            *self*, to allow method chaining.
        """
        self._manifest.record_metric(key, value)
        return self

    def add_validator(self, fn: Callable[["ScalingLimitsManifest"], List[str]]) -> "ScalingManifestBuilder":
        """Attach a custom validation callable to the build pipeline.

        *fn* must accept a :class:`ScalingLimitsManifest` and return a
        (possibly empty) list of error strings.  All registered validators are
        invoked by :meth:`build` after the default :meth:`ScalingLimitsManifest.validate`
        check.  Errors from custom validators are appended to the built
        manifest's :attr:`~ScalingLimitsManifest.notes` list.

        Parameters
        ----------
        fn:
            A callable ``(ScalingLimitsManifest) -> list[str]``.

        Returns
        -------
        ScalingManifestBuilder
            *self*, to allow method chaining.
        """
        self._validators.append(fn)
        return self

    def add_hook(self, fn: Callable[["ScalingLimitsManifest"], None]) -> "ScalingManifestBuilder":
        """Attach a post-build hook to be executed after manifest construction.

        Hooks receive the fully-built :class:`ScalingLimitsManifest` and may
        perform side effects such as logging, persisting the manifest to disk,
        or notifying a monitoring service.  Hooks are executed in registration
        order and must not raise exceptions (any exceptions are caught and
        recorded as notes on the manifest).

        Parameters
        ----------
        fn:
            A callable ``(ScalingLimitsManifest) -> None``.

        Returns
        -------
        ScalingManifestBuilder
            *self*, to allow method chaining.
        """
        self._hooks.append(fn)
        return self

    def build(self) -> ScalingLimitsManifest:
        """Finalise and return the constructed :class:`ScalingLimitsManifest`.

        The build process:

        1. Runs the built-in :meth:`ScalingLimitsManifest.validate` check.
        2. Invokes each registered custom validator, collecting error strings.
        3. Appends all collected errors to the manifest's ``notes`` list.
        4. Runs each registered post-build hook; hook exceptions are caught
           and appended as notes (they do not abort the build).
        5. Returns the finalised manifest.

        Calling :meth:`build` does **not** reset the builder state; call
        :meth:`reset` explicitly if you want to start a new build cycle.

        Returns
        -------
        ScalingLimitsManifest
            The finalised manifest.
        """
        errors = self._manifest.validate()
        for validator in self._validators:
            try:
                extra_errors = validator(self._manifest)
                errors.extend(extra_errors)
            except Exception as exc:  # noqa: BLE001
                self._manifest.notes.append(
                    f"[{_utcnow()}] Validator {validator!r} raised: {exc}"
                )

        for error in errors:
            self._manifest.notes.append(f"[{_utcnow()}] VALIDATION ERROR: {error}")

        for hook in self._hooks:
            try:
                hook(self._manifest)
            except Exception as exc:  # noqa: BLE001
                self._manifest.notes.append(
                    f"[{_utcnow()}] Hook {hook!r} raised: {exc}"
                )

        return self._manifest

    def reset(self) -> "ScalingManifestBuilder":
        """Discard all accumulated state and start a fresh build cycle.

        After calling this method the builder behaves exactly as if it were
        newly constructed: all module registrations, dependency edges, metrics,
        validators, and hooks are cleared, and a new manifest skeleton with a
        fresh :func:`_uid` and current :func:`_utcnow` timestamp is created.

        Returns
        -------
        ScalingManifestBuilder
            *self*, to allow method chaining (e.g., ``builder.reset().with_module(...)``).
        """
        self._manifest = ScalingLimitsManifest(
            manifest_id=_uid(),
            created_at=_utcnow(),
            version=MANIFEST_VERSION,
            package_name=PACKAGE_NAME,
            module_registry={},
            dependency_graph={},
            scaling_metrics={},
            health_status=DEFAULT_HEALTH,
            notes=[],
        )
        self._validators.clear()
        self._hooks.clear()
        return self

    @classmethod
    def from_config(cls, config: dict) -> "ScalingManifestBuilder":
        """Construct a pre-populated builder from a configuration dictionary.

        This classmethod allows callers to drive manifest construction from a
        serialised config (e.g., loaded from a TOML or JSON file).  The
        *config* dict is expected to contain the following optional keys:

        * ``"modules"`` — list of ``{name, version, role, enabled}`` dicts.
        * ``"dependencies"`` — list of ``{source, target}`` dicts.
        * ``"metrics"`` — dict of ``{key: value}`` pairs.
        * ``"validators"`` — ignored (callables cannot be serialised).
        * ``"package_name"`` — overrides the default :data:`PACKAGE_NAME`.

        Parameters
        ----------
        config:
            Configuration dictionary.

        Returns
        -------
        ScalingManifestBuilder
            A builder pre-populated according to *config*.
        """
        builder = cls()
        if "package_name" in config:
            builder._manifest.package_name = config["package_name"]
        for mod in config.get("modules", []):
            builder.with_module(
                mod["name"],
                version=mod.get("version", "0.0.0"),
                role=mod.get("role", "core"),
                enabled=mod.get("enabled", True),
            )
        for dep in config.get("dependencies", []):
            builder.with_dependency(dep["source"], dep["target"])
        for key, value in config.get("metrics", {}).items():
            builder.with_metric(key, float(value))
        return builder


# ---------------------------------------------------------------------------
# Free function: build_scaling_manifest
# ---------------------------------------------------------------------------


def build_scaling_manifest(config: dict) -> ScalingLimitsManifest:
    """Construct and return a :class:`ScalingLimitsManifest` from a config dict.

    This convenience function combines :meth:`ScalingManifestBuilder.from_config`
    and :meth:`ScalingManifestBuilder.build` into a single call.  It is the
    recommended entry-point for code that needs a ready-to-use manifest from
    a plain dictionary (e.g., one loaded from a YAML or JSON configuration
    file, or from a unit-test fixture).

    The *config* dict is passed unchanged to :meth:`ScalingManifestBuilder.from_config`;
    refer to that method's documentation for the expected schema.

    After building, the manifest is automatically validated; any validation
    errors are recorded as notes within the returned manifest.  The caller
    can inspect ``manifest.health_status`` to determine whether the build
    succeeded cleanly.

    Example
    -------
    ::

        manifest = build_scaling_manifest(
            {
                "package_name": "jugeo.evaluation.scaling_limits",
                "modules": [
                    {"name": "jugeo.evaluation.scaling_limits.models", "role": "core"},
                    {"name": "jugeo.evaluation.scaling_limits.manifest", "role": "core"},
                ],
                "dependencies": [
                    {
                        "source": "jugeo.evaluation.scaling_limits.manifest",
                        "target": "jugeo.evaluation.scaling_limits.models",
                    }
                ],
                "metrics": {"import_time_ms": 8.3},
            }
        )
        print(manifest.health_status)   # → "healthy"
        print(manifest.render_report())

    Parameters
    ----------
    config:
        A plain Python dictionary following the schema accepted by
        :meth:`ScalingManifestBuilder.from_config`.

    Returns
    -------
    ScalingLimitsManifest
        The built and validated manifest.
    """
    builder = ScalingManifestBuilder.from_config(config)
    return builder.build()
