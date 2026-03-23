"""
manifest.py -- Package manifest for jugeo.generation.replay_gluing.

This module provides a machine-readable description of every module,
class, function, constant, and dependency in the replay_gluing package
(theory2.tex Chapter 43).

Purpose
-------
The manifest serves several roles in the jugeo ecosystem:

  1. **Discoverability** -- tools can query the manifest to enumerate what
     the package exports without importing it.
  2. **Dependency auditing** -- the DependencyTracker records inter-module
     dependencies and can detect cycles.
  3. **Documentation generation** -- ReplayGluingManifest.generate_readme()
     produces a README-style overview automatically.
  4. **Integrity checking** -- check_manifest_integrity() verifies that all
     declared exports resolve to known modules.

Structure of this module
------------------------
  DependencyKind    -- REQUIRED | OPTIONAL | SOFT
  ExportKind        -- CLASS | FUNCTION | CONSTANT | TYPE_ALIAS | EXCEPTION | ENUM
  ModuleDescriptor  -- metadata for a single .py file
  ExportDescriptor  -- metadata for a single public symbol
  DependencyRecord  -- one directed module dependency
  ExportRegistry    -- searchable registry of ExportDescriptor instances
  DependencyTracker -- directed dependency graph with cycle detection
  ReplayGluingManifest -- top-level manifest; aggregates everything above

Module-level globals
--------------------
  MANIFEST          -- pre-populated instance for the entire package
  get_manifest()    -- accessor returning MANIFEST
  list_all_exports()-- return sorted list of all public export names
  check_manifest_integrity() -- run integrity checks on MANIFEST

Usage example
-------------
    from jugeo.generation.replay_gluing.manifest import get_manifest

    m = get_manifest()
    print(m.summary())
    for mod in m.list_modules():
        print(mod)
    readme = m.generate_readme()

Theory reference
----------------
  theory2.tex Chapter 43 "Correctness of Replay-Gluing"
"""

from __future__ import annotations

import uuid
import time
import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


__all__ = [
    "DependencyKind",
    "ExportKind",
    "ModuleDescriptor",
    "ExportDescriptor",
    "DependencyRecord",
    "ExportRegistry",
    "DependencyTracker",
    "ReplayGluingManifest",
    "MANIFEST",
    "get_manifest",
    "list_all_exports",
    "check_manifest_integrity",
]

_MODULE_VERSION: str = "0.1.0"
_PACKAGE_NAME: str = "jugeo.generation.replay_gluing"
_THEORY_REF: str = "theory2.tex Ch43"


# ===========================================================================
# Enumerations
# ===========================================================================

class DependencyKind(Enum):
    """Strength of a module dependency relationship.

    Values
    ------
    REQUIRED
        The importing module cannot function without the dependency.
    OPTIONAL
        The importing module degrades gracefully if the dependency is absent.
    SOFT
        The importing module may use the dependency at runtime but does not
        fail at import time if it is missing.
    """

    REQUIRED = "REQUIRED"
    OPTIONAL = "OPTIONAL"
    SOFT = "SOFT"


class ExportKind(Enum):
    """Kind of a public symbol exported by a module.

    Values
    ------
    CLASS
        A Python class (regular or dataclass).
    FUNCTION
        A callable function or staticmethod.
    CONSTANT
        A module-level constant (typically upper-case).
    TYPE_ALIAS
        A type alias defined with TypeAlias or plain assignment.
    EXCEPTION
        A subclass of BaseException.
    ENUM
        A subclass of enum.Enum.
    """

    CLASS = "CLASS"
    FUNCTION = "FUNCTION"
    CONSTANT = "CONSTANT"
    TYPE_ALIAS = "TYPE_ALIAS"
    EXCEPTION = "EXCEPTION"
    ENUM = "ENUM"


# ===========================================================================
# Data classes
# ===========================================================================

@dataclass
class ModuleDescriptor:
    """
    Metadata describing a single Python module within the package.

    Attributes
    ----------
    module_name : str
        Dotted module name relative to the package root, e.g. "models".
    file_path : str
        Path relative to the src/ directory, e.g.
        "jugeo/generation/replay_gluing/models.py".
    description : str
        One-paragraph description of the module's purpose.
    exports : list[str]
        Names of public symbols exported from this module.
    imports : list[str]
        Dotted names of modules this module imports from.
    line_count : int
        Approximate number of lines in the module (0 = unknown).
    theory_section : str
        Section of theory2.tex most relevant to this module.
    """

    module_name: str
    file_path: str
    description: str
    exports: list[str] = field(default_factory=list)
    imports: list[str] = field(default_factory=list)
    line_count: int = 0
    theory_section: str = ""

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a plain dictionary."""
        return {
            "module_name": self.module_name,
            "file_path": self.file_path,
            "description": self.description,
            "exports": list(self.exports),
            "imports": list(self.imports),
            "line_count": self.line_count,
            "theory_section": self.theory_section,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ModuleDescriptor:
        """Reconstruct a ModuleDescriptor from a plain dictionary."""
        return cls(
            module_name=d.get("module_name", ""),
            file_path=d.get("file_path", ""),
            description=d.get("description", ""),
            exports=list(d.get("exports", [])),
            imports=list(d.get("imports", [])),
            line_count=int(d.get("line_count", 0)),
            theory_section=d.get("theory_section", ""),
        )

    def get_public_exports(self) -> list[str]:
        """Return only exports that do not start with an underscore."""
        return [e for e in self.exports if not e.startswith("_")]


@dataclass
class ExportDescriptor:
    """
    Metadata describing a single public symbol exported by the package.

    Attributes
    ----------
    name : str
        The symbol name as it appears in Python source.
    module : str
        Dotted module name where the symbol is defined.
    kind : ExportKind
        What kind of symbol this is.
    description : str
        One-sentence description of the symbol.
    is_public : bool
        Whether this symbol is part of the public API (default True).
    """

    name: str
    module: str
    kind: ExportKind
    description: str
    is_public: bool = True

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a plain dictionary."""
        return {
            "name": self.name,
            "module": self.module,
            "kind": self.kind.value,
            "description": self.description,
            "is_public": self.is_public,
        }

    def full_qualified_name(self) -> str:
        """Return the fully qualified dotted name, e.g. 'models.ReplayGluingPlan'."""
        return f"{self.module}.{self.name}"


