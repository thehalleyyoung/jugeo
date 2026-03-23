"""Package manifest for jugeo.generation.hypercover_treaties.

The manifest provides introspective metadata about this package:
which modules it contains, what each module exports, how modules
depend on each other, and what theory2.tex sections each module
implements.

The manifest is used by:
- The JuGeo copilot integration layer to understand package structure
- Documentation generators to produce theory-aligned API docs
- The package __init__.py to build the public API
- Dependency trackers in the orchestration layer

Theory reference: theory2.tex §41.0 (package architecture overview)

Every module in ``jugeo.generation.hypercover_treaties`` corresponds to a
specific subsection of Chapter 41.  The mapping is:

    models.py          §41.1   Core dataclass models and enums
    *.py           §41.2   Hypercover synthesis loop
    *.py           §41.3   Overlap law mining
    *.py           §41.4   Treaty formation from mined laws
    algorithms.py      §41.5   Low-level synthesis algorithms
    integration.py     §41.6   Integration with descent and frontier
    theorems.py        §41.7   Formal theorem verification
    manifest.py        §41.0   Package architecture metadata (this file)

The :func:`build_manifest` factory constructs the complete
:class:`HypercoverTreatiesManifest` and :func:`get_manifest` returns
a cached singleton.

Usage::

    from jugeo.generation.hypercover_treaties.manifest import (
        get_manifest,
        build_manifest,
        HypercoverTreatiesManifest,
        ModuleDescriptor,
        ExportRegistry,
        DependencyTracker,
        ExportKind,
        DependencyKind,
    )

    manifest = get_manifest()
    print(manifest)
    for mod in manifest.stable_modules():
        print(mod.module_name, mod.theory_sections)

copilot: manifest-marker
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

try:
    from jugeo.geometry.descent import (
        DescentEngine, DescentResult, LocalSection, OverlapCondition,
        GluingData, DescentObstruction, RepairFrontier, DescentStrategy,
        OverlapStatus,
    )
    from jugeo.geometry.covers import Cover
    from jugeo.geometry.supports import SupportRegion
    from jugeo.geometry.site import CoordinateObject, CoordinateKind, Coordinate
    from jugeo.generation.goals import (
        GenerationGoal, GoalDecomposer, ConstructionGoal, GoalPriority,
        GoalStatus, OverlapGoal,
    )
    from jugeo.generation.construction import (
        Candidate, ConstructionLoop, ConstructionResult, ConstructionContext,
    )
    from jugeo.generation.treaties import (
        OverlapTreaty, TreatyClause, TreatyStatus, evaluate_treaty,
    )
    from jugeo.orchestration.frontier import FrontierNode, Frontier, FrontierItem
    from jugeo.evidence.trust import TrustTier, TrustLevel
except ImportError:
    pass

try:
    from jugeo.generation.hypercover_treaties.models import (
        HypercoverSynthesisRecord, TreatyCandidate, OverlapLaw, DependentTreaty,
        SynthesisOutcome, SynthesisPhase, LawStability, CandidateSource,
        TreatyRole, OutcomeKind, SynthesisConfig, OverlapLawIndex,
    )
except ImportError:
    pass

logger = logging.getLogger(__name__)

__all__ = [
    # Enums
    "ExportKind",
    "DependencyKind",
    # Dataclasses
    "ModuleDescriptor",
    "HypercoverTreatiesManifest",
    # Classes
    "ExportRegistry",
    "DependencyTracker",
    # Factory functions
    "build_manifest",
    "get_manifest",
]

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class ExportKind(str, Enum):
    """Classification of a package export by its syntactic kind.

    Used in :class:`ModuleDescriptor` and :class:`ExportRegistry` to
    distinguish classes from functions, constants, type aliases, and
    exceptions.  This mirrors the categories used by the theory2.tex
    §41.0 manifest schema.
    """

    CLASS = "class"
    """A Python class (dataclass or regular class)."""

    FUNCTION = "function"
    """A module-level function."""

    CONSTANT = "constant"
    """A module-level constant (all-caps by convention)."""

    TYPE_ALIAS = "type_alias"
    """A ``TypeAlias`` or ``type X = ...`` declaration."""

    EXCEPTION = "exception"
    """An exception class (subclass of ``Exception``)."""


class DependencyKind(str, Enum):
    """Classification of a dependency edge between two modules.

    Used in :class:`DependencyTracker` to annotate the nature of the
    relationship so that documentation generators and the copilot integration
    layer can reason about import order and coupling.
    """

    IMPORTS_FROM = "imports_from"
    """Module A directly imports names from module B."""

    EXTENDS = "extends"
    """Module A defines classes that subclass classes in module B."""

    USES = "uses"
    """Module A calls functions or uses types defined in module B at runtime."""

    REQUIRED_BY = "required_by"
    """Module A must be fully initialised before module B can be loaded."""


# ---------------------------------------------------------------------------
# ModuleDescriptor
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ModuleDescriptor:
    """Frozen metadata record describing a single module in this package.

    Each field corresponds to a column in the theory2.tex §41.0 manifest
    table.  The ``exports`` and ``export_kinds`` tuples are parallel: index
    ``i`` of ``exports`` has kind ``export_kinds[i]``.

    Instances are produced by :func:`build_manifest` and stored in the
    :class:`HypercoverTreatiesManifest`.
    """

    module_name: str
    """Fully-qualified module name, e.g. ``"jugeo.generation.hypercover_treaties.models"``."""

    file_name: str
    """File name within the package directory, e.g. ``"models.py"``."""

    description: str
    """One-sentence description of the module's purpose."""

    theory_sections: tuple[str, ...]
    """theory2.tex section references this module implements, e.g. ``("§41.1",)``."""

    exports: tuple[str, ...]
    """All names this module places in its ``__all__``."""

    export_kinds: tuple[ExportKind, ...]
    """Parallel to *exports* — the :class:`ExportKind` of each export."""

    dependencies: tuple[str, ...]
    """Other module names (short form) this module imports from."""

    is_stable: bool = True
    """False if the module is under active development and may change."""

    version: str = "0.1.0"
    """Module version following semantic versioning."""

    def export_count(self) -> int:
        """Return the number of public names exported by this module."""
        return len(self.exports)

    def has_export(self, name: str) -> bool:
        """Return True if *name* is in this module's ``exports`` tuple."""
        return name in self.exports

    def theory_section_count(self) -> int:
        """Return the number of theory2.tex sections this module implements."""
        return len(self.theory_sections)

    def __repr__(self) -> str:
        return (
            f"ModuleDescriptor({self.module_name!r}, "
            f"exports={len(self.exports)}, "
            f"sections={self.theory_sections!r})"
        )

    def summary(self) -> str:
        """Return a multi-line human-readable summary of this descriptor."""
        lines = [
            f"Module: {self.module_name}",
            f"  File:     {self.file_name}",
            f"  Version:  {self.version}",
            f"  Stable:   {self.is_stable}",
            f"  Theory:   {', '.join(self.theory_sections)}",
            f"  Exports:  {len(self.exports)} names",
            f"  Deps:     {', '.join(self.dependencies) if self.dependencies else 'none'}",
            f"  Desc:     {self.description}",
        ]
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# ExportRegistry
# ---------------------------------------------------------------------------


