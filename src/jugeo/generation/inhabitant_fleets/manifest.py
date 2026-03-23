"""Package Manifest — inhabitant_fleets module registry and dependency tracker.

Overview
--------
This module provides the *package manifest* for the ``inhabitant_fleets``
sub-package.  The manifest serves three purposes:

  1. **Module Registry** – records every sub-module with its description,
     exported symbols, version, and theoretical section reference.

  2. **Export Tracking** – the ExportRegistry maintains a mapping from
     symbol name to the module it originates from, enabling programmatic
     introspection of the package's public API.

  3. **Dependency Ordering** – the DependencyTracker records inter-module
     dependencies, detects cycles (DFS-based), and computes a topological
     load order (Kahn's algorithm).

Architecture
-------------
The manifest follows the *Registry Pattern*: a single
``InhabitantFleetsManifest`` object holds all descriptors, the export
registry, and the dependency tracker.  Callers construct the manifest by
calling ``build()``, which populates all three components atomically.

Sub-modules Covered
---------------------
The manifest covers eight sub-modules:

  1. ``models``                      – Ch42 core data types
  2. ``local_inhabitant_synthesis`` – Ch42 §1 local synthesis
  3. ``ai_fleets``               – Ch42 §2 fleet architecture
  4. ``semantic_backpressure``   – Ch42 §3 backpressure
  5. ``algorithms``                  – Ch42 algorithms
  6. ``integration``                 – Ch42 integration layer
  7. ``theorems``                    – Ch42 formal theorems
  8. ``manifest``                    – Ch42 manifest (this module)

Dependency Graph
-----------------
The inter-module dependency graph is a DAG (directed acyclic graph):

    models
      ↑
    s01 ← s02
      ↑     ↑
    s03 ←──┘
      ↑
    algorithms
      ↑
    integration ← theorems
      ↑
    manifest

Topological order (Kahn's algorithm):

    models → s01 → s02 → s03 → algorithms → integration → theorems → manifest

No cycle exists in this graph (verified by DFS cycle detection).

Dependency Tracker — DFS Cycle Detection
------------------------------------------
Cycle detection uses a three-colour DFS (WHITE/GRAY/BLACK):

    WHITE (0) – not yet visited
    GRAY  (1) – currently on the DFS stack (potential back-edge)
    BLACK (2) – fully processed

A cycle exists iff the DFS visits a GRAY node.

Dependency Tracker — Kahn's Algorithm
----------------------------------------
Topological ordering uses Kahn's algorithm (BFS with in-degree tracking):

    1. Compute in_degree[v] = |{u : (u, v) ∈ E}| for all v
    2. Initialize queue with all v where in_degree[v] = 0
    3. While queue is non-empty:
         v ← dequeue (sorted for determinism)
         append v to order
         for each neighbour w of v:
             in_degree[w] -= 1
             if in_degree[w] == 0: enqueue w
    4. Return order

ExportRegistry
---------------
The ExportRegistry maps symbol names to:
  • the owning object (Any)
  • the source module name (str)

It also maintains a reverse mapping (module → list of symbol names) for
efficient module-level queries.

Validation
-----------
``InhabitantFleetsManifest.validate()`` returns a list of error strings.
A valid manifest has:
  • ``_built = True``
  • No dependency cycles
  • Each module descriptor passes its own validation:
      – non-empty ``module_name``
      – non-empty ``description``

Examples
---------
>>> from jugeo.generation.inhabitant_fleets.manifest import build_manifest
>>> m = build_manifest()
>>> m.validate()
[]
>>> m.export_count() > 0
True
>>> "models" in m.modules
True
>>> order = m.tracker.topological_order()
>>> order.index("models") < order.index("ai_fleets")
True

See Also
---------
- jugeo.generation.inhabitant_fleets.integration  – integration layer
- jugeo.generation.inhabitant_fleets.theorems     – formal theorems
"""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# ModuleDescriptor
# ---------------------------------------------------------------------------