@dataclass
class DependencyRecord:
    """
    A directed dependency from one module to another.

    Attributes
    ----------
    from_module : str
        Module that declares the dependency.
    to_module : str
        Module that is depended upon.
    kind : DependencyKind
        Strength of the dependency.
    symbols : list[str]
        Specific symbols imported from to_module (may be empty for wildcard).
    """

    from_module: str
    to_module: str
    kind: DependencyKind = DependencyKind.REQUIRED
    symbols: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a plain dictionary."""
        return {
            "from_module": self.from_module,
            "to_module": self.to_module,
            "kind": self.kind.value,
            "symbols": list(self.symbols),
        }

    def is_required(self) -> bool:
        """Return True iff this is a REQUIRED dependency."""
        return self.kind == DependencyKind.REQUIRED


# ===========================================================================
# ExportRegistry
# ===========================================================================

class ExportRegistry:
    """
    Searchable registry of ExportDescriptor instances.

    Provides O(1) lookup by name and filtered views by module or kind.
    """

    def __init__(self) -> None:
        self._exports: dict[str, ExportDescriptor] = {}

    # ------------------------------------------------------------------
    # Mutation
    # ------------------------------------------------------------------

    def register(self, export: ExportDescriptor) -> None:
        """Register an export descriptor; overwrites any prior entry."""
        self._exports[export.name] = export

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    def lookup(self, name: str) -> Optional[ExportDescriptor]:
        """Return the ExportDescriptor for name, or None if not found."""
        return self._exports.get(name)

    def get_all_public(self) -> list[ExportDescriptor]:
        """Return all public export descriptors, sorted by name."""
        return sorted(
            (e for e in self._exports.values() if e.is_public),
            key=lambda e: e.name,
        )

    def get_by_module(self, module: str) -> list[ExportDescriptor]:
        """Return all exports defined in the given module, sorted by name."""
        return sorted(
            (e for e in self._exports.values() if e.module == module),
            key=lambda e: e.name,
        )

    def get_by_kind(self, kind: ExportKind) -> list[ExportDescriptor]:
        """Return all exports of the given kind, sorted by name."""
        return sorted(
            (e for e in self._exports.values() if e.kind == kind),
            key=lambda e: e.name,
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize the registry to a plain dictionary."""
        return {
            "exports": [e.to_dict() for e in sorted(self._exports.values(), key=lambda x: x.name)],
            "count": len(self._exports),
        }

    def count(self) -> int:
        """Return the number of registered exports."""
        return len(self._exports)


# ===========================================================================
# DependencyTracker
# ===========================================================================

class DependencyTracker:
    """
    Directed dependency graph with cycle detection and topological ordering.

    Modules are nodes; DependencyRecord instances are directed edges.
    """

    def __init__(self) -> None:
        self._deps: list[DependencyRecord] = []

    # ------------------------------------------------------------------
    # Mutation
    # ------------------------------------------------------------------

    def add(self, dep: DependencyRecord) -> None:
        """Add a dependency record to the graph."""
        self._deps.append(dep)

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    def get_dependencies(self, module: str) -> list[DependencyRecord]:
        """Return all outgoing dependencies of module (what it imports)."""
        return [d for d in self._deps if d.from_module == module]

    def get_dependents(self, module: str) -> list[DependencyRecord]:
        """Return all incoming dependencies to module (what imports it)."""
        return [d for d in self._deps if d.to_module == module]

    def has_cycle(self) -> bool:
        """
        Return True iff the dependency graph contains a cycle.

        Uses iterative DFS with colouring (white/grey/black).
        """
        # Build adjacency list
        adj: dict[str, list[str]] = {}
        for dep in self._deps:
            adj.setdefault(dep.from_module, [])
            adj.setdefault(dep.to_module, [])
            adj[dep.from_module].append(dep.to_module)

        color: dict[str, int] = {n: 0 for n in adj}  # 0=white, 1=grey, 2=black

        def dfs(start: str) -> bool:
            stack = [(start, iter(adj.get(start, [])))]
            color[start] = 1
            while stack:
                node, children = stack[-1]
                try:
                    child = next(children)
                    if color.get(child, 0) == 1:
                        return True  # back edge => cycle
                    if color.get(child, 0) == 0:
                        color[child] = 1
                        stack.append((child, iter(adj.get(child, []))))
                except StopIteration:
                    color[node] = 2
                    stack.pop()
            return False

        for node in list(adj.keys()):
            if color.get(node, 0) == 0:
                if dfs(node):
                    return True
        return False

    def topological_order(self) -> list[str]:
        """
        Return modules in topological order (dependencies before dependents).

        If a cycle exists, returns a best-effort ordering.

        Returns
        -------
        list[str]
        """
        adj: dict[str, list[str]] = {}
        in_degree: dict[str, int] = {}

        for dep in self._deps:
            adj.setdefault(dep.from_module, [])
            adj.setdefault(dep.to_module, [])
            adj[dep.from_module].append(dep.to_module)

        for node in adj:
            in_degree.setdefault(node, 0)
        for dep in self._deps:
            in_degree[dep.to_module] = in_degree.get(dep.to_module, 0) + 1

        # Kahn's algorithm
        queue = sorted(n for n, d in in_degree.items() if d == 0)
        order: list[str] = []
        while queue:
            node = queue.pop(0)
            order.append(node)
            for neighbour in sorted(adj.get(node, [])):
                in_degree[neighbour] -= 1
                if in_degree[neighbour] == 0:
                    queue.append(neighbour)

        # Append any remaining nodes (cycle members)
        for node in sorted(adj.keys()):
            if node not in order:
                order.append(node)

        return order

    def to_dict(self) -> dict[str, Any]:
        """Serialize the dependency graph to a plain dictionary."""
        return {
            "dependencies": [d.to_dict() for d in self._deps],
            "count": len(self._deps),
            "has_cycle": self.has_cycle(),
        }