class ExportRegistry:
    """Registry that maps public names to the module and kind that provides them.

    Built incrementally by :class:`HypercoverTreatiesManifest` from its
    :class:`ModuleDescriptor` list.  Supports lookup by name, filtering by
    kind, and bulk registration from a descriptor.

    Usage::

        reg = ExportRegistry()
        reg.register("HypercoverSynthesisRecord", "models", ExportKind.CLASS)
        reg.register_module(descriptor)
        classes = reg.filter_by_type(ExportKind.CLASS)
        mod = reg.get_module_for("HypercoverSynthesisRecord")
    """

    def __init__(self) -> None:
        """Initialise with an empty registry."""
        self._registry: dict[str, tuple[str, ExportKind]] = {}

    def register(self, name: str, module_name: str, kind: ExportKind) -> None:
        """Register a single export *name* from *module_name* with *kind*.

        If *name* is already registered (from a different module), the new
        registration overwrites the old one and a warning is logged.

        Args:
            name: The exported symbol name.
            module_name: Short or fully-qualified module name.
            kind: The :class:`ExportKind` of this export.
        """
        if name in self._registry and self._registry[name][0] != module_name:
            logger.warning(
                "ExportRegistry: overwriting %r (was from %r, now from %r)",
                name, self._registry[name][0], module_name,
            )
        self._registry[name] = (module_name, kind)

    def register_module(self, descriptor: ModuleDescriptor) -> None:
        """Register all exports from *descriptor* into this registry.

        Iterates over ``descriptor.exports`` and ``descriptor.export_kinds``
        in parallel and calls :meth:`register` for each pair.

        Args:
            descriptor: A :class:`ModuleDescriptor` whose exports should be
                registered.
        """
        for name, kind in zip(descriptor.exports, descriptor.export_kinds):
            self.register(name, descriptor.module_name, kind)

    def get_all(self) -> dict[str, tuple[str, ExportKind]]:
        """Return a copy of the full registry.

        Returns:
            Dict mapping name to ``(module_name, ExportKind)``.
        """
        return dict(self._registry)

    def filter_by_type(self, kind: ExportKind) -> dict[str, tuple[str, ExportKind]]:
        """Return all entries whose :class:`ExportKind` equals *kind*.

        Args:
            kind: The :class:`ExportKind` to filter by.

        Returns:
            Sub-dict of the registry containing only entries of *kind*.
        """
        return {
            name: entry
            for name, entry in self._registry.items()
            if entry[1] == kind
        }

    def get_module_for(self, name: str) -> "str | None":
        """Return the module name that exports *name*, or ``None`` if not found.

        Args:
            name: The exported symbol name to look up.

        Returns:
            The module name string, or ``None``.
        """
        entry = self._registry.get(name)
        return entry[0] if entry is not None else None

    def all_names(self) -> list[str]:
        """Return a sorted list of all registered export names."""
        return sorted(self._registry)

    def __len__(self) -> int:
        return len(self._registry)

    def __repr__(self) -> str:
        counts: dict[str, int] = {}
        for _, (mod, _) in self._registry.items():
            counts[mod] = counts.get(mod, 0) + 1
        summary = ", ".join(f"{mod}:{n}" for mod, n in sorted(counts.items()))
        return f"ExportRegistry({len(self._registry)} exports: {summary})"


