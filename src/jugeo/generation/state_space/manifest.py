"""
State Space Package Manifest for JuGeo Generation System.

# copilot: state-space-manifest

This module documents all modules in the jugeo.generation.state_space package,
provides capability probes, and exports a machine-readable manifest describing
the package's contents, version, and theory references.

The state_space package implements generation-as-section-construction from
theory2.tex, Chapter 40. Generation is modelled as constructing global sections
of a semantic sheaf over a Grothendieck site of coordinates. The state space
captures all intermediate states a generation process can occupy, with
transitions that correspond to sheaf-theoretic operations (local section
proposal, gluing, obstruction recording, descent).

Theory Reference: theory2.tex, Chapter 40.
"""

from __future__ import annotations

import importlib
import logging
import sys
import time
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

__all__ = [
    "StateSpaceManifest",
    "StateSpaceCapability",
    "ExportedSymbol",
    "ModuleDescriptor",
    "CapabilityProbe",
    "build_manifest",
    "list_capabilities",
    "get_exports",
    "VERSION",
    "CHAPTER",
    "PACKAGE_NAME",
    "THEORY_FILE",
]

# ---------------------------------------------------------------------------
# Package constants
# ---------------------------------------------------------------------------

VERSION = "2.0.0"
CHAPTER = 40
PACKAGE_NAME = "jugeo.generation.state_space"
THEORY_FILE = "theory2.tex"

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StateSpaceCapability:
    """A capability provided by the state_space package.

    Each capability corresponds to a theoretical concept from theory2.tex and
    maps to one or more modules that implement it.

    Fields
    ------
    name : str
        Short machine-readable name of the capability.
    description : str
        Human-readable description of what the capability provides.
    theory_section : str
        Section of theory2.tex that motivates this capability.
    is_available : bool
        Whether the capability is available at runtime (imports succeed).
    """

    name: str
    description: str
    theory_section: str
    is_available: bool = True

    def __str__(self) -> str:  # noqa: D105
        status = "✓" if self.is_available else "✗"
        return f"[{status}] {self.name}: {self.description} (§{self.theory_section})"


@dataclass(frozen=True)
class ExportedSymbol:
    """A symbol exported from the state_space package.

    Fields
    ------
    name : str
        The symbol name (class, function, enum, constant).
    module : str
        The module path where the symbol is defined, relative to the package.
    kind : str
        One of 'class', 'function', 'enum', 'constant', 'dataclass'.
    theory_reference : str
        Reference to theory2.tex section / definition number.
    description : str
        Brief description of what the symbol represents or does.
    """

    name: str
    module: str
    kind: str
    theory_reference: str
    description: str

    def qualified_name(self) -> str:
        """Return the fully qualified import path for this symbol."""
        return f"{PACKAGE_NAME}.{self.module}.{self.name}"


@dataclass(frozen=True)
class ModuleDescriptor:
    """Descriptor for a module in the state_space package.

    Fields
    ------
    module_name : str
        The module name (without package prefix).
    full_path : str
        The fully qualified module path.
    description : str
        What this module implements.
    theory_section : str
        Theory reference.
    is_new : bool
        True for the 8 new modules, False for pre-existing ones.
    exported_symbols : tuple[str, ...]
        Top-level symbols exported by this module.
    """

    module_name: str
    full_path: str
    description: str
    theory_section: str
    is_new: bool
    exported_symbols: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class StateSpaceManifest:
    """Machine-readable manifest for the jugeo.generation.state_space package.

    The manifest records all modules, capabilities, and exported symbols. It is
    the authoritative source of truth for what the package contains and what
    theory sections each module corresponds to.

    Fields
    ------
    package_name : str
        Fully qualified package name.
    version : str
        Package version string (semver).
    chapter : int
        Chapter number in theory2.tex this package implements.
    theory_file : str
        Filename of the theory document.
    modules : tuple[ModuleDescriptor, ...]
        All modules in the package.
    capabilities : tuple[StateSpaceCapability, ...]
        All capabilities provided.
    exported_symbols : tuple[ExportedSymbol, ...]
        All exported symbols.
    created_at : float
        Unix timestamp when this manifest was created.
    """

    package_name: str
    version: str
    chapter: int
    theory_file: str
    modules: tuple[ModuleDescriptor, ...]
    capabilities: tuple[StateSpaceCapability, ...]
    exported_symbols: tuple[ExportedSymbol, ...]
    created_at: float

    def get_module(self, name: str) -> Optional[ModuleDescriptor]:
        """Return the descriptor for *name*, or None if not found."""
        for mod in self.modules:
            if mod.module_name == name:
                return mod
        return None

    def get_new_modules(self) -> list[ModuleDescriptor]:
        """Return only the newly-created modules."""
        return [m for m in self.modules if m.is_new]

    def get_legacy_modules(self) -> list[ModuleDescriptor]:
        """Return only the pre-existing modules."""
        return [m for m in self.modules if not m.is_new]

    def capability_names(self) -> list[str]:
        """Return list of all capability names."""
        return [c.name for c in self.capabilities]

    def available_capabilities(self) -> list[StateSpaceCapability]:
        """Return capabilities that are available at runtime."""
        return [c for c in self.capabilities if c.is_available]

    def summary(self) -> dict:
        """Return a summary dict suitable for logging or display."""
        return {
            "package_name": self.package_name,
            "version": self.version,
            "chapter": self.chapter,
            "theory_file": self.theory_file,
            "module_count": len(self.modules),
            "new_module_count": len(self.get_new_modules()),
            "capability_count": len(self.capabilities),
            "available_capability_count": len(self.available_capabilities()),
            "exported_symbol_count": len(self.exported_symbols),
            "created_at": self.created_at,
        }

    def __str__(self) -> str:  # noqa: D105
        s = self.summary()
        return (
            f"StateSpaceManifest(package={s['package_name']}, "
            f"version={s['version']}, "
            f"modules={s['module_count']}, "
            f"capabilities={s['available_capability_count']}/{s['capability_count']})"
        )