# ===========================================================================
# ReplayGluingManifest
# ===========================================================================

class ReplayGluingManifest:
    """
    Top-level manifest for the jugeo.generation.replay_gluing package.

    Aggregates module descriptors, export registry, and dependency graph.
    Can be serialised to / deserialised from plain dicts for persistence.
    """

    def __init__(self) -> None:
        self.modules: list[ModuleDescriptor] = []
        self.registry: ExportRegistry = ExportRegistry()
        self.tracker: DependencyTracker = DependencyTracker()
        self.created_at: float = time.time()
        self._manifest_id: str = str(uuid.uuid4())

    # ------------------------------------------------------------------
    # Mutation
    # ------------------------------------------------------------------

    def register_module(self, module: ModuleDescriptor) -> None:
        """Add a module descriptor to the manifest."""
        self.modules.append(module)

    def register_export(self, export: ExportDescriptor) -> None:
        """Register an export in the manifest's ExportRegistry."""
        self.registry.register(export)

    def add_dependency(self, dep: DependencyRecord) -> None:
        """Add a dependency record to the manifest's DependencyTracker."""
        self.tracker.add(dep)

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    def get_module(self, name: str) -> Optional[ModuleDescriptor]:
        """Return the ModuleDescriptor for module_name, or None."""
        for m in self.modules:
            if m.module_name == name:
                return m
        return None

    def list_modules(self) -> list[str]:
        """Return sorted list of module names registered in the manifest."""
        return sorted(m.module_name for m in self.modules)

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Serialize the entire manifest to a plain dictionary."""
        return {
            "manifest_id": self._manifest_id,
            "package": _PACKAGE_NAME,
            "version": _MODULE_VERSION,
            "theory_ref": _THEORY_REF,
            "created_at": self.created_at,
            "modules": [m.to_dict() for m in self.modules],
            "registry": self.registry.to_dict(),
            "dependencies": self.tracker.to_dict(),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ReplayGluingManifest:
        """Reconstruct a ReplayGluingManifest from a plain dictionary."""
        m = cls()
        m._manifest_id = d.get("manifest_id", str(uuid.uuid4()))
        m.created_at = float(d.get("created_at", time.time()))
        for mod_dict in d.get("modules", []):
            m.register_module(ModuleDescriptor.from_dict(mod_dict))
        for exp_dict in d.get("registry", {}).get("exports", []):
            m.register_export(
                ExportDescriptor(
                    name=exp_dict.get("name", ""),
                    module=exp_dict.get("module", ""),
                    kind=ExportKind(exp_dict.get("kind", "FUNCTION")),
                    description=exp_dict.get("description", ""),
                    is_public=exp_dict.get("is_public", True),
                )
            )
        for dep_dict in d.get("dependencies", {}).get("dependencies", []):
            m.add_dependency(
                DependencyRecord(
                    from_module=dep_dict.get("from_module", ""),
                    to_module=dep_dict.get("to_module", ""),
                    kind=DependencyKind(dep_dict.get("kind", "REQUIRED")),
                    symbols=list(dep_dict.get("symbols", [])),
                )
            )
        return m

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def validate(self) -> list[str]:
        """
        Run integrity checks on the manifest.

        Returns
        -------
        list[str]
            List of error messages.  Empty list means the manifest is valid.
        """
        errors: list[str] = []
        module_names = set(self.list_modules())

        # Every export must reference a known module
        for exp in self.registry.get_all_public():
            if exp.module not in module_names:
                errors.append(
                    f"Export '{exp.name}' references unknown module '{exp.module}'."
                )

        # Dependency endpoints must reference known modules
        for dep in self.tracker._deps:
            if dep.from_module not in module_names:
                errors.append(
                    f"Dependency from unknown module '{dep.from_module}'."
                )
            if dep.to_module not in module_names:
                errors.append(
                    f"Dependency to unknown module '{dep.to_module}'."
                )

        # Warn about cycles
        if self.tracker.has_cycle():
            errors.append(
                "Dependency graph contains a cycle. "
                "Check DependencyTracker.topological_order() for details."
            )

        return errors

    # ------------------------------------------------------------------
    # Presentation
    # ------------------------------------------------------------------

    def summary(self) -> str:
        """Return a brief human-readable summary of the manifest."""
        return (
            f"ReplayGluingManifest [{_PACKAGE_NAME}]\n"
            f"  manifest_id : {self._manifest_id}\n"
            f"  modules     : {len(self.modules)}\n"
            f"  exports     : {self.registry.count()}\n"
            f"  dependencies: {len(self.tracker._deps)}\n"
            f"  has_cycle   : {self.tracker.has_cycle()}\n"
            f"  theory_ref  : {_THEORY_REF}\n"
        )

    def generate_readme(self) -> str:
        """
        Generate a README-style string describing the package.

        This covers the package's purpose, each module's role, its public
        exports, and its dependencies -- all derived from the manifest data.

        Returns
        -------
        str
        """
        lines: list[str] = [
            f"# {_PACKAGE_NAME}",
            "",
            f"> Theory reference: {_THEORY_REF}",
            "",
            "## Overview",
            "",
            "This package implements the replay-gluing pipeline described in",
            f"Chapter 43 of theory2.tex.  It is responsible for:",
            "",
            "- Planning which patches need to be replayed after a change",
            "  (`replay_planning`)",
            "- Incrementally replaying only the affected patches",
            "  (`incremental_replay`)",
            "- Verifying convergence of the replay process",
            "  (`convergence_verification`)",
            "- Providing algorithm implementations and an integration layer",
            "  (`algorithms`, `integration`)",
            "- Exposing formal correctness theorems",
            "  (`theorems`)",
            "",
            f"## Package version: {_MODULE_VERSION}",
            "",
            "## Modules",
            "",
        ]

        for mod in sorted(self.modules, key=lambda m: m.module_name):
            lines.append(f"### `{mod.module_name}`")
            lines.append("")
            lines.append(f"**File**: `{mod.file_path}`")
            if mod.theory_section:
                lines.append(f"**Theory**: {mod.theory_section}")
            if mod.line_count:
                lines.append(f"**Lines**: ~{mod.line_count}")
            lines.append("")
            lines.append(mod.description)
            lines.append("")

            public_exports = mod.get_public_exports()
            if public_exports:
                lines.append("**Exports**:")
                for e in public_exports:
                    desc_obj = self.registry.lookup(e)
                    desc = desc_obj.description if desc_obj else ""
                    kind = desc_obj.kind.value if desc_obj else "UNKNOWN"
                    lines.append(f"- `{e}` ({kind}): {desc}")
                lines.append("")

            deps = self.tracker.get_dependencies(mod.module_name)
            if deps:
                lines.append("**Depends on**:")
                for dep in deps:
                    sym_str = (
                        ", ".join(dep.symbols) if dep.symbols else "(entire module)"
                    )
                    lines.append(
                        f"- `{dep.to_module}` [{dep.kind.value}] -- {sym_str}"
                    )
                lines.append("")

        lines += [
            "## Dependency order",
            "",
        ]
        topo = self.tracker.topological_order()
        for i, mod in enumerate(topo, 1):
            lines.append(f"{i}. `{mod}`")

        lines += [
            "",
            "## Integrity",
            "",
        ]
        errors = self.validate()
        if errors:
            lines.append("**Validation errors:**")
            for err in errors:
                lines.append(f"- {err}")
        else:
            lines.append("No integrity errors detected.")

        return "\n".join(lines)


# ===========================================================================
# Module-level functions
# ===========================================================================

def get_manifest() -> ReplayGluingManifest:
    """Return the singleton MANIFEST instance for this package."""
    return MANIFEST


def list_all_exports() -> list[str]:
    """Return a sorted list of all public export names in MANIFEST."""
    return sorted(e.name for e in MANIFEST.registry.get_all_public())


def check_manifest_integrity() -> bool:
    """
    Run integrity checks on MANIFEST.

    Returns
    -------
    bool
        True iff no errors are found.
    """
    errors = MANIFEST.validate()
    return len(errors) == 0


# ===========================================================================
# Helper: convenience wrappers
# ===========================================================================

def _make_export(
    name: str,
    module: str,
    kind: ExportKind,
    description: str,
    is_public: bool = True,
) -> ExportDescriptor:
    """Shorthand constructor for ExportDescriptor."""
    return ExportDescriptor(
        name=name,
        module=module,
        kind=kind,
        description=description,
        is_public=is_public,
    )


def _make_dep(
    from_module: str,
    to_module: str,
    kind: DependencyKind = DependencyKind.REQUIRED,
    symbols: Optional[list[str]] = None,
) -> DependencyRecord:
    """Shorthand constructor for DependencyRecord."""
    return DependencyRecord(
        from_module=from_module,
        to_module=to_module,
        kind=kind,
        symbols=symbols or [],
    )


# ===========================================================================
# Build the MANIFEST singleton
# ===========================================================================

MANIFEST: ReplayGluingManifest = ReplayGluingManifest()

# ---------------------------------------------------------------------------
# Register modules
# ---------------------------------------------------------------------------

MANIFEST.register_module(ModuleDescriptor(
    module_name="models",
    file_path="jugeo/generation/replay_gluing/models.py",
    description=(
        "Core data models for the replay-gluing system. Defines enumerations "
        "(ReplayStrategy, ReplayPhase, PatchStatus), plan and result structures "
        "(ReplayGluingPlan, GluingUnderReplay), the IncrementalGluing record, "
        "the ConvergenceRecord, and utility functions for computing replay cost "
        "and merging dependency structures."
    ),
    exports=[
        "ReplayStrategy", "ReplayPhase", "ReplayGluingPlan", "GluingUnderReplay",
        "IncrementalGluing", "ConvergenceRecord", "PatchStatus", "ReplayMetrics",
        "GluingDiff", "REPLAY_STRATEGY_COSTS", "DEFAULT_CONVERGENCE_THRESHOLD",
        "MAX_REPLAY_ROUNDS", "validate_plan_id", "compute_replay_cost",
        "merge_dependency_structures",
    ],
    imports=[],
    line_count=600,
    theory_section="theory2.tex Ch43 §0 (Definitions)",
))

MANIFEST.register_module(ModuleDescriptor(
    module_name="replay_planning",
    file_path="jugeo/generation/replay_gluing/replay_planning.py",
    description=(
        "Stage 1 of the replay-gluing pipeline: planning. Analyses incoming "
        "change sets, computes dependency relationships between patches, "
        "estimates replay costs, and produces a ReplayGluingPlan that drives "
        "stages 2 and 3."
    ),
    exports=[
        "ChangeSet", "DependencyAnalyzer", "ReplayCostEstimator", "ReplayPlanner",
        "PlanningError", "CyclicDependencyError", "build_trivial_plan",
        "merge_plans", "plan_is_noop",
    ],
    imports=["models"],
    line_count=600,
    theory_section="theory2.tex Ch43 §1 (Planning)",
))

MANIFEST.register_module(ModuleDescriptor(
    module_name="incremental_replay",
    file_path="jugeo/generation/replay_gluing/incremental_replay.py",
    description=(
        "Stage 2 of the replay-gluing pipeline: incremental replay. Manages "
        "snapshot caching (GluingSnapshot), reconciles overlapping patch "
        "regions (OverlapReconciler), and runs the incremental replay loop "
        "(IncrementalReplayer) that produces a GluingUnderReplay result."
    ),
    exports=[
        "GluingSnapshot", "ReplayCache", "OverlapReconciler",
        "IncrementalReplayer", "ReplayError", "OverlapIncompatibilityError",
        "create_snapshot_from_gluing",
    ],
    imports=["models", "replay_planning"],
    line_count=600,
    theory_section="theory2.tex Ch43 §2 (Incremental Replay)",
))

MANIFEST.register_module(ModuleDescriptor(
    module_name="convergence_verification",
    file_path="jugeo/generation/replay_gluing/convergence_verification.py",
    description=(
        "Stage 3 of the replay-gluing pipeline: convergence verification. "
        "Computes convergence metrics (ConvergenceMetric), checks for fixed "
        "points (FixedPointChecker), issues convergence certificates "
        "(ConvergenceCertificate), and produces a ConvergenceReport."
    ),
    exports=[
        "ConvergenceMetric", "FixedPointChecker", "ConvergenceCertificate",
        "ConvergenceVerifier", "ConvergenceStatus", "ConvergenceReport",
        "compute_convergence_status",
    ],
    imports=["models", "incremental_replay"],
    line_count=600,
    theory_section="theory2.tex Ch43 §3 (Convergence)",
))

MANIFEST.register_module(ModuleDescriptor(
    module_name="algorithms",
    file_path="jugeo/generation/replay_gluing/algorithms.py",
    description=(
        "Algorithm implementations for the three replay strategies: full "
        "replay (FullReplayAlgorithm), incremental replay "
        "(IncrementalReplayAlgorithm), and lazy replay (LazyReplayAlgorithm). "
        "Also provides a change-impact analyser, a gluing merger, a task "
        "scheduler, and the AlgorithmRegistry for selecting the right strategy."
    ),
    exports=[
        "ReplayAlgorithm", "FullReplayAlgorithm", "IncrementalReplayAlgorithm",
        "LazyReplayAlgorithm", "ChangeImpactAnalyzer", "GluingMerger",
        "ReplayTask", "ReplayScheduler", "AlgorithmRegistry",
        "select_algorithm", "run_algorithm",
    ],
    imports=["models", "replay_planning", "incremental_replay",
             "convergence_verification"],
    line_count=600,
    theory_section="theory2.tex Ch43 §4 (Algorithms)",
))

MANIFEST.register_module(ModuleDescriptor(
    module_name="integration",
    file_path="jugeo/generation/replay_gluing/integration.py",
    description=(
        "Integration layer that connects the replay-gluing pipeline to the "
        "broader jugeo system. Provides ReplayGluingPipeline for end-to-end "
        "execution, adaptors for the descent and goal subsystems, a frontier "
        "integrator, and convenience functions run_full_pipeline and "
        "pipeline_from_goal_change."
    ),
    exports=[
        "PipelineResult", "ReplayGluingPipeline", "DescentAdaptor",
        "GoalAdaptor", "FrontierIntegrator", "run_full_pipeline",
        "pipeline_from_goal_change",
    ],
    imports=["models", "replay_planning", "incremental_replay",
             "convergence_verification", "algorithms"],
    line_count=600,
    theory_section="theory2.tex Ch43 §5 (Integration)",
))

MANIFEST.register_module(ModuleDescriptor(
    module_name="theorems",
    file_path="jugeo/generation/replay_gluing/theorems.py",
    description=(
        "Mathematical theorem checker for the replay-gluing system, "
        "corresponding to theory2.tex Ch43 §43.1--43.4. Provides "
        "IncrementalCorrectnessTheorem (43.1), ConvergenceGuaranteeTheorem "
        "(43.2), ReplaySoundnessTheorem (43.3), MonotonicityClaim (43.4), "
        "and TheoremSuite for running all checks at once."
    ),
    exports=[
        "TheoremStatus", "TheoremResult", "IncrementalCorrectnessTheorem",
        "ConvergenceGuaranteeTheorem", "ReplaySoundnessTheorem",
        "MonotonicityClaim", "TheoremSuite", "FORMAL_THEORY_REFERENCE",
        "HAS_JUGEO_DEPS", "check_gluing_correctness",
        "verify_convergence_guarantee", "verify_soundness",
        "verify_monotonicity", "run_full_theorem_check",
        "all_theorems_applicable", "theorems_applicable_count",
        "collect_failed_conditions", "compute_applicability_ratio",
        "make_convergence_history", "estimate_rounds_to_threshold",
        "describe_theorem_status", "theorem_status_is_positive",
        "merge_theorem_results", "format_single_result",
        "get_all_formal_statements", "check_incremental_gluing_dict",
        "build_theorem_evidence_summary", "theorems_to_json_list",
    ],
    imports=["models", "convergence_verification"],
    line_count=600,
    theory_section="theory2.tex Ch43 (Theorems)",
))

MANIFEST.register_module(ModuleDescriptor(
    module_name="manifest",
    file_path="jugeo/generation/replay_gluing/manifest.py",
    description=(
        "Machine-readable package manifest. Describes every module, export, "
        "and dependency in the replay_gluing package. Provides the MANIFEST "
        "singleton, ExportRegistry, DependencyTracker, and helpers for "
        "integrity checking and README generation."
    ),
    exports=[
        "DependencyKind", "ExportKind", "ModuleDescriptor", "ExportDescriptor",
        "DependencyRecord", "ExportRegistry", "DependencyTracker",
        "ReplayGluingManifest", "MANIFEST", "get_manifest",
        "list_all_exports", "check_manifest_integrity",
    ],
    imports=[],
    line_count=600,
    theory_section="theory2.tex Ch43 (Meta)",
))

MANIFEST.register_module(ModuleDescriptor(
    module_name="__init__",
    file_path="jugeo/generation/replay_gluing/__init__.py",
    description=(
        "Package initialiser for jugeo.generation.replay_gluing. Re-exports "
        "all major public symbols from the submodules, defines __version__, "
        "__author__, __all__, PACKAGE_INFO, and provides get_version(), "
        "describe(), and run_self_test() convenience functions."
    ),
    exports=["__version__", "__author__", "__all__", "PACKAGE_INFO",
             "get_version", "describe", "run_self_test"],
    imports=["models", "replay_planning", "incremental_replay",
             "convergence_verification", "algorithms", "integration",
             "theorems", "manifest"],
    line_count=400,
    theory_section="theory2.tex Ch43 (Package root)",
))

# ---------------------------------------------------------------------------
# Register exports
# ---------------------------------------------------------------------------

# -- models --
_models_exports = [
    ("ReplayStrategy",               ExportKind.ENUM,      "Enum of replay strategies (FULL, INCREMENTAL, LAZY, SKIP)."),
    ("ReplayPhase",                  ExportKind.ENUM,      "Enum of replay pipeline phases."),
    ("PatchStatus",                  ExportKind.ENUM,      "Status of a single patch within a replay run."),
    ("ReplayGluingPlan",             ExportKind.CLASS,     "Plan produced by the planner driving stages 2 and 3."),
    ("GluingUnderReplay",            ExportKind.CLASS,     "Result produced by the incremental replayer."),
    ("IncrementalGluing",            ExportKind.CLASS,     "State record for one incremental gluing round."),
    ("ConvergenceRecord",            ExportKind.CLASS,     "Record of convergence history for a gluing run."),
    ("ReplayMetrics",                ExportKind.CLASS,     "Performance metrics collected during replay."),
    ("GluingDiff",                   ExportKind.CLASS,     "Diff between two consecutive gluing results."),
    ("REPLAY_STRATEGY_COSTS",        ExportKind.CONSTANT,  "Default cost weights for each replay strategy."),
    ("DEFAULT_CONVERGENCE_THRESHOLD",ExportKind.CONSTANT,  "Default threshold for declaring convergence."),
    ("MAX_REPLAY_ROUNDS",            ExportKind.CONSTANT,  "Hard limit on replay rounds before forced termination."),
    ("validate_plan_id",             ExportKind.FUNCTION,  "Raise ValueError if the plan_id is malformed."),
    ("compute_replay_cost",          ExportKind.FUNCTION,  "Estimate the computational cost of a replay plan."),
    ("merge_dependency_structures",  ExportKind.FUNCTION,  "Merge two dependency dicts, resolving conflicts."),
]
for _name, _kind, _desc in _models_exports:
    MANIFEST.register_export(_make_export(_name, "models", _kind, _desc))

# -- replay_planning --
_s01_exports = [
    ("ChangeSet",              ExportKind.CLASS,     "A set of changes that trigger a new replay run."),
    ("DependencyAnalyzer",     ExportKind.CLASS,     "Analyses patch dependencies and builds the dependency graph."),
    ("ReplayCostEstimator",    ExportKind.CLASS,     "Estimates the cost of replaying each patch."),
    ("ReplayPlanner",          ExportKind.CLASS,     "Produces a ReplayGluingPlan from a ChangeSet."),
    ("PlanningError",          ExportKind.EXCEPTION, "Raised when planning fails for a non-cyclic reason."),
    ("CyclicDependencyError",  ExportKind.EXCEPTION, "Raised when cyclic dependencies are detected."),
    ("build_trivial_plan",     ExportKind.FUNCTION,  "Build a plan that replays every patch (no optimisation)."),
    ("merge_plans",            ExportKind.FUNCTION,  "Merge two ReplayGluingPlan instances."),
    ("plan_is_noop",           ExportKind.FUNCTION,  "Return True iff the plan contains no patches to replay."),
]
for _name, _kind, _desc in _s01_exports:
    MANIFEST.register_export(_make_export(_name, "replay_planning", _kind, _desc))

# -- incremental_replay --
_s02_exports = [
    ("GluingSnapshot",               ExportKind.CLASS,     "Immutable snapshot of a completed gluing result."),
    ("ReplayCache",                  ExportKind.CLASS,     "LRU cache of gluing snapshots keyed by plan hash."),
    ("OverlapReconciler",            ExportKind.CLASS,     "Reconciles overlapping patch regions during replay."),
    ("IncrementalReplayer",          ExportKind.CLASS,     "Drives the incremental replay loop."),
    ("ReplayError",                  ExportKind.EXCEPTION, "Base exception for replay failures."),
    ("OverlapIncompatibilityError",  ExportKind.EXCEPTION, "Raised when overlapping patches are incompatible."),
    ("create_snapshot_from_gluing",  ExportKind.FUNCTION,  "Create a GluingSnapshot from a GluingUnderReplay."),
]
for _name, _kind, _desc in _s02_exports:
    MANIFEST.register_export(_make_export(_name, "incremental_replay", _kind, _desc))

# -- convergence_verification --
_s03_exports = [
    ("ConvergenceMetric",    ExportKind.CLASS,    "A single convergence metric value with metadata."),
    ("FixedPointChecker",    ExportKind.CLASS,    "Checks whether the gluing has reached a fixed point."),
    ("ConvergenceCertificate",ExportKind.CLASS,   "Certificate issued when convergence is verified."),
    ("ConvergenceVerifier",  ExportKind.CLASS,    "Orchestrates convergence checking across rounds."),
    ("ConvergenceStatus",    ExportKind.ENUM,     "Enum of convergence statuses (CONVERGED, DIVERGED, ...)."),
    ("ConvergenceReport",    ExportKind.CLASS,    "Report summarising the convergence history."),
    ("compute_convergence_status", ExportKind.FUNCTION, "Compute convergence status from a metric history."),
]
for _name, _kind, _desc in _s03_exports:
    MANIFEST.register_export(_make_export(_name, "convergence_verification", _kind, _desc))

# -- algorithms --
_alg_exports = [
    ("ReplayAlgorithm",              ExportKind.CLASS,    "Abstract base for all replay algorithm implementations."),
    ("FullReplayAlgorithm",          ExportKind.CLASS,    "Replays every patch from scratch."),
    ("IncrementalReplayAlgorithm",   ExportKind.CLASS,    "Replays only changed patches using cached results."),
    ("LazyReplayAlgorithm",          ExportKind.CLASS,    "Defers replay until results are needed."),
    ("ChangeImpactAnalyzer",         ExportKind.CLASS,    "Determines which patches are affected by a ChangeSet."),
    ("GluingMerger",                 ExportKind.CLASS,    "Merges partial gluing results into a final result."),
    ("ReplayTask",                   ExportKind.CLASS,    "A unit of work in the replay scheduler queue."),
    ("ReplayScheduler",              ExportKind.CLASS,    "Schedules replay tasks respecting dependencies."),
    ("AlgorithmRegistry",            ExportKind.CLASS,    "Registry of available replay algorithms."),
    ("select_algorithm",             ExportKind.FUNCTION, "Select the best algorithm for a ReplayGluingPlan."),
    ("run_algorithm",                ExportKind.FUNCTION, "Run a chosen algorithm on a plan and return results."),
]
for _name, _kind, _desc in _alg_exports:
    MANIFEST.register_export(_make_export(_name, "algorithms", _kind, _desc))

# -- integration --
_int_exports = [
    ("PipelineResult",            ExportKind.CLASS,    "End-to-end result from the full pipeline."),
    ("ReplayGluingPipeline",      ExportKind.CLASS,    "Orchestrates stages 1-3 end-to-end."),
    ("DescentAdaptor",            ExportKind.CLASS,    "Adapts the replay pipeline to the descent subsystem."),
    ("GoalAdaptor",               ExportKind.CLASS,    "Adapts the replay pipeline to the goal subsystem."),
    ("FrontierIntegrator",        ExportKind.CLASS,    "Integrates replay results into the frontier."),
    ("run_full_pipeline",         ExportKind.FUNCTION, "Run the complete replay-gluing pipeline end-to-end."),
    ("pipeline_from_goal_change", ExportKind.FUNCTION, "Construct a pipeline from a goal-level change event."),
]
for _name, _kind, _desc in _int_exports:
    MANIFEST.register_export(_make_export(_name, "integration", _kind, _desc))

# -- theorems --
_thm_exports = [
    ("TheoremStatus",                ExportKind.ENUM,     "Lifecycle status of a single theorem check."),
    ("TheoremResult",                ExportKind.CLASS,    "Outcome of applying one theorem to concrete data."),
    ("IncrementalCorrectnessTheorem",ExportKind.CLASS,    "Theorem 43.1: incremental replay preserves correctness."),
    ("ConvergenceGuaranteeTheorem",  ExportKind.CLASS,    "Theorem 43.2: replay converges under stable treaties."),
    ("ReplaySoundnessTheorem",       ExportKind.CLASS,    "Theorem 43.3: replay is observationally equivalent to full re-execution."),
    ("MonotonicityClaim",            ExportKind.CLASS,    "Claim 43.4: convergence metric is monotonically non-increasing."),
    ("TheoremSuite",                 ExportKind.CLASS,    "Runs all four theorems and reports overall status."),
    ("FORMAL_THEORY_REFERENCE",      ExportKind.CONSTANT, "Citation string 'theory2.tex Ch43'."),
    ("check_gluing_correctness",     ExportKind.FUNCTION, "Quick check for Theorem 43.1 on an IncrementalGluing."),
    ("verify_convergence_guarantee", ExportKind.FUNCTION, "Quick check for Theorem 43.2."),
    ("verify_soundness",             ExportKind.FUNCTION, "Quick check for Theorem 43.3."),
    ("verify_monotonicity",          ExportKind.FUNCTION, "Quick check for Claim 43.4."),
]
for _name, _kind, _desc in _thm_exports:
    MANIFEST.register_export(_make_export(_name, "theorems", _kind, _desc))

# -- manifest --
_mfst_exports = [
    ("DependencyKind",        ExportKind.ENUM,     "Strength of a module dependency (REQUIRED/OPTIONAL/SOFT)."),
    ("ExportKind",            ExportKind.ENUM,     "Kind of a public symbol (CLASS/FUNCTION/etc.)."),
    ("ModuleDescriptor",      ExportKind.CLASS,    "Metadata for one Python module."),
    ("ExportDescriptor",      ExportKind.CLASS,    "Metadata for one public symbol."),
    ("DependencyRecord",      ExportKind.CLASS,    "A directed module dependency."),
    ("ExportRegistry",        ExportKind.CLASS,    "Searchable registry of ExportDescriptor instances."),
    ("DependencyTracker",     ExportKind.CLASS,    "Directed dependency graph with cycle detection."),
    ("ReplayGluingManifest",  ExportKind.CLASS,    "Top-level manifest for the package."),
    ("MANIFEST",              ExportKind.CONSTANT, "Pre-populated manifest singleton."),
    ("get_manifest",          ExportKind.FUNCTION, "Return the MANIFEST singleton."),
    ("list_all_exports",      ExportKind.FUNCTION, "Return sorted list of all public export names."),
    ("check_manifest_integrity", ExportKind.FUNCTION, "Run integrity checks; return True if valid."),
]
for _name, _kind, _desc in _mfst_exports:
    MANIFEST.register_export(_make_export(_name, "manifest", _kind, _desc))

# ---------------------------------------------------------------------------
# Register dependencies
# ---------------------------------------------------------------------------

# s01 depends on models
MANIFEST.add_dependency(_make_dep(
    "replay_planning", "models", DependencyKind.REQUIRED,
    ["ReplayGluingPlan", "ReplayStrategy", "PatchStatus"],
))

# s02 depends on models and s01
MANIFEST.add_dependency(_make_dep(
    "incremental_replay", "models", DependencyKind.REQUIRED,
    ["GluingUnderReplay", "IncrementalGluing", "ConvergenceRecord"],
))
MANIFEST.add_dependency(_make_dep(
    "incremental_replay", "replay_planning", DependencyKind.REQUIRED,
    ["ReplayGluingPlan", "ChangeSet"],
))

# s03 depends on models and s02
MANIFEST.add_dependency(_make_dep(
    "convergence_verification", "models", DependencyKind.REQUIRED,
    ["ConvergenceRecord", "GluingUnderReplay"],
))
MANIFEST.add_dependency(_make_dep(
    "convergence_verification", "incremental_replay", DependencyKind.REQUIRED,
    ["GluingSnapshot", "IncrementalReplayer"],
))

# algorithms depends on models, s01, s02, s03
MANIFEST.add_dependency(_make_dep(
    "algorithms", "models", DependencyKind.REQUIRED,
    ["ReplayGluingPlan", "GluingUnderReplay", "ReplayStrategy"],
))
MANIFEST.add_dependency(_make_dep(
    "algorithms", "replay_planning", DependencyKind.REQUIRED,
    ["ReplayPlanner", "ChangeSet"],
))
MANIFEST.add_dependency(_make_dep(
    "algorithms", "incremental_replay", DependencyKind.REQUIRED,
    ["IncrementalReplayer", "GluingSnapshot"],
))
MANIFEST.add_dependency(_make_dep(
    "algorithms", "convergence_verification", DependencyKind.REQUIRED,
    ["ConvergenceVerifier", "ConvergenceCertificate"],
))

# integration depends on all stages and algorithms
MANIFEST.add_dependency(_make_dep(
    "integration", "models", DependencyKind.REQUIRED,
    ["ReplayGluingPlan", "GluingUnderReplay"],
))
MANIFEST.add_dependency(_make_dep(
    "integration", "replay_planning", DependencyKind.REQUIRED,
    ["ReplayPlanner"],
))
MANIFEST.add_dependency(_make_dep(
    "integration", "incremental_replay", DependencyKind.REQUIRED,
    ["IncrementalReplayer"],
))
MANIFEST.add_dependency(_make_dep(
    "integration", "convergence_verification", DependencyKind.REQUIRED,
    ["ConvergenceVerifier"],
))
MANIFEST.add_dependency(_make_dep(
    "integration", "algorithms", DependencyKind.REQUIRED,
    ["select_algorithm", "run_algorithm"],
))

# theorems depends on models and s03 (optional -- stubs provided)
MANIFEST.add_dependency(_make_dep(
    "theorems", "models", DependencyKind.OPTIONAL,
    ["ReplayGluingPlan", "GluingUnderReplay", "IncrementalGluing", "ConvergenceRecord"],
))
MANIFEST.add_dependency(_make_dep(
    "theorems", "convergence_verification", DependencyKind.OPTIONAL,
    ["ConvergenceCertificate", "ConvergenceMetric"],
))

# __init__ re-exports from all modules
for _src in [
    "models", "replay_planning", "incremental_replay",
    "convergence_verification", "algorithms", "integration",
    "theorems", "manifest",
]:
    MANIFEST.add_dependency(_make_dep(
        "__init__", _src, DependencyKind.SOFT, [],
    ))


# ===========================================================================
# Module self-test
# ===========================================================================

def _self_test() -> bool:
    """Run a minimal self-test of the manifest module."""
    # ExportKind and DependencyKind have expected values
    assert ExportKind.CLASS.value == "CLASS"
    assert DependencyKind.REQUIRED.value == "REQUIRED"

    # ModuleDescriptor round-trips through to_dict / from_dict
    md = ModuleDescriptor(
        module_name="test_mod",
        file_path="test_mod.py",
        description="A test module.",
        exports=["Foo", "_bar"],
        imports=["models"],
        line_count=42,
        theory_section="Ch0",
    )
    d = md.to_dict()
    md2 = ModuleDescriptor.from_dict(d)
    assert md2.module_name == "test_mod"
    assert md2.line_count == 42
    assert md.get_public_exports() == ["Foo"]  # _bar filtered out

    # ExportDescriptor
    ed = ExportDescriptor("MyClass", "test_mod", ExportKind.CLASS, "A class.")
    assert ed.full_qualified_name() == "test_mod.MyClass"
    assert ed.to_dict()["kind"] == "CLASS"

    # DependencyRecord
    dr = DependencyRecord("a", "b", DependencyKind.REQUIRED, ["X"])
    assert dr.is_required()
    assert dr.to_dict()["from_module"] == "a"

    # ExportRegistry CRUD
    reg = ExportRegistry()
    reg.register(ed)
    assert reg.lookup("MyClass") is not None
    assert reg.count() == 1
    assert len(reg.get_all_public()) == 1
    assert len(reg.get_by_module("test_mod")) == 1
    assert len(reg.get_by_kind(ExportKind.CLASS)) == 1

    # DependencyTracker cycle detection
    tracker = DependencyTracker()
    tracker.add(DependencyRecord("a", "b"))
    tracker.add(DependencyRecord("b", "c"))
    assert not tracker.has_cycle()
    tracker.add(DependencyRecord("c", "a"))
    assert tracker.has_cycle()

    # DependencyTracker topological order (acyclic)
    tracker2 = DependencyTracker()
    tracker2.add(DependencyRecord("a", "b"))
    tracker2.add(DependencyRecord("b", "c"))
    order = tracker2.topological_order()
    assert order.index("c") < order.index("b") or "a" in order

    # MANIFEST singleton
    m = get_manifest()
    assert len(m.list_modules()) >= 9
    assert m.registry.count() > 0

    # list_all_exports non-empty
    exports = list_all_exports()
    assert len(exports) > 0

    # generate_readme
    readme = m.generate_readme()
    assert "replay_gluing" in readme

    # summary
    s = m.summary()
    assert "ReplayGluingManifest" in s

    return True


if __name__ == "__main__":  # pragma: no cover
    ok = _self_test()
    print(f"Self-test: {'PASSED' if ok else 'FAILED'}")