# ---------------------------------------------------------------------------
# DependencyTracker
# ---------------------------------------------------------------------------


class DependencyTracker:
    """Tracks inter-module dependencies within the package.

    Internally represents dependencies as a directed graph where an edge
    ``(A, B, kind)`` means "module A depends on module B with relationship
    *kind*".  Supports topological ordering (Kahn's algorithm) and cycle
    detection.

    Usage::

        tracker = DependencyTracker()
        tracker.add_dep("algorithms", "models")
        tracker.add_dep("hypercover_synthesis", "models")
        order = tracker.topological_order()
        has_cycle = tracker.has_cycles()
    """

    def __init__(self) -> None:
        """Initialise with an empty dependency graph."""
        self._graph: dict[str, list[tuple[str, DependencyKind]]] = {}

    def _ensure_node(self, mod: str) -> None:
        """Ensure *mod* appears as a key in the graph."""
        if mod not in self._graph:
            self._graph[mod] = []

    def add_dep(
        self,
        from_mod: str,
        to_mod: str,
        kind: DependencyKind = DependencyKind.IMPORTS_FROM,
    ) -> None:
        """Record that *from_mod* depends on *to_mod* with relationship *kind*.

        If the exact ``(to_mod, kind)`` pair is already recorded for
        *from_mod*, the call is a no-op (idempotent).

        Args:
            from_mod: The dependent module (short name).
            to_mod: The module being depended upon (short name).
            kind: The nature of the dependency.
        """
        self._ensure_node(from_mod)
        self._ensure_node(to_mod)
        entry = (to_mod, kind)
        if entry not in self._graph[from_mod]:
            self._graph[from_mod].append(entry)

    def get_deps(self, mod: str) -> list[tuple[str, DependencyKind]]:
        """Return all direct dependencies of *mod* as ``(target, kind)`` pairs.

        Args:
            mod: The module whose dependencies to retrieve.

        Returns:
            List of ``(target_module, DependencyKind)`` tuples, possibly empty.
        """
        return list(self._graph.get(mod, []))

    def topological_order(self) -> list[str]:
        """Return modules in topological order using Kahn's algorithm.

        A module appears before all modules that depend on it.  If the graph
        contains a cycle, returns a partial ordering and logs a warning.

        Returns:
            List of module names in topological order (dependencies first).
        """
        in_degree: dict[str, int] = {mod: 0 for mod in self._graph}
        for mod, deps in self._graph.items():
            for target, _ in deps:
                in_degree[target] = in_degree.get(target, 0) + 1

        # Ensure all nodes appear in in_degree
        for mod in self._graph:
            if mod not in in_degree:
                in_degree[mod] = 0

        queue: list[str] = sorted(m for m, d in in_degree.items() if d == 0)
        order: list[str] = []

        while queue:
            node = queue.pop(0)
            order.append(node)
            for neighbour, _ in self._graph.get(node, []):
                in_degree[neighbour] -= 1
                if in_degree[neighbour] == 0:
                    queue.append(neighbour)
                    queue.sort()

        if len(order) < len(in_degree):
            logger.warning(
                "DependencyTracker.topological_order: cycle detected; "
                "returning partial order (%d/%d nodes)",
                len(order), len(in_degree),
            )
        return order

    def get_dependents(self, mod: str) -> list[str]:
        """Return all modules that directly depend on *mod*.

        This is the reverse of :meth:`get_deps`: it returns modules that
        list *mod* as a dependency.

        Args:
            mod: The module to find dependents for.

        Returns:
            Sorted list of module names that depend on *mod*.
        """
        dependents = [
            from_mod
            for from_mod, deps in self._graph.items()
            if any(target == mod for target, _ in deps)
        ]
        return sorted(dependents)

    def has_cycles(self) -> bool:
        """Return True if the dependency graph contains at least one cycle.

        Uses the same Kahn topological sort and checks whether the resulting
        order covers all nodes.

        Returns:
            True iff a cycle exists.
        """
        order = self.topological_order()
        return len(order) < len(self._graph)

    def all_modules(self) -> list[str]:
        """Return a sorted list of all modules tracked in the graph."""
        return sorted(self._graph)

    def __repr__(self) -> str:
        edge_count = sum(len(deps) for deps in self._graph.values())
        return (
            f"DependencyTracker("
            f"modules={len(self._graph)}, edges={edge_count})"
        )