# ---------------------------------------------------------------------------
# Capability probe
# ---------------------------------------------------------------------------


class CapabilityProbe:
    """Checks which state_space capabilities are available at runtime.

    Each capability maps to one or more (module, symbol) pairs. The probe
    tries to import each symbol and records whether it succeeds. This is used
    at startup to populate the ``is_available`` field of
    :class:`StateSpaceCapability`.

    Example
    -------
    >>> probe = CapabilityProbe()
    >>> results = probe.probe_all()
    >>> for name, avail in results.items():
    ...     print(name, avail)
    """

    # Map capability name → list of (module_path, symbol_name) pairs to probe
    _PROBES: dict[str, list[tuple[str, str]]] = {
        "section_construction": [
            (
                "jugeo.generation.state_space.generation_as_section_construction",
                "GenerationAsSectionConstruction",
            ),
        ],
        "core_state_space": [
            (
                "jugeo.generation.state_space.the_core_state_space_for_generatio",
                "StateSpaceExplorer",
            ),
        ],
        "dependent_transitions": [
            (
                "jugeo.generation.state_space.generation_moves_as_dependent_tran",
                "GenerationMove",
            ),
        ],
        "implementation_consequences": [
            (
                "jugeo.generation.state_space.implementation_consequences",
                "PolicyEnforcer",
            ),
        ],
        "search_algorithms": [
            ("jugeo.generation.state_space.algorithms", "StateSpaceSearch"),
        ],
        "integration": [
            ("jugeo.generation.state_space.integration", "StateSpaceIntegration"),
        ],
        "theorems": [
            ("jugeo.generation.state_space.theorems", "TheoremRegistry"),
        ],
        "state_representation": [
            ("jugeo.generation.state_space.state_representation", "SemanticState"),
        ],
        "frontier_management": [
            (
                "jugeo.generation.state_space.frontier_management",
                "FrontierManager",
            ),
        ],
        "search_strategies": [
            (
                "jugeo.generation.state_space.search_strategies",
                "SearchStrategy",
            ),
        ],
        "pruning": [
            ("jugeo.generation.state_space.pruning", "PruningStrategy"),
        ],
        "convergence_detection": [
            (
                "jugeo.generation.state_space.convergence_detection",
                "ConvergenceDetector",
            ),
        ],
        "state_merging": [
            ("jugeo.generation.state_space.state_merging", "StateMerger"),
        ],
        "backtracking": [
            ("jugeo.generation.state_space.backtracking", "BacktrackingStrategy"),
        ],
        "state_serialization": [
            (
                "jugeo.generation.state_space.state_serialization",
                "StateSerializer",
            ),
        ],
    }

    def probe_capability(self, name: str) -> bool:
        """Return True if the capability *name* is importable at runtime."""
        probes = self._PROBES.get(name, [])
        if not probes:
            logger.debug("No probes registered for capability %r", name)
            return False
        for module_path, symbol_name in probes:
            try:
                mod = importlib.import_module(module_path)
                if not hasattr(mod, symbol_name):
                    logger.debug(
                        "Module %s loaded but symbol %s missing",
                        module_path,
                        symbol_name,
                    )
                    return False
            except ImportError as exc:
                logger.debug(
                    "Capability %r unavailable: %s", name, exc
                )
                return False
        return True

    def probe_all(self) -> dict[str, bool]:
        """Probe all registered capabilities and return a name→bool dict."""
        results: dict[str, bool] = {}
        for name in self._PROBES:
            results[name] = self.probe_capability(name)
            logger.debug("Capability %r: %s", name, results[name])
        return results

    def unavailable_capabilities(self) -> list[str]:
        """Return names of capabilities that are NOT available."""
        return [name for name, ok in self.probe_all().items() if not ok]

    def available_capabilities(self) -> list[str]:
        """Return names of capabilities that ARE available."""
        return [name for name, ok in self.probe_all().items() if ok]