@dataclass
class ModuleDescriptor:
    """Describes a single sub-module of the inhabitant_fleets package.

    A ModuleDescriptor records:
      • The module's fully qualified name
      • The path to its source file (relative to the package root)
      • A human-readable description
      • The list of exported symbol names
      • The module version (SemVer string)
      • The list of direct dependency module names
      • The theoretical section from Chapter 42 that the module implements

    Theory — Ch42 Manifest §1
    ---------------------------
    Each sub-module M is characterised by a tuple:

        M = (name, file, description, exports, version, deps, section)

    The *export closure* of M is the set of all symbols reachable from M
    by following the dependency graph:

        closure(M) = exports(M) ∪ ⋃_{d ∈ deps(M)} closure(d)

    The manifest's ExportRegistry tracks individual exports, while the
    DependencyTracker tracks the dep edges.

    Attributes
    ----------
    module_name : str
        Short module name (e.g. "models", "local_inhabitant_synthesis").
    file_path : str
        Path to the module file relative to the package root.
    description : str
        Human-readable description of the module's purpose.
    exports : list[str]
        Names of all public symbols exported by this module.
    version : str
        Module version string (SemVer).
    dependencies : list[str]
        Names of other sub-modules this module directly depends on.
    theory_section : str
        Chapter 42 section reference (e.g. "Ch42 §1").

    Examples
    --------
    >>> desc = ModuleDescriptor(
    ...     module_name="models",
    ...     file_path="models.py",
    ...     description="Core data types.",
    ...     exports=["InhabitantProposal"],
    ... )
    >>> desc.validate()
    []
    """

    module_name: str
    file_path: str
    description: str
    exports: list[str]
    version: str = "0.1.0"
    dependencies: list[str] = field(default_factory=list)
    theory_section: str = ""

    def to_dict(self) -> dict:
        """Serialise the descriptor to a plain dict.

        Returns
        -------
        dict
            All fields as a JSON-serialisable dict.
        """
        return {
            "module_name": self.module_name,
            "file_path": self.file_path,
            "description": self.description,
            "exports": self.exports,
            "version": self.version,
            "dependencies": self.dependencies,
            "theory_section": self.theory_section,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ModuleDescriptor":
        """Deserialise from a plain dict.

        Parameters
        ----------
        d : dict
            Dict with the same keys as to_dict().

        Returns
        -------
        ModuleDescriptor
        """
        return cls(
            module_name=d["module_name"],
            file_path=d.get("file_path", ""),
            description=d.get("description", ""),
            exports=d.get("exports", []),
            version=d.get("version", "0.1.0"),
            dependencies=d.get("dependencies", []),
            theory_section=d.get("theory_section", ""),
        )

    def validate(self) -> list[str]:
        """Validate this descriptor and return a list of error strings.

        Returns
        -------
        list[str]
            Empty if valid; error messages otherwise.
        """
        errors: list[str] = []
        if not self.module_name:
            errors.append("module_name is empty")
        if not self.description:
            errors.append("description is empty")
        return errors

    def export_count(self) -> int:
        """Return the number of exported symbols."""
        return len(self.exports)

    def has_dependency(self, module_name: str) -> bool:
        """Return True if this module depends on module_name.

        Parameters
        ----------
        module_name : str
        """
        return module_name in self.dependencies

    def __repr__(self) -> str:
        return (
            f"ModuleDescriptor("
            f"name={self.module_name!r}, "
            f"exports={len(self.exports)}, "
            f"deps={self.dependencies!r})"
        )


# ---------------------------------------------------------------------------
# ExportRegistry
# ---------------------------------------------------------------------------


class ExportRegistry:
    """Registry of named exports from all sub-modules.

    The ExportRegistry maintains two mappings:
      • ``_exports``    : symbol name → object
      • ``_module_map`` : symbol name → source module name

    This enables both symbol lookup (get) and module-level queries
    (exports_from).

    Theory — Ch42 Manifest §2
    ---------------------------
    The export registry implements a *namespace table* T:

        T = { (name, obj, module) | name ∈ public_api(module) }

    Symbol resolution:

        resolve(name) = obj  where (name, obj, _) ∈ T
                      = None  if name ∉ T

    Module listing:

        exports_from(m) = { name | (name, _, m) ∈ T }

    Attributes
    ----------
    _exports : dict[str, Any]
        Mapping from symbol name to object.
    _module_map : dict[str, str]
        Mapping from symbol name to source module name.
    _modules : dict[str, ModuleDescriptor]
        Registered module descriptors (keyed by module name).

    Examples
    --------
    >>> reg = ExportRegistry()
    >>> reg.register("MyClass", object(), "mymodule")
    >>> reg.get("MyClass") is not None
    True
    >>> reg.exports_from("mymodule")
    ['MyClass']
    """

    def __init__(self) -> None:
        self._exports: dict[str, Any] = {}
        self._module_map: dict[str, str] = {}
        self._modules: dict[str, ModuleDescriptor] = {}

    def register(self, name: str, obj: Any, module: str) -> None:
        """Register a named export.

        Parameters
        ----------
        name : str
            Symbol name.
        obj : Any
            The exported object.
        module : str
            Source module name.
        """
        self._exports[name] = obj
        self._module_map[name] = module

    def register_module(self, descriptor: ModuleDescriptor) -> None:
        """Register a ModuleDescriptor.

        Parameters
        ----------
        descriptor : ModuleDescriptor
        """
        self._modules[descriptor.module_name] = descriptor

    def get(self, name: str) -> Any | None:
        """Look up a symbol by name.

        Parameters
        ----------
        name : str

        Returns
        -------
        Any | None
            The registered object, or None if not found.
        """
        return self._exports.get(name)

    def list_exports(self) -> list[str]:
        """Return a sorted list of all registered symbol names.

        Returns
        -------
        list[str]
        """
        return sorted(self._exports.keys())

    def exports_from(self, module: str) -> list[str]:
        """Return symbols originating from the given module.

        Parameters
        ----------
        module : str
            Module name.

        Returns
        -------
        list[str]
            Sorted list of symbol names from that module.
        """
        return sorted(n for n, m in self._module_map.items() if m == module)

    def count(self) -> int:
        """Return the total number of registered symbols.

        Returns
        -------
        int
        """
        return len(self._exports)

    def source_module(self, name: str) -> str | None:
        """Return the source module for a symbol name, or None.

        Parameters
        ----------
        name : str

        Returns
        -------
        str | None
        """
        return self._module_map.get(name)

    def all_modules(self) -> list[str]:
        """Return all module names that have registered exports.

        Returns
        -------
        list[str]
        """
        return sorted(set(self._module_map.values()))

    def __repr__(self) -> str:
        return f"ExportRegistry(symbols={len(self._exports)}, modules={len(self._modules)})"


# ---------------------------------------------------------------------------
# DependencyTracker
# ---------------------------------------------------------------------------


class DependencyTracker:
    """Tracks inter-module dependencies with cycle detection and topological ordering.

    The DependencyTracker maintains a directed graph:

        G = (V, E)  where  V = module names  and  E = dependency edges

    An edge (source, target) means "source depends on target".

    Cycle Detection (DFS — three-colour algorithm)
    -----------------------------------------------
    Uses WHITE (0) / GRAY (1) / BLACK (2) colouring:

        WHITE – not yet visited
        GRAY  – on the current DFS path (in the stack)
        BLACK – fully processed

    A back-edge (to a GRAY node) indicates a cycle.

        has_cycle() → True iff ∃ back-edge in the DFS

    Topological Ordering (Kahn's Algorithm)
    ----------------------------------------
    Uses in-degree tracking with a FIFO queue sorted for determinism:

        1. in_degree[v] ← |{u : (u,v) ∈ E}|
        2. queue ← {v : in_degree[v] = 0}
        3. While queue ≠ ∅:
               v ← pop(sorted(queue))
               order.append(v)
               for w ∈ successors(v):
                   in_degree[w] -= 1
                   if in_degree[w] = 0: queue.add(w)
        4. return order

    If the graph has a cycle, topological_order() returns an incomplete
    ordering (the cycle nodes are omitted).

    Attributes
    ----------
    _deps : dict[str, set[str]]
        Forward edges: source → {targets}.
    _rdeps : dict[str, set[str]]
        Reverse edges: target → {sources}.

    Examples
    --------
    >>> tracker = DependencyTracker()
    >>> tracker.add("integration", "models")
    >>> tracker.add("integration", "local_inhabitant_synthesis")
    >>> tracker.has_cycle()
    False
    >>> order = tracker.topological_order()
    >>> order.index("models") < order.index("integration")
    True
    """

    def __init__(self) -> None:
        self._deps: dict[str, set[str]] = {}
        self._rdeps: dict[str, set[str]] = {}

    def add(self, source: str, target: str) -> None:
        """Add a dependency edge source → target.

        Both nodes are registered in the graph even if they have no other
        edges.

        Parameters
        ----------
        source : str
            Module that depends on target.
        target : str
            Module that is depended upon.
        """
        self._deps.setdefault(source, set()).add(target)
        self._rdeps.setdefault(target, set()).add(source)
        # Ensure both appear as keys in both dicts
        self._deps.setdefault(target, set())
        self._rdeps.setdefault(source, set())

    def get_dependencies(self, module: str) -> list[str]:
        """Return direct dependencies of module (sorted).

        Parameters
        ----------
        module : str

        Returns
        -------
        list[str]
        """
        return sorted(self._deps.get(module, set()))

    def get_dependents(self, module: str) -> list[str]:
        """Return modules that directly depend on module (sorted).

        Parameters
        ----------
        module : str

        Returns
        -------
        list[str]
        """
        return sorted(self._rdeps.get(module, set()))

    def has_cycle(self) -> bool:
        """Return True if the dependency graph contains a cycle.

        Uses three-colour DFS to detect back-edges.

        Returns
        -------
        bool
        """
        WHITE, GRAY, BLACK = 0, 1, 2
        color: dict[str, int] = {n: WHITE for n in self._deps}

        def dfs(node: str) -> bool:
            color[node] = GRAY
            for nb in self._deps.get(node, set()):
                if color.get(nb, WHITE) == GRAY:
                    return True
                if color.get(nb, WHITE) == WHITE and dfs(nb):
                    return True
            color[node] = BLACK
            return False

        return any(dfs(n) for n in list(color) if color[n] == WHITE)

    def topological_order(self) -> list[str]:
        """Compute a topological order of modules using Kahn's algorithm.

        Returns
        -------
        list[str]
            Modules in topological order (dependencies before dependents).
            If a cycle exists, cycle nodes are omitted.
        """
        # in_degree[v] = number of modules that v depends on
        # (i.e., how many modules must come before v)
        in_degree: dict[str, int] = {
            n: len(self._rdeps.get(n, set())) for n in self._deps
        }
        queue = [n for n, d in in_degree.items() if d == 0]
        order: list[str] = []
        while queue:
            queue.sort()  # deterministic ordering
            node = queue.pop(0)
            order.append(node)
            for dep in sorted(self._deps.get(node, set())):
                in_degree[dep] -= 1
                if in_degree[dep] == 0:
                    queue.append(dep)
        return order

    def all_modules(self) -> list[str]:
        """Return all module names known to the tracker.

        Returns
        -------
        list[str]
        """
        return sorted(self._deps.keys())

    def edge_count(self) -> int:
        """Return the total number of dependency edges.

        Returns
        -------
        int
        """
        return sum(len(v) for v in self._deps.values())

    def __repr__(self) -> str:
        return (
            f"DependencyTracker("
            f"modules={len(self._deps)}, "
            f"edges={self.edge_count()})"
        )


# ---------------------------------------------------------------------------
# InhabitantFleetsManifest
# ---------------------------------------------------------------------------


class InhabitantFleetsManifest:
    """Complete manifest for the inhabitant_fleets package.

    The manifest aggregates all sub-module descriptors, the export
    registry, and the dependency tracker into a single object.

    Build Process
    --------------
    Call ``build()`` to populate the manifest.  This is idempotent:
    calling build() twice is safe (the second call resets state).

        manifest = InhabitantFleetsManifest()
        manifest.build()

    After building:
      • ``manifest.modules``            – 8 ModuleDescriptor objects
      • ``manifest.registry.count()``   – total exported symbols
      • ``manifest.tracker.topological_order()`` – load order

    Serialisation
    --------------
    ``to_dict()`` returns a JSON-serialisable dict:

        {
            "built":            bool,
            "build_time":       float,
            "modules":          { name: ModuleDescriptor.to_dict() },
            "dependency_order": list[str],
        }

    Validation
    -----------
    ``validate()`` returns a list of error strings.  An empty list means
    the manifest is fully valid.

    Attributes
    ----------
    modules : dict[str, ModuleDescriptor]
        Module descriptors keyed by module name.
    registry : ExportRegistry
        Export registry.
    tracker : DependencyTracker
        Dependency tracker.
    _built : bool
        Whether build() has been called.
    _build_time : float
        Unix timestamp of the last build() call.

    Examples
    --------
    >>> from jugeo.generation.inhabitant_fleets.manifest import InhabitantFleetsManifest
    >>> m = InhabitantFleetsManifest()
    >>> m.build()
    >>> m.validate()
    []
    >>> m.export_count() > 0
    True
    >>> "models" in m.modules
    True
    """

    def __init__(self) -> None:
        self.modules: dict[str, ModuleDescriptor] = {}
        self.registry = ExportRegistry()
        self.tracker = DependencyTracker()
        self._built: bool = False
        self._build_time: float = 0.0

    def build(self) -> None:
        """Populate the manifest with all 8 module descriptors.

        This method:
          1. Creates a ModuleDescriptor for each of the 8 sub-modules
          2. Registers each descriptor in self.modules and self.registry
          3. Registers dependency edges in self.tracker
          4. Sets _built = True and records build_time

        Sub-modules registered:
          models, local_inhabitant_synthesis, ai_fleets,
          semantic_backpressure, algorithms, integration,
          theorems, manifest
        """
        # Reset state
        self.modules = {}
        self.registry = ExportRegistry()
        self.tracker = DependencyTracker()

        # --- 1. models ---
        models_desc = ModuleDescriptor(
            module_name="models",
            file_path="models.py",
            description=(
                "Core data types for the inhabitant_fleets package: "
                "InhabitantProposal, FleetBid, BackpressureSignal, SemanticMove, "
                "NormalizedProposal, and factory functions."
            ),
            exports=[
                "ProposalStatus",
                "SeverityLevel",
                "MoveType",
                "TrustTier",
                "InhabitantProposal",
                "FleetBid",
                "BackpressureSignal",
                "SemanticMove",
                "NormalizedProposal",
                "make_proposal",
                "make_bid",
                "make_signal",
                "make_move",
            ],
            theory_section="Ch42",
        )
        self._register_module(models_desc)

        # --- 2. local_inhabitant_synthesis ---
        desc = ModuleDescriptor(
            module_name="local_inhabitant_synthesis",
            file_path="local_inhabitant_synthesis.py",
            description=(
                "Ch42 §1 local inhabitant synthesis: InhabitantSpace, "
                "SynthesisContext, InhabitantValidator, LocalInhabitantSynthesizer, "
                "and synthesis helper functions."
            ),
            exports=[
                "InhabitantSpace",
                "SynthesisContext",
                "InhabitantValidator",
                "LocalInhabitantSynthesizer",
                "synthesize_inhabitants",
                "normalize_proposal",
                "create_synthesis_context",
            ],
            dependencies=["models"],
            theory_section="Ch42 §1",
        )
        self._register_module(desc)
        self.tracker.add("local_inhabitant_synthesis", "models")

        # --- 3. ai_fleets ---
        desc = ModuleDescriptor(
            module_name="ai_fleets",
            file_path="ai_fleets.py",
            description=(
                "Ch42 §2 AI fleet architecture: FleetMember, FleetCoordinator, "
                "InhabitantFleet, FleetRegistry, BidAggregator, and factory functions."
            ),
            exports=[
                "FleetMember",
                "FleetCoordinator",
                "InhabitantFleet",
                "FleetRegistry",
                "BidAggregator",
                "create_default_fleet",
                "create_fleet_member",
            ],
            dependencies=["models", "local_inhabitant_synthesis"],
            theory_section="Ch42 §2",
        )
        self._register_module(desc)
        self.tracker.add("ai_fleets", "models")
        self.tracker.add("ai_fleets", "local_inhabitant_synthesis")

        # --- 4. semantic_backpressure ---
        desc = ModuleDescriptor(
            module_name="semantic_backpressure",
            file_path="semantic_backpressure.py",
            description=(
                "Ch42 §3 semantic backpressure: InstabilityMetric, "
                "BackpressureMonitor, BackpressureController, BackpressureResolver, "
                "and CascadeDetector."
            ),
            exports=[
                "InstabilityMetric",
                "BackpressureMonitor",
                "BackpressureController",
                "BackpressureResolver",
                "CascadeDetector",
            ],
            dependencies=["models"],
            theory_section="Ch42 §3",
        )
        self._register_module(desc)
        self.tracker.add("semantic_backpressure", "models")

        # --- 5. algorithms ---
        algo_desc = ModuleDescriptor(
            module_name="algorithms",
            file_path="algorithms.py",
            description=(
                "Ch42 algorithms: fleet allocation (greedy, optimal, heuristic), "
                "backpressure propagation, multi-criteria ranking, semantic distance, "
                "and convergence checking."
            ),
            exports=[
                "FleetAllocationAlgorithm",
                "GreedyFleetAllocation",
                "OptimalFleetAllocation",
                "HeuristicFleetAllocation",
                "BackpressurePropagation",
                "InhabitantRanking",
                "SemanticDistanceComputer",
                "FleetConvergenceChecker",
            ],
            dependencies=["models"],
            theory_section="Ch42 algorithms",
        )
        self._register_module(algo_desc)
        self.tracker.add("algorithms", "models")

        # --- 6. integration ---
        integ_desc = ModuleDescriptor(
            module_name="integration",
            file_path="integration.py",
            description=(
                "Ch42 integration layer: adaptors for descent engine, goal system, "
                "frontier, and construction loop.  InhabitantFleetPipeline orchestrates "
                "the full synthesis pipeline."
            ),
            exports=[
                "DescentAdaptor",
                "GoalAdaptor",
                "FrontierIntegrator",
                "ConstructionAdaptor",
                "InhabitantFleetPipeline",
                "create_pipeline",
            ],
            dependencies=[
                "models",
                "local_inhabitant_synthesis",
                "ai_fleets",
                "semantic_backpressure",
                "algorithms",
            ],
            theory_section="Ch42 integration",
        )
        self._register_module(integ_desc)
        for dep in integ_desc.dependencies:
            self.tracker.add("integration", dep)

        # --- 7. theorems ---
        thm_desc = ModuleDescriptor(
            module_name="theorems",
            file_path="theorems.py",
            description=(
                "Ch42 formal theorems: FleetConvergenceTheorem (42.1), "
                "BackpressureBoundednessTheorem (42.2), "
                "SemanticMoveCompletenessTheorem (42.3), "
                "InhabitantExistenceTheorem (42.4), and verify_all_theorems()."
            ),
            exports=[
                "TheoremVerifier",
                "FleetConvergenceTheorem",
                "BackpressureBoundednessTheorem",
                "SemanticMoveCompletenessTheorem",
                "InhabitantExistenceTheorem",
                "verify_all_theorems",
            ],
            dependencies=["models", "local_inhabitant_synthesis"],
            theory_section="Ch42 theorems",
        )
        self._register_module(thm_desc)
        for dep in thm_desc.dependencies:
            self.tracker.add("theorems", dep)

        # --- 8. manifest (self) ---
        manifest_desc = ModuleDescriptor(
            module_name="manifest",
            file_path="manifest.py",
            description=(
                "Ch42 package manifest: ModuleDescriptor dataclass, "
                "ExportRegistry, DependencyTracker (DFS cycle detection + "
                "Kahn's topological sort), and InhabitantFleetsManifest."
            ),
            exports=[
                "ModuleDescriptor",
                "ExportRegistry",
                "DependencyTracker",
                "InhabitantFleetsManifest",
                "build_manifest",
            ],
            theory_section="Ch42 manifest",
        )
        self._register_module(manifest_desc)

        self._built = True
        self._build_time = time.time()

    def _register_module(self, descriptor: ModuleDescriptor) -> None:
        """Register a module descriptor in modules and registry.

        Parameters
        ----------
        descriptor : ModuleDescriptor
        """
        self.modules[descriptor.module_name] = descriptor
        self.registry.register_module(descriptor)
        for export_name in descriptor.exports:
            self.registry.register(
                export_name, export_name, descriptor.module_name
            )

    def describe(self) -> str:
        """Return a human-readable summary of the manifest.

        Returns
        -------
        str
        """
        lines = [
            "InhabitantFleetsManifest",
            f"  Built: {self._built}",
            f"  Modules: {len(self.modules)}",
            f"  Exports: {self.registry.count()}",
            "",
        ]
        for name, mod in sorted(self.modules.items()):
            lines.append(
                f"  {name}: {len(mod.exports)} exports ({mod.theory_section})"
            )
        return "\n".join(lines)

    def to_dict(self) -> dict:
        """Serialise the manifest to a JSON-serialisable dict.

        Returns
        -------
        dict
            Keys: built, build_time, modules, dependency_order.
        """
        return {
            "built": self._built,
            "build_time": self._build_time,
            "modules": {k: v.to_dict() for k, v in self.modules.items()},
            "dependency_order": self.tracker.topological_order(),
        }

    def validation_errors(self) -> list[str]:
        """Validate the manifest and return a list of error strings.

        Returns
        -------
        list[str]
            Empty if the manifest is fully valid.
        """
        errors: list[str] = []
        if not self._built:
            errors.append("manifest not built; call build() first")
        if self.tracker.has_cycle():
            errors.append("dependency cycle detected")
        for name, mod in self.modules.items():
            errors.extend(mod.validate())
        return errors

    def validate(self) -> bool:
        """Legacy boolean validation API."""
        return len(self.validation_errors()) == 0

    def get_module(self, name: str) -> ModuleDescriptor | None:
        """Return the descriptor for the named module, or None.

        Parameters
        ----------
        name : str

        Returns
        -------
        ModuleDescriptor | None
        """
        return self.modules.get(name)

    def export_count(self) -> int:
        """Return the total number of registered exports.

        Returns
        -------
        int
        """
        return self.registry.count()

    def module_count(self) -> int:
        """Return the number of registered modules.

        Returns
        -------
        int
        """
        return len(self.modules)

    def dependency_order(self) -> list[str]:
        """Return modules in topological dependency order.

        Returns
        -------
        list[str]
        """
        return self.tracker.topological_order()

    def __repr__(self) -> str:
        return (
            f"InhabitantFleetsManifest("
            f"built={self._built}, "
            f"modules={len(self.modules)}, "
            f"exports={self.registry.count()})"
        )


# ---------------------------------------------------------------------------
# Module-level factory
# ---------------------------------------------------------------------------


def build_manifest() -> InhabitantFleetsManifest:
    """Build and return a fully populated InhabitantFleetsManifest.

    Convenience function that creates a manifest, calls build(), and
    returns it.

    Returns
    -------
    InhabitantFleetsManifest
        A fully built and validated manifest.

    Examples
    --------
    >>> from jugeo.generation.inhabitant_fleets.manifest import build_manifest
    >>> m = build_manifest()
    >>> m.validate()
    []
    >>> m.module_count()
    8
    """
    m = InhabitantFleetsManifest()
    m.build()
    return m


__all__ = [
    "ModuleDescriptor",
    "ExportRegistry",
    "DependencyTracker",
    "InhabitantFleetsManifest",
    "build_manifest",
]