# ---------------------------------------------------------------------------
# HypercoverTreatiesManifest
# ---------------------------------------------------------------------------


@dataclass
class HypercoverTreatiesManifest:
    """Complete manifest for the ``jugeo.generation.hypercover_treaties`` package.

    Aggregates the descriptors for all 8 non-init modules, the export
    registry, and the dependency tracker.  Built by :func:`build_manifest`
    and cached by :func:`get_manifest`.

    Theory reference: theory2.tex §41.0 Definition 41.0.1 (package manifest).
    """

    package_name: str = "jugeo.generation.hypercover_treaties"
    package_version: str = "0.1.0"
    theory_chapter: str = "41"
    theory_document: str = "theory2.tex"
    modules: tuple[ModuleDescriptor, ...] = field(default_factory=tuple)
    export_registry: ExportRegistry = field(default_factory=ExportRegistry)
    dependency_tracker: DependencyTracker = field(default_factory=DependencyTracker)
    description: str = (
        "Hypercover treaty synthesis: overlap law mining, treaty formation, "
        "and formal theorem verification for the descent-based generation pipeline."
    )

    def get_module(self, name: str) -> "ModuleDescriptor | None":
        """Return the descriptor for the module with short name *name*.

        Matches against both the short file name (e.g. ``"models"``) and the
        full module name.

        Args:
            name: Short or fully-qualified module name to look up.

        Returns:
            :class:`ModuleDescriptor` if found, else ``None``.
        """
        for mod in self.modules:
            if mod.file_name == name or mod.module_name == name:
                return mod
            if mod.module_name.endswith(f".{name}"):
                return mod
        return None

    def all_exports(self) -> list[str]:
        """Return sorted list of all names exported by any module in this package."""
        return self.export_registry.all_names()

    def stable_modules(self) -> list[ModuleDescriptor]:
        """Return all modules where ``is_stable=True``."""
        return [m for m in self.modules if m.is_stable]

    def theory_coverage(self) -> dict[str, list[str]]:
        """Return a mapping from theory2.tex section to implementing modules.

        Returns:
            Dict where each key is a section reference (e.g. ``"§41.1"``)
            and the value is the list of module names that implement it.
        """
        coverage: dict[str, list[str]] = {}
        for mod in self.modules:
            for section in mod.theory_sections:
                coverage.setdefault(section, []).append(mod.module_name)
        return {k: sorted(v) for k, v in sorted(coverage.items())}

    def total_exports(self) -> int:
        """Return the total number of exported names across all modules."""
        return len(self.export_registry)

    def __repr__(self) -> str:
        return (
            f"HypercoverTreatiesManifest("
            f"package={self.package_name!r}, "
            f"version={self.package_version!r}, "
            f"modules={len(self.modules)}, "
            f"exports={len(self.export_registry)})"
        )

    def __str__(self) -> str:
        lines = [
            f"Package: {self.package_name}  v{self.package_version}",
            f"Theory:  {self.theory_document} Chapter {self.theory_chapter}",
            f"Modules: {len(self.modules)}",
            f"Exports: {self.total_exports()} names",
            f"Stable:  {len(self.stable_modules())} modules",
            "",
            "Theory coverage:",
        ]
        for section, mods in self.theory_coverage().items():
            short = [m.split(".")[-1] for m in mods]
            lines.append(f"  {section:<10} {', '.join(short)}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def build_manifest() -> HypercoverTreatiesManifest:
    """Construct the complete manifest for ``jugeo.generation.hypercover_treaties``.

    Defines all 8 non-init modules with their descriptions, theory sections,
    exports, export kinds, and dependencies.  Registers exports into the
    :class:`ExportRegistry` and records dependencies in the
    :class:`DependencyTracker`.

    Returns:
        A fully-populated :class:`HypercoverTreatiesManifest`.

    Theory reference: theory2.tex §41.0 (package architecture overview).
    """
    PKG = "jugeo.generation.hypercover_treaties"
    CLS = ExportKind.CLASS
    FN = ExportKind.FUNCTION
    CONST = ExportKind.CONSTANT
    ENUM = ExportKind.CLASS  # enums are classes

    # ------------------------------------------------------------------
    # Module 1: models.py — core dataclass models and enumerations
    # ------------------------------------------------------------------
    models_mod = ModuleDescriptor(
        module_name=f"{PKG}.models",
        file_name="models.py",
        description=(
            "Core dataclass models and enumerations for hypercover treaty "
            "synthesis (theory2.tex §41.1)."
        ),
        theory_sections=("§41.1",),
        exports=(
            "HypercoverSynthesisRecord",
            "TreatyCandidate",
            "OverlapLaw",
            "DependentTreaty",
            "SynthesisOutcome",
            "SynthesisConfig",
            "OverlapLawIndex",
            "SynthesisPhase",
            "LawStability",
            "CandidateSource",
            "TreatyRole",
            "OutcomeKind",
            "EMPTY_SYNTHESIS_RECORD",
            "DEFAULT_CONFIG",
        ),
        export_kinds=(
            CLS, CLS, CLS, CLS, CLS, CLS, CLS,
            ENUM, ENUM, ENUM, ENUM, ENUM,
            CONST, CONST,
        ),
        dependencies=(),
        is_stable=True,
        version="0.1.0",
    )

    # ------------------------------------------------------------------
    # Module 2: hypercover_synthesis.py — synthesis loop
    # ------------------------------------------------------------------
    mod = ModuleDescriptor(
        module_name=f"{PKG}.hypercover_synthesis",
        file_name="hypercover_synthesis.py",
        description=(
            "Main hypercover synthesis loop: initialises cover, iterates over "
            "patches, and accumulates overlap laws (theory2.tex §41.2)."
        ),
        theory_sections=("§41.2",),
        exports=(
            "HypercoverSynthesisEngine",
            "SynthesisIteration",
            "SynthesisStep",
            "run_hypercover_synthesis",
            "initialize_synthesis",
            "advance_synthesis",
        ),
        export_kinds=(CLS, CLS, CLS, FN, FN, FN),
        dependencies=("models",),
        is_stable=True,
        version="0.1.0",
    )

    # ------------------------------------------------------------------
    # Module 3: overlap_law_mining.py — law mining
    # ------------------------------------------------------------------
    mod = ModuleDescriptor(
        module_name=f"{PKG}.overlap_law_mining",
        file_name="overlap_law_mining.py",
        description=(
            "Overlap law mining: discovers recurrent behavioural patterns on "
            "patch overlaps and proposes them as candidate laws (theory2.tex §41.3)."
        ),
        theory_sections=("§41.3",),
        exports=(
            "OverlapLawMiner",
            "MiningContext",
            "LawProposal",
            "mine_overlap_laws",
            "score_law_candidate",
            "filter_stable_laws",
            "build_law_index",
        ),
        export_kinds=(CLS, CLS, CLS, FN, FN, FN, FN),
        dependencies=("models",),
        is_stable=True,
        version="0.1.0",
    )

    # ------------------------------------------------------------------
    # Module 4: treaty_formation.py — treaty formation
    # ------------------------------------------------------------------
    mod = ModuleDescriptor(
        module_name=f"{PKG}.treaty_formation",
        file_name="treaty_formation.py",
        description=(
            "Treaty formation: converts stable overlap laws into ratified "
            "OverlapTreaty objects with verified clauses (theory2.tex §41.4)."
        ),
        theory_sections=("§41.4",),
        exports=(
            "TreatyFormationEngine",
            "TreatyNegotiationRecord",
            "ClauseCompatibilityCheck",
            "form_treaties_from_laws",
            "negotiate_clauses",
            "ratify_treaty",
            "check_clause_compatibility",
        ),
        export_kinds=(CLS, CLS, CLS, FN, FN, FN, FN),
        dependencies=("models", "overlap_law_mining"),
        is_stable=True,
        version="0.1.0",
    )

    # ------------------------------------------------------------------
    # Module 5: algorithms.py — synthesis algorithms
    # ------------------------------------------------------------------
    algorithms_mod = ModuleDescriptor(
        module_name=f"{PKG}.algorithms",
        file_name="algorithms.py",
        description=(
            "Low-level synthesis algorithms: patch pair iteration, confidence "
            "scoring, law deduplication, and outcome aggregation (theory2.tex §41.5)."
        ),
        theory_sections=("§41.5",),
        exports=(
            "compute_overlap_confidence",
            "deduplicate_laws",
            "aggregate_synthesis_outcomes",
            "rank_treaty_candidates",
            "patch_pair_iterator",
            "score_candidate_by_trust",
            "merge_law_indices",
        ),
        export_kinds=(FN, FN, FN, FN, FN, FN, FN),
        dependencies=("models",),
        is_stable=True,
        version="0.1.0",
    )

    # ------------------------------------------------------------------
    # Module 6: integration.py — integration with descent and frontier
    # ------------------------------------------------------------------
    integration_mod = ModuleDescriptor(
        module_name=f"{PKG}.integration",
        file_name="integration.py",
        description=(
            "Integration layer: bridges hypercover synthesis with the descent "
            "engine, frontier nodes, and construction goals (theory2.tex §41.6)."
        ),
        theory_sections=("§41.6",),
        exports=(
            "HypercoverTreatyPipeline",
            "DescentBridgeAdapter",
            "FrontierUpdateRecord",
            "run_synthesis_with_descent",
            "update_frontier_from_outcome",
            "build_construction_goals_from_laws",
            "adapt_descent_result",
        ),
        export_kinds=(CLS, CLS, CLS, FN, FN, FN, FN),
        dependencies=(
            "models",
            "hypercover_synthesis",
            "overlap_law_mining",
            "treaty_formation",
            "algorithms",
        ),
        is_stable=True,
        version="0.1.0",
    )

    # ------------------------------------------------------------------
    # Module 7: theorems.py — formal theorem verification
    # ------------------------------------------------------------------
    theorems_mod = ModuleDescriptor(
        module_name=f"{PKG}.theorems",
        file_name="theorems.py",
        description=(
            "Formal theorem verification: encodes and mechanically checks "
            "Theorems T41.1–T41.4 from theory2.tex Chapter 41."
        ),
        theory_sections=("§41.3", "§41.4", "§41.5", "§41.6", "§41.7"),
        exports=(
            "TheoremCondition",
            "TheoremResult",
            "DescentSuccessTheorem",
            "TreatyConsistencyTheorem",
            "HypercoverExistenceTheorem",
            "OverlapLawCompletenessTheorem",
            "TheoremProver",
            "ProofCertificate",
            "generate_proof_certificate",
        ),
        export_kinds=(CLS, CLS, CLS, CLS, CLS, CLS, CLS, CLS, FN),
        dependencies=("models",),
        is_stable=True,
        version="0.1.0",
    )

    # ------------------------------------------------------------------
    # Module 8: manifest.py — package architecture metadata (this file)
    # ------------------------------------------------------------------
    manifest_mod = ModuleDescriptor(
        module_name=f"{PKG}.manifest",
        file_name="manifest.py",
        description=(
            "Package manifest: introspective metadata about module structure, "
            "exports, dependencies, and theory2.tex section coverage (§41.0)."
        ),
        theory_sections=("§41.0",),
        exports=(
            "ExportKind",
            "DependencyKind",
            "ModuleDescriptor",
            "HypercoverTreatiesManifest",
            "ExportRegistry",
            "DependencyTracker",
            "build_manifest",
            "get_manifest",
        ),
        export_kinds=(ENUM, ENUM, CLS, CLS, CLS, CLS, FN, FN),
        dependencies=(),
        is_stable=True,
        version="0.1.0",
    )

    # ------------------------------------------------------------------
    # Assemble modules tuple
    # ------------------------------------------------------------------
    modules = (
        models_mod,
        mod,
        mod,
        mod,
        algorithms_mod,
        integration_mod,
        theorems_mod,
        manifest_mod,
    )

    # ------------------------------------------------------------------
    # Build ExportRegistry
    # ------------------------------------------------------------------
    registry = ExportRegistry()
    for mod in modules:
        registry.register_module(mod)

    # ------------------------------------------------------------------
    # Build DependencyTracker
    # ------------------------------------------------------------------
    tracker = DependencyTracker()
    short_deps: dict[str, tuple[str, ...]] = {
        "models": (),
        "hypercover_synthesis": ("models",),
        "overlap_law_mining": ("models",),
        "treaty_formation": ("models", "overlap_law_mining"),
        "algorithms": ("models",),
        "integration": (
            "models",
            "hypercover_synthesis",
            "overlap_law_mining",
            "treaty_formation",
            "algorithms",
        ),
        "theorems": ("models",),
        "manifest": (),
    }
    for from_mod, deps in short_deps.items():
        for dep in deps:
            tracker.add_dep(from_mod, dep, DependencyKind.IMPORTS_FROM)

    # integration also USES the descent engine (external)
    tracker.add_dep("integration", "descent", DependencyKind.USES)
    # s01 USES the cover geometry
    tracker.add_dep("hypercover_synthesis", "covers", DependencyKind.USES)
    # theorems EXTENDS from local_construction theorems conceptually
    tracker.add_dep("theorems", "local_construction.theorems", DependencyKind.EXTENDS)

    # ------------------------------------------------------------------
    # Construct and return the manifest
    # ------------------------------------------------------------------
    return HypercoverTreatiesManifest(
        package_name=PKG,
        package_version="0.1.0",
        theory_chapter="41",
        theory_document="theory2.tex",
        modules=modules,
        export_registry=registry,
        dependency_tracker=tracker,
        description=(
            "Hypercover treaty synthesis: overlap law mining, treaty formation, "
            "and formal theorem verification for the descent-based generation pipeline. "
            "Implements theory2.tex Chapter 41."
        ),
    )


# ---------------------------------------------------------------------------
# Cached singleton
# ---------------------------------------------------------------------------

_MANIFEST_SINGLETON: "HypercoverTreatiesManifest | None" = None


def get_manifest() -> HypercoverTreatiesManifest:
    """Return the cached package manifest, building it on first call.

    The manifest is built once and cached as a module-level singleton.
    Subsequent calls return the same object without re-building.

    Returns:
        The :class:`HypercoverTreatiesManifest` for this package.

    Example::

        manifest = get_manifest()
        print(manifest.theory_coverage())
        assert manifest.get_module("theorems") is not None
    """
    global _MANIFEST_SINGLETON
    if _MANIFEST_SINGLETON is None:
        _MANIFEST_SINGLETON = build_manifest()
        logger.debug(
            "HypercoverTreatiesManifest built: %d modules, %d exports",
            len(_MANIFEST_SINGLETON.modules),
            _MANIFEST_SINGLETON.total_exports(),
        )
    return _MANIFEST_SINGLETON