# ---------------------------------------------------------------------------
# Module registry helpers
# ---------------------------------------------------------------------------

def _build_module_descriptors() -> tuple[ModuleDescriptor, ...]:
    """Build the full list of module descriptors for the package."""
    legacy = [
        ModuleDescriptor(
            module_name="models",
            full_path=f"{PACKAGE_NAME}.models",
            description=(
                "Core data models: SemanticState, StateTransition, "
                "GenerationStateSpace. Foundation for all other modules."
            ),
            theory_section="40.0",
            is_new=False,
            exported_symbols=(
                "SemanticState",
                "StateTransition",
                "GenerationStateSpace",
            ),
        ),
        ModuleDescriptor(
            module_name="state_representation",
            full_path=f"{PACKAGE_NAME}.state_representation",
            description=(
                "State representation: how semantic states are encoded, "
                "their fingerprints, and their theory-grounded structure."
            ),
            theory_section="40.1",
            is_new=False,
            exported_symbols=("SemanticState", "StateFingerprint"),
        ),
        ModuleDescriptor(
            module_name="frontier_management",
            full_path=f"{PACKAGE_NAME}.frontier_management",
            description=(
                "Frontier management: the open list of states yet to be "
                "explored, with priority and deduplication."
            ),
            theory_section="40.2",
            is_new=False,
            exported_symbols=("FrontierManager", "FrontierEntry"),
        ),
        ModuleDescriptor(
            module_name="search_strategies",
            full_path=f"{PACKAGE_NAME}.search_strategies",
            description=(
                "Search strategies: BFS, DFS, beam, and policy-guided "
                "traversal of the generation state space."
            ),
            theory_section="40.3",
            is_new=False,
            exported_symbols=("SearchStrategy", "BFSStrategy", "DFSStrategy"),
        ),
        ModuleDescriptor(
            module_name="pruning",
            full_path=f"{PACKAGE_NAME}.pruning",
            description=(
                "Pruning: identifying and discarding dead-end states early "
                "to reduce search overhead."
            ),
            theory_section="40.4",
            is_new=False,
            exported_symbols=("PruningStrategy", "ObstructionPruner"),
        ),
        ModuleDescriptor(
            module_name="convergence_detection",
            full_path=f"{PACKAGE_NAME}.convergence_detection",
            description=(
                "Convergence detection: recognising when the generation "
                "process has reached a fixpoint."
            ),
            theory_section="40.5",
            is_new=False,
            exported_symbols=("ConvergenceDetector", "ConvergenceCriterion"),
        ),
        ModuleDescriptor(
            module_name="state_merging",
            full_path=f"{PACKAGE_NAME}.state_merging",
            description=(
                "State merging: combining compatible states to reduce the "
                "effective state space size."
            ),
            theory_section="40.6",
            is_new=False,
            exported_symbols=("StateMerger", "MergeResult"),
        ),
        ModuleDescriptor(
            module_name="backtracking",
            full_path=f"{PACKAGE_NAME}.backtracking",
            description=(
                "Backtracking: undoing moves when a dead end is reached and "
                "resuming from a saved checkpoint."
            ),
            theory_section="40.7",
            is_new=False,
            exported_symbols=("BacktrackingStrategy", "Checkpoint"),
        ),
        ModuleDescriptor(
            module_name="state_serialization",
            full_path=f"{PACKAGE_NAME}.state_serialization",
            description=(
                "State serialisation: persisting and restoring generation "
                "states across sessions."
            ),
            theory_section="40.8",
            is_new=False,
            exported_symbols=("StateSerializer", "StateDeserializer"),
        ),
    ]

    new_modules = [
        ModuleDescriptor(
            module_name="manifest",
            full_path=f"{PACKAGE_NAME}.manifest",
            description=(
                "Package manifest: documents all modules, capabilities, and "
                "exported symbols in the state_space package."
            ),
            theory_section="40.0",
            is_new=True,
            exported_symbols=(
                "StateSpaceManifest",
                "StateSpaceCapability",
                "ExportedSymbol",
                "CapabilityProbe",
                "build_manifest",
                "list_capabilities",
                "get_exports",
            ),
        ),
        ModuleDescriptor(
            module_name="generation_as_section_construction",
            full_path=f"{PACKAGE_NAME}.generation_as_section_construction",
            description=(
                "Generation as section construction: the core thesis that "
                "generation equals constructing global sections of a semantic "
                "sheaf. Implements SectionTarget, GenerationGoal, "
                "SectionConstructionPlan, and GenerationAsSectionConstruction."
            ),
            theory_section="40.9",
            is_new=True,
            exported_symbols=(
                "SectionTarget",
                "GenerationGoal",
                "SectionConstructionPlan",
                "GenerationAsSectionConstruction",
                "SectionConstructionWitness",
                "CoverDesign",
                "plan_section_construction",
                "construct_section",
                "validate_section_completeness",
            ),
        ),
        ModuleDescriptor(
            module_name="the_core_state_space_for_generatio",
            full_path=f"{PACKAGE_NAME}.the_core_state_space_for_generatio",
            description=(
                "Core generation state space: all states a generation process "
                "can occupy, from INITIAL through COMPLETE or FAILED. Includes "
                "GenerationContext, GenerationState, StateTransition, "
                "StateSpaceExplorer, and StateSpace."
            ),
            theory_section="40.10",
            is_new=True,
            exported_symbols=(
                "GenStateKind",
                "GenerationContext",
                "GenerationState",
                "StateTransition",
                "StateSpaceExplorer",
                "StateSpace",
                "build_state_space",
                "explore_state_space",
                "find_path_to_completion",
            ),
        ),
        ModuleDescriptor(
            module_name="generation_moves_as_dependent_tran",
            full_path=f"{PACKAGE_NAME}.generation_moves_as_dependent_tran",
            description=(
                "Generation moves as dependent transitions: each move depends "
                "on the current judgment state (coordinate, proposition, "
                "carrier, evidence, obligations, obstructions, trust, "
                "provenance). Includes concrete moves for local section "
                "proposal, obligation discharge, gluing, retraction, trust "
                "escalation, and obstruction recording."
            ),
            theory_section="40.11",
            is_new=True,
            exported_symbols=(
                "MoveObligation",
                "TransitionGuard",
                "MoveResult",
                "DependentTransition",
                "GenerationMove",
                "apply_move",
                "check_move_preconditions",
                "get_applicable_moves",
                "sequence_moves",
            ),
        ),
        ModuleDescriptor(
            module_name="implementation_consequences",
            full_path=f"{PACKAGE_NAME}.implementation_consequences",
            description=(
                "Implementation consequences: concrete invariants and policies "
                "derived from the state space theory. Includes PolicyViolation, "
                "StateSpaceConstraint, GenerationPolicy, PolicyEnforcer, "
                "ConstraintRegistry, and ConsequenceAnalyzer."
            ),
            theory_section="40.12",
            is_new=True,
            exported_symbols=(
                "PolicyViolation",
                "StateSpaceConstraint",
                "GenerationPolicy",
                "ImplementationConsequence",
                "PolicyEnforcer",
                "ConstraintRegistry",
                "ConsequenceAnalyzer",
                "derive_implementation_consequences",
                "check_policy",
                "build_default_policy",
            ),
        ),
        ModuleDescriptor(
            module_name="algorithms",
            full_path=f"{PACKAGE_NAME}.algorithms",
            description=(
                "Core search algorithms over the generation state space: BFS, "
                "DFS, A* with semantic heuristics. The semantic heuristic "
                "combines obligation count, trust gap, coverage, and "
                "obstruction penalties."
            ),
            theory_section="40.13",
            is_new=True,
            exported_symbols=(
                "SearchNode",
                "SemanticHeuristic",
                "PriorityQueue",
                "SearchResult",
                "StateSpaceSearch",
                "bfs_generation",
                "dfs_generation",
                "astar_generation",
                "build_default_heuristic",
            ),
        ),
        ModuleDescriptor(
            module_name="integration",
            full_path=f"{PACKAGE_NAME}.integration",
            description=(
                "Integration with orchestration, solver, and evidence layers. "
                "Provides OrchestratorBridge, SolverBridge, EvidenceBridge, "
                "and StateSpaceIntegration facade with graceful fallback."
            ),
            theory_section="40.14",
            is_new=True,
            exported_symbols=(
                "OrchestratorBridge",
                "SolverBridge",
                "EvidenceBridge",
                "StateSpaceIntegration",
                "integrate_with_orchestrator",
                "query_solver_for_state",
                "update_evidence_from_state",
                "build_integration",
            ),
        ),
        ModuleDescriptor(
            module_name="theorems",
            full_path=f"{PACKAGE_NAME}.theorems",
            description=(
                "Formal theorems about the generation state space: section "
                "existence, generation completeness, trust monotonicity, "
                "termination, obstruction persistence, no-silent-promotion, "
                "and descent-returns-section-or-obstruction."
            ),
            theory_section="40.15",
            is_new=True,
            exported_symbols=(
                "CorrectnessObligation",
                "TerminationArgument",
                "CompletenessProof",
                "GenerationTheorem",
                "TheoremRegistry",
                "CompletenessVerifier",
                "TerminationChecker",
                "CorrectnessValidator",
                "verify_completeness",
                "check_termination",
                "build_core_theorems",
            ),
        ),
    ]

    return tuple(legacy + new_modules)


def _build_capabilities(probe: Optional[CapabilityProbe] = None) -> tuple[StateSpaceCapability, ...]:
    """Build capability descriptors, optionally probing availability."""
    probe_results: dict[str, bool] = {}
    if probe is not None:
        probe_results = probe.probe_all()

    def avail(name: str) -> bool:
        return probe_results.get(name, True)  # optimistic if not probed

    caps = [
        StateSpaceCapability(
            name="section_construction",
            description=(
                "Generation as section construction: the core thesis mapping "
                "generation to Grothendieck sheaf global-section assembly."
            ),
            theory_section="40.9",
            is_available=avail("section_construction"),
        ),
        StateSpaceCapability(
            name="core_state_space",
            description=(
                "Core generation state space with GenStateKind enum, "
                "GenerationState dataclass, and StateSpaceExplorer."
            ),
            theory_section="40.10",
            is_available=avail("core_state_space"),
        ),
        StateSpaceCapability(
            name="dependent_transitions",
            description=(
                "Dependent generation moves: each move depends on the full "
                "judgment tuple (c,φ,A,E,O,B,T,Π)."
            ),
            theory_section="40.11",
            is_available=avail("dependent_transitions"),
        ),
        StateSpaceCapability(
            name="implementation_consequences",
            description=(
                "Policy enforcement and implementation consequences derived "
                "from state-space invariants."
            ),
            theory_section="40.12",
            is_available=avail("implementation_consequences"),
        ),
        StateSpaceCapability(
            name="search_algorithms",
            description=(
                "BFS, DFS, A* search over the generation state space with "
                "semantic heuristics."
            ),
            theory_section="40.13",
            is_available=avail("search_algorithms"),
        ),
        StateSpaceCapability(
            name="integration",
            description=(
                "Integration bridges to orchestration, solver, and evidence "
                "layers with graceful fallback."
            ),
            theory_section="40.14",
            is_available=avail("integration"),
        ),
        StateSpaceCapability(
            name="theorems",
            description=(
                "Formal theorems: section existence, completeness, trust "
                "monotonicity, termination, obstruction persistence."
            ),
            theory_section="40.15",
            is_available=avail("theorems"),
        ),
        StateSpaceCapability(
            name="state_representation",
            description="Legacy state representation from state_representation.",
            theory_section="40.1",
            is_available=avail("state_representation"),
        ),
        StateSpaceCapability(
            name="frontier_management",
            description="Legacy frontier management from frontier_management.",
            theory_section="40.2",
            is_available=avail("frontier_management"),
        ),
        StateSpaceCapability(
            name="search_strategies",
            description="Legacy search strategies from search_strategies.",
            theory_section="40.3",
            is_available=avail("search_strategies"),
        ),
        StateSpaceCapability(
            name="pruning",
            description="Legacy pruning strategies from pruning.",
            theory_section="40.4",
            is_available=avail("pruning"),
        ),
        StateSpaceCapability(
            name="convergence_detection",
            description="Legacy convergence detection from convergence_detection.",
            theory_section="40.5",
            is_available=avail("convergence_detection"),
        ),
        StateSpaceCapability(
            name="state_merging",
            description="Legacy state merging from state_merging.",
            theory_section="40.6",
            is_available=avail("state_merging"),
        ),
        StateSpaceCapability(
            name="backtracking",
            description="Legacy backtracking from backtracking.",
            theory_section="40.7",
            is_available=avail("backtracking"),
        ),
        StateSpaceCapability(
            name="state_serialization",
            description="Legacy state serialization from state_serialization.",
            theory_section="40.8",
            is_available=avail("state_serialization"),
        ),
    ]
    return tuple(caps)


def _build_exported_symbols() -> tuple[ExportedSymbol, ...]:
    """Build the list of all exported symbols across the package."""
    symbols = [
        # ---------- manifest.py ----------
        ExportedSymbol(
            name="StateSpaceManifest",
            module="manifest",
            kind="dataclass",
            theory_reference="§40.0",
            description="Machine-readable manifest for the state_space package.",
        ),
        ExportedSymbol(
            name="StateSpaceCapability",
            module="manifest",
            kind="dataclass",
            theory_reference="§40.0",
            description="A capability provided by the state_space package.",
        ),
        ExportedSymbol(
            name="ExportedSymbol",
            module="manifest",
            kind="dataclass",
            theory_reference="§40.0",
            description="A symbol exported from the state_space package.",
        ),
        ExportedSymbol(
            name="CapabilityProbe",
            module="manifest",
            kind="class",
            theory_reference="§40.0",
            description="Runtime probe for capability availability.",
        ),
        # ---------- generation_as_section_construction.py ----------
        ExportedSymbol(
            name="SectionTarget",
            module="generation_as_section_construction",
            kind="dataclass",
            theory_reference="§40.9 Def 40.1",
            description="Target for section construction: coordinate + proposition + trust floor.",
        ),
        ExportedSymbol(
            name="GenerationGoal",
            module="generation_as_section_construction",
            kind="dataclass",
            theory_reference="§40.9 Def 40.2",
            description="A generation goal with section target and judgment fields.",
        ),
        ExportedSymbol(
            name="SectionConstructionPlan",
            module="generation_as_section_construction",
            kind="dataclass",
            theory_reference="§40.9 Def 40.3",
            description="Plan for constructing a section over a cover.",
        ),
        ExportedSymbol(
            name="GenerationAsSectionConstruction",
            module="generation_as_section_construction",
            kind="class",
            theory_reference="§40.9",
            description="Main orchestrator for section construction.",
        ),
        ExportedSymbol(
            name="SectionConstructionWitness",
            module="generation_as_section_construction",
            kind="dataclass",
            theory_reference="§40.9 Def 40.4",
            description="Evidence witness for a completed section construction.",
        ),
        ExportedSymbol(
            name="CoverDesign",
            module="generation_as_section_construction",
            kind="dataclass",
            theory_reference="§40.9 Def 40.5",
            description="Grothendieck cover design for a coordinate.",
        ),
        # ---------- the_core_state_space_for_generatio.py ----------
        ExportedSymbol(
            name="GenStateKind",
            module="the_core_state_space_for_generatio",
            kind="enum",
            theory_reference="§40.10 Def 40.6",
            description="Enum of all states a generation process can occupy.",
        ),
        ExportedSymbol(
            name="GenerationContext",
            module="the_core_state_space_for_generatio",
            kind="dataclass",
            theory_reference="§40.10 Def 40.7",
            description="Context for a generation run.",
        ),
        ExportedSymbol(
            name="GenerationState",
            module="the_core_state_space_for_generatio",
            kind="dataclass",
            theory_reference="§40.10 Def 40.8",
            description="A state in the generation process.",
        ),
        ExportedSymbol(
            name="StateSpace",
            module="the_core_state_space_for_generatio",
            kind="class",
            theory_reference="§40.10",
            description="The full generation state space graph.",
        ),
        ExportedSymbol(
            name="StateSpaceExplorer",
            module="the_core_state_space_for_generatio",
            kind="class",
            theory_reference="§40.10",
            description="Explores the generation state space from a starting state.",
        ),
        # ---------- generation_moves_as_dependent_tran.py ----------
        ExportedSymbol(
            name="MoveObligation",
            module="generation_moves_as_dependent_tran",
            kind="dataclass",
            theory_reference="§40.11 Def 40.9",
            description="An obligation a move must discharge.",
        ),
        ExportedSymbol(
            name="TransitionGuard",
            module="generation_moves_as_dependent_tran",
            kind="dataclass",
            theory_reference="§40.11 Def 40.10",
            description="A guard that must hold for a transition to fire.",
        ),
        ExportedSymbol(
            name="MoveResult",
            module="generation_moves_as_dependent_tran",
            kind="dataclass",
            theory_reference="§40.11 Def 40.11",
            description="Result of applying a generation move.",
        ),
        ExportedSymbol(
            name="DependentTransition",
            module="generation_moves_as_dependent_tran",
            kind="dataclass",
            theory_reference="§40.11 Def 40.12",
            description="A transition dependent on the current judgment state.",
        ),
        ExportedSymbol(
            name="GenerationMove",
            module="generation_moves_as_dependent_tran",
            kind="class",
            theory_reference="§40.11",
            description="A move in the generation state space.",
        ),
        # ---------- implementation_consequences.py ----------
        ExportedSymbol(
            name="PolicyViolation",
            module="implementation_consequences",
            kind="dataclass",
            theory_reference="§40.12 Def 40.13",
            description="A violation of a generation policy.",
        ),
        ExportedSymbol(
            name="StateSpaceConstraint",
            module="implementation_consequences",
            kind="dataclass",
            theory_reference="§40.12 Def 40.14",
            description="A constraint on the state space.",
        ),
        ExportedSymbol(
            name="GenerationPolicy",
            module="implementation_consequences",
            kind="dataclass",
            theory_reference="§40.12 Def 40.15",
            description="A policy governing generation behaviour.",
        ),
        ExportedSymbol(
            name="PolicyEnforcer",
            module="implementation_consequences",
            kind="class",
            theory_reference="§40.12",
            description="Enforces generation policies against states.",
        ),
        ExportedSymbol(
            name="ConstraintRegistry",
            module="implementation_consequences",
            kind="class",
            theory_reference="§40.12",
            description="Registry of all active state-space constraints.",
        ),
        # ---------- algorithms.py ----------
        ExportedSymbol(
            name="SearchNode",
            module="algorithms",
            kind="dataclass",
            theory_reference="§40.13 Def 40.16",
            description="A node in the BFS/DFS/A* search tree.",
        ),
        ExportedSymbol(
            name="SemanticHeuristic",
            module="algorithms",
            kind="class",
            theory_reference="§40.13",
            description="Semantic heuristic for A* search over generation states.",
        ),
        ExportedSymbol(
            name="SearchResult",
            module="algorithms",
            kind="dataclass",
            theory_reference="§40.13 Def 40.17",
            description="Result of a BFS/DFS/A* search.",
        ),
        ExportedSymbol(
            name="StateSpaceSearch",
            module="algorithms",
            kind="class",
            theory_reference="§40.13",
            description="Main search class implementing BFS, DFS, and A*.",
        ),
        ExportedSymbol(
            name="bfs_generation",
            module="algorithms",
            kind="function",
            theory_reference="§40.13",
            description="Top-level BFS search function.",
        ),
        ExportedSymbol(
            name="astar_generation",
            module="algorithms",
            kind="function",
            theory_reference="§40.13",
            description="Top-level A* search function.",
        ),
        # ---------- integration.py ----------
        ExportedSymbol(
            name="OrchestratorBridge",
            module="integration",
            kind="class",
            theory_reference="§40.14",
            description="Bridge to the orchestration layer.",
        ),
        ExportedSymbol(
            name="SolverBridge",
            module="integration",
            kind="class",
            theory_reference="§40.14",
            description="Bridge to the solver layer.",
        ),
        ExportedSymbol(
            name="EvidenceBridge",
            module="integration",
            kind="class",
            theory_reference="§40.14",
            description="Bridge to the evidence layer.",
        ),
        ExportedSymbol(
            name="StateSpaceIntegration",
            module="integration",
            kind="class",
            theory_reference="§40.14",
            description="Facade integrating orchestration, solver, and evidence bridges.",
        ),
        # ---------- theorems.py ----------
        ExportedSymbol(
            name="GenerationTheorem",
            module="theorems",
            kind="dataclass",
            theory_reference="§40.15 Def 40.18",
            description="Formal theorem about the generation state space.",
        ),
        ExportedSymbol(
            name="TheoremRegistry",
            module="theorems",
            kind="class",
            theory_reference="§40.15",
            description="Registry of all formal theorems.",
        ),
        ExportedSymbol(
            name="CompletenessVerifier",
            module="theorems",
            kind="class",
            theory_reference="§40.15",
            description="Verifies completeness theorems.",
        ),
        ExportedSymbol(
            name="TerminationChecker",
            module="theorems",
            kind="class",
            theory_reference="§40.15",
            description="Checks termination arguments.",
        ),
        ExportedSymbol(
            name="build_core_theorems",
            module="theorems",
            kind="function",
            theory_reference="§40.15",
            description="Builds and returns all core theorems as a list.",
        ),
    ]
    return tuple(symbols)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def build_manifest(probe_capabilities: bool = False) -> StateSpaceManifest:
    """Build and return the :class:`StateSpaceManifest` for this package.

    Parameters
    ----------
    probe_capabilities:
        If True, actually try to import each capability module at runtime and
        populate ``is_available`` accordingly. If False (default), all
        capabilities are reported as available (optimistic).

    Returns
    -------
    StateSpaceManifest
        The fully populated manifest.
    """
    logger.info("Building state_space manifest (probe=%s)", probe_capabilities)
    probe: Optional[CapabilityProbe] = None
    if probe_capabilities:
        probe = CapabilityProbe()

    modules = _build_module_descriptors()
    capabilities = _build_capabilities(probe)
    exported = _build_exported_symbols()

    manifest = StateSpaceManifest(
        package_name=PACKAGE_NAME,
        version=VERSION,
        chapter=CHAPTER,
        theory_file=THEORY_FILE,
        modules=modules,
        capabilities=capabilities,
        exported_symbols=exported,
        created_at=time.time(),
    )
    logger.debug(
        "Manifest built: %d modules, %d capabilities, %d symbols",
        len(modules),
        len(capabilities),
        len(exported),
    )
    return manifest


def list_capabilities(probe: bool = False) -> list[StateSpaceCapability]:
    """Return the list of :class:`StateSpaceCapability` objects for this package.

    Parameters
    ----------
    probe:
        If True, check runtime availability of each capability.
    """
    manifest = build_manifest(probe_capabilities=probe)
    return list(manifest.capabilities)


def get_exports() -> list[ExportedSymbol]:
    """Return all :class:`ExportedSymbol` objects for this package."""
    return list(_build_exported_symbols())


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    print("=== manifest.py smoke test ===")

    # 1. Build manifest without probing
    m = build_manifest(probe_capabilities=False)
    assert m.package_name == PACKAGE_NAME, "package_name mismatch"
    assert m.version == VERSION, "version mismatch"
    assert m.chapter == CHAPTER, "chapter mismatch"
    assert len(m.modules) > 0, "no modules in manifest"
    print(f"  Manifest built: {m}")

    # 2. Module lookup
    desc = m.get_module("algorithms")
    assert desc is not None, "algorithms module not found"
    assert desc.is_new, "algorithms should be marked as new"
    print(f"  Module lookup OK: {desc.module_name} (new={desc.is_new})")

    # 3. New / legacy split
    new = m.get_new_modules()
    legacy = m.get_legacy_modules()
    assert len(new) == 8, f"expected 8 new modules, got {len(new)}"
    assert len(legacy) == 9, f"expected 9 legacy modules, got {len(legacy)}"
    print(f"  New modules: {len(new)}, Legacy modules: {len(legacy)}")

    # 4. Exported symbols
    exports = get_exports()
    assert len(exports) > 10, "too few exported symbols"
    print(f"  Exported symbols: {len(exports)}")

    # 5. list_capabilities
    caps = list_capabilities(probe=False)
    assert len(caps) > 0, "no capabilities"
    print(f"  Capabilities: {len(caps)}")
    for cap in caps[:3]:
        print(f"    {cap}")

    # 6. CapabilityProbe smoke (no actual imports needed)
    probe = CapabilityProbe()
    all_names = list(probe._PROBES.keys())
    assert len(all_names) > 5, "too few probes registered"
    print(f"  CapabilityProbe registered {len(all_names)} capability probes")

    # 7. Summary dict
    summary = m.summary()
    assert "module_count" in summary
    assert summary["module_count"] == len(m.modules)
    print(f"  Summary: {summary}")

    print("All smoke tests passed.")
    sys.exit(0)
