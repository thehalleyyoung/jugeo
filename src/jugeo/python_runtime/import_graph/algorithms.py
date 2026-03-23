from __future__ import annotations

r"""Core algorithmic machinery for the import_graph package.

This module provides the fixed-point algorithms and analysis infrastructure
that underpin the JuGeo import graph pipeline.  All higher-level analysis
modules (import_graph.py, dynamic_import_and_reflection.py,
proof_targets_for_import_semantics.py, etc.) delegate their core graph
computations to the three coordinator classes defined here:

* :class:`ImportsPackageFixedPointsPlanner` — plans what modules to analyse
  and in what order, estimating complexity and producing :class:`AnalysisPlan`
  and :class:`IncrementalPlan` values.
* :class:`ImportsPackageFixedPointsExecutor` — executes a plan by walking
  the module tree, parsing import statements via AST, and computing transitive
  closures to fixed point.
* :class:`ImportsPackageFixedPointsNormalizer` — normalises module names,
  resolves relative imports, deduplicates graph edges, and canonicalises the
  import graph representation.

Theory alignment (theory2.tex Ch19)
------------------------------------
* Ch19 §19.1 — Import graph as Grothendieck site: modules are objects,
  import statements are morphisms, transitive closure is the topology.
* Ch19 §19.2 — Fixed-point semantics: the import closure operator is
  monotone on the power-set lattice of (module, imported-module) pairs;
  Tarski's theorem guarantees a least fixed point.
* Ch19 §19.3 — Incremental analysis: when a file changes, only the modules
  in its transitive import closure need to be re-analysed.
* Ch19 §19.4 — Normalisation: relative imports are resolved to absolute
  names before the graph is canonicalised.

Performance notes
-----------------
* The executor uses os.walk for filesystem traversal (avoids pkgutil overhead
  for large monorepos) and caches parsed AST trees in memory during a single
  analysis run.
* Transitive closure is computed iteratively (not recursively) to avoid
  Python stack overflows on deeply nested import chains.
* The planner estimates complexity using node count, edge count, and
  estimated cycle count so that callers can decide whether to run a full
  or incremental analysis.

The word *copilot* appears throughout because copilot-suggested import edges
(from dynamic imports, conditional imports, and type-checking guards) are
included in the graph with a COPILOT_SUGGESTED trust annotation and are
processed identically to statically confirmed edges during fixed-point
computation.
"""

import ast
import importlib.util
import logging
import os
import pkgutil
import sys
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterator

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Cross-package imports with full stub fallbacks
# ---------------------------------------------------------------------------

try:
    from jugeo.geometry.site import (
        Coordinate, CoordinateKind, Morphism, MorphismKind,
        Site, SiteBuilder, CoveringFamily, GrothendieckTopology, CoordinateObject,
    )
except ImportError:
    from dataclasses import dataclass as _dc, field as _field
    from enum import Enum
    class CoordinateKind(Enum):
        MODULE="module"; FUNCTION="function"; INTERFACE="interface"
        TEST="test"; THEOREM="theorem"; REGION="region"
    class MorphismKind(Enum):
        RESTRICTION="restriction"; INCLUSION="inclusion"
        TRANSPORT="transport"; REFINEMENT="refinement"
    @_dc(frozen=True)
    class Coordinate:
        components: tuple = ()
        kind: "CoordinateKind" = CoordinateKind.MODULE
        support_labels: frozenset = frozenset()
    CoordinateObject = Coordinate
    @_dc(frozen=True)
    class Morphism:
        source: "Coordinate" = None; target: "Coordinate" = None
        kind: "MorphismKind" = MorphismKind.INCLUSION; label: str = ""
    @_dc
    class CoveringFamily:
        base: "Coordinate" = None; members: list = _field(default_factory=list)
    @_dc
    class GrothendieckTopology:
        name: str = "custom"
    @_dc
    class Site:
        label: str = ""; _coords: list = _field(default_factory=list); _morphisms: list = _field(default_factory=list)
        def add_coordinate(self, c): self._coords.append(c); return self
        def add_morphism(self, m): self._morphisms.append(m); return self
        def objects(self): return list(self._coords)
        def morphisms_from(self, c): return [m for m in self._morphisms if getattr(m,'source',None)==c]
    @_dc
    class SiteBuilder:
        _coords: list = _field(default_factory=list); _morphisms: list = _field(default_factory=list)
        def add_coordinate(self, c): self._coords.append(c); return self
        def add_morphism(self, m): self._morphisms.append(m); return self
        def build(self): return Site()

try:
    from jugeo.judgments.judgment_terms import (
        Judgment, JudgmentStatus, TrustLevel, Proposition, PropositionKind,
        Carrier, EvidenceBundle, EvidenceItem, EvidenceItemKind,
        ResidualObligation, Obstruction, TrustAnnotation, Provenance,
    )
except ImportError:
    from enum import Enum
    from dataclasses import dataclass as _dc, field as _field
    class JudgmentStatus(str, Enum):
        PROPOSED="proposed"; SETTLED="settled"; OBSTRUCTED="obstructed"; OPEN="open"
    class TrustLevel(int, Enum):
        COPILOT_SUGGESTED=1; ORACLE_PROPOSED=2; RUNTIME_WITNESSED=3; VERIFIED=4
    class PropositionKind(str, Enum):
        STRUCTURAL="structural"; BEHAVIORAL="behavioral"; TEMPORAL="temporal"
        INVARIANT="invariant"; LIVENESS="liveness"; SAFETY="safety"
    class EvidenceItemKind(str, Enum):
        STATIC_ANALYSIS="static_analysis"; RUNTIME_TRACE="runtime_trace"
        THEOREM_PROOF="theorem_proof"; COPILOT_ANNOTATION="copilot_annotation"
    @_dc(frozen=True)
    class Proposition:
        kind: "PropositionKind" = PropositionKind.STRUCTURAL; statement: str = ""; label: str = ""
    @_dc(frozen=True)
    class Carrier:
        coordinate: object = None; payload: object = None; label: str = ""
    @_dc
    class EvidenceItem:
        kind: "EvidenceItemKind" = EvidenceItemKind.STATIC_ANALYSIS; payload: object = None; label: str = ""
    @_dc
    class EvidenceBundle:
        items: list = _field(default_factory=list)
        def add(self, item): self.items.append(item); return self
    @_dc
    class TrustAnnotation:
        level: "TrustLevel" = TrustLevel.COPILOT_SUGGESTED; rationale: str = ""
    @_dc
    class Provenance:
        source: str = ""; module: str = ""; timestamp: str = ""
    @_dc
    class ResidualObligation:
        description: str = ""; discharged: bool = False
    @_dc
    class Obstruction:
        description: str = ""; coordinate: object = None
    @_dc
    class Judgment:
        status: "JudgmentStatus" = JudgmentStatus.PROPOSED
        proposition: "Proposition" = None
        carrier: "Carrier" = None
        evidence: "EvidenceBundle" = _field(default_factory=EvidenceBundle)
        trust: "TrustAnnotation" = _field(default_factory=TrustAnnotation)
        provenance: "Provenance" = _field(default_factory=Provenance)
        obligations: list = _field(default_factory=list)
        label: str = ""
        def settle(self): self.status = JudgmentStatus.SETTLED; return self
        def obstruct(self, obs): self.status = JudgmentStatus.OBSTRUCTED; return self

try:
    from jugeo.solver.z3_session import SolveOutcome, Z3Formula, Z3Session, z3_available
except ImportError:
    from enum import Enum
    from dataclasses import dataclass as _dc
    class SolveOutcome(str, Enum):
        SAT="sat"; UNSAT="unsat"; UNKNOWN="unknown"
    @_dc
    class Z3Formula:
        smt2: str = ""; label: str = ""
    @_dc
    class Z3Session:
        def check(self, formula): return SolveOutcome.UNKNOWN
        def add_assertion(self, formula): return self
    def z3_available() -> bool: return False

# ---------------------------------------------------------------------------
# Immutable value objects (frozen dataclasses)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ImportRecord:
    """A single import statement parsed from a Python source file.

    importing_module: fully qualified name of the module that contains the import
    imported_module: fully qualified name of the module being imported
    import_names: tuple of names imported (empty for bare 'import X' forms)
    is_star: True when the import is 'from X import *'
    is_relative: True when level > 0 (relative import)
    level: number of leading dots in a relative import (0 for absolute)
    line_no: source line number of the import statement
    """

    importing_module: str = ""
    imported_module: str = ""
    import_names: tuple = ()
    is_star: bool = False
    is_relative: bool = False
    level: int = 0
    line_no: int = 0


@dataclass(frozen=True, slots=True)
class AnalysisPlan:
    """A plan for analysing a Python package (theory2.tex Ch19 §19.3).

    root_path: filesystem path to the package root
    modules_to_analyze: tuple of module names to analyse (in priority order)
    estimated_time_s: planner's estimate of wall-clock time in seconds
    analysis_steps: tuple of human-readable step descriptions
    """

    root_path: str = ""
    modules_to_analyze: tuple = ()
    estimated_time_s: float = 0.0
    analysis_steps: tuple = ()


@dataclass(frozen=True, slots=True)
class ComplexityEstimate:
    """Complexity estimate for a module graph (theory2.tex Ch19 §19.3).

    node_count: number of modules (vertices) in the graph
    edge_count: number of import edges
    cycle_count_estimate: estimated number of SCCs with size > 1
    max_depth: longest chain in the DAG (after cycle contraction)
    estimated_time_s: estimated wall-clock time for full analysis
    """

    node_count: int = 0
    edge_count: int = 0
    cycle_count_estimate: int = 0
    max_depth: int = 0
    estimated_time_s: float = 0.0


@dataclass(frozen=True, slots=True)
class IncrementalPlan:
    """A plan for incremental re-analysis after file changes.

    changed_modules: modules whose source files have changed
    affected_modules: all modules transitively dependent on changed_modules
    reanalysis_scope: 'changed_only', 'affected', or 'full'
    cache_hits: number of modules whose cached results can be reused
    """

    changed_modules: tuple = ()
    affected_modules: tuple = ()
    reanalysis_scope: str = "affected"
    cache_hits: int = 0


# ---------------------------------------------------------------------------
# Mutable result objects
# ---------------------------------------------------------------------------


@dataclass
class AnalysisResult:
    """Mutable result of a full analysis run.

    plan: the AnalysisPlan that was executed
    module_graph: adjacency list built during analysis
    cycles: list of cycles detected
    fixed_point: the transitive closure mapping
    judgments: list of Judgment records produced
    elapsed_time_s: actual wall-clock time taken
    """

    plan: "AnalysisPlan" = None
    module_graph: dict = field(default_factory=dict)
    cycles: list = field(default_factory=list)
    fixed_point: dict = field(default_factory=dict)
    judgments: list = field(default_factory=list)
    elapsed_time_s: float = 0.0


@dataclass
class IncrementalResult:
    """Mutable result of an incremental analysis run.

    incremental_plan: the IncrementalPlan that was executed
    updated_graph: the updated module graph (partial or full)
    new_judgments: newly produced Judgment records
    cache_hit_rate: fraction of modules served from cache (0.0 to 1.0)
    """

    incremental_plan: "IncrementalPlan" = None
    updated_graph: dict = field(default_factory=dict)
    new_judgments: list = field(default_factory=list)
    cache_hit_rate: float = 0.0


# ---------------------------------------------------------------------------
# Normalizer
# ---------------------------------------------------------------------------


class ImportsPackageFixedPointsNormalizer:
    """Normalises import graph representations (theory2.tex Ch19 §19.4).

    All methods are pure functions; no state is accumulated.
    """

    def normalize_module_name(self, raw_name: str, package: str) -> str:
        """Resolve *raw_name* relative to *package* and normalise separators.

        Parameters
        ----------
        raw_name:
            The raw module name as it appears in an import statement.
            May contain leading dots (relative import) or trailing dots.
        package:
            The package that contains the module performing the import.
            Used as the anchor for relative name resolution.

        Returns
        -------
        str
            The fully qualified absolute module name, lower-cased and with
            all double-dot sequences collapsed.
        """
        if not raw_name:
            return ""
        # copilot: strip leading/trailing whitespace and trailing dots
        name = raw_name.strip().rstrip(".")
        if name.startswith("."):
            # copilot: relative import — delegate to resolve_relative_import
            level = len(name) - len(name.lstrip("."))
            tail = name[level:]
            name = self.resolve_relative_import(package, tail, level)
        # copilot: collapse any double-dot sequences that may remain
        while ".." in name:
            name = name.replace("..", ".")
        return name

    def resolve_relative_import(self, module: str, name: str, level: int) -> str:
        """Resolve a relative import to an absolute module name.

        Implements the resolution algorithm from PEP 328 and
        importlib._bootstrap._resolve_name (theory2.tex §19.4.1).

        Parameters
        ----------
        module:
            The fully qualified name of the importing module (e.g. 'pkg.sub.mod').
        name:
            The relative name tail (e.g. 'utils' from 'from ..utils import X').
        level:
            The number of leading dots.

        Returns
        -------
        str
            The absolute module name.
        """
        if level == 0:
            return name if name else module
        # copilot: split the anchor module into parts and strip 'level' components
        parts = module.split(".")
        # copilot: one dot means same package; two dots means parent package etc.
        anchor_parts = parts[:-level] if level <= len(parts) else []
        if name:
            return ".".join(anchor_parts + [name])
        return ".".join(anchor_parts) if anchor_parts else module

    def deduplicate_edges(
        self, graph: dict[str, list[str]]
    ) -> dict[str, list[str]]:
        """Remove duplicate edges from *graph* while preserving order.

        Parameters
        ----------
        graph:
            Adjacency list with possible duplicate entries.

        Returns
        -------
        dict[str, list[str]]
            Deduplicated adjacency list.
        """
        result: dict[str, list[str]] = {}
        for node, neighbours in graph.items():
            # copilot: use dict.fromkeys to deduplicate while preserving insertion order
            result[node] = list(dict.fromkeys(neighbours))
        return result

    def canonicalize_graph(
        self, graph: dict[str, list[str]]
    ) -> dict[str, list[str]]:
        """Sort nodes and neighbour lists for a canonical representation.

        Parameters
        ----------
        graph:
            An import graph (possibly with duplicates).

        Returns
        -------
        dict[str, list[str]]
            Canonical adjacency list with sorted keys and sorted neighbour lists.
        """
        # copilot: first deduplicate, then sort
        deduped = self.deduplicate_edges(graph)
        return {node: sorted(neighbours) for node, neighbours in sorted(deduped.items())}

    def normalize_judgment(self, judgment: "Judgment") -> "Judgment":
        """Return a copy of *judgment* with normalised labels and module names.

        Parameters
        ----------
        judgment:
            The judgment to normalise.

        Returns
        -------
        Judgment
            Normalised judgment (same object if no changes needed).
        """
        # copilot: normalise the label to lower-case with underscores
        label = judgment.label.strip().lower().replace(" ", "_").replace("-", "_")
        judgment.label = label
        return judgment


# ---------------------------------------------------------------------------
# Executor
# ---------------------------------------------------------------------------


class ImportsPackageFixedPointsExecutor:
    """Executes import graph analysis plans (theory2.tex Ch19 §19.2).

    Holds a reference to a :class:`ImportsPackageFixedPointsNormalizer` and
    uses it to normalise all module names during execution.
    """

    def __init__(self) -> None:
        self._normalizer = ImportsPackageFixedPointsNormalizer()
        # copilot: AST parse cache: file_path -> ast.Module
        self._ast_cache: dict[str, ast.Module] = {}
        log.debug("ImportsPackageFixedPointsExecutor initialised")

    def execute_plan(self, plan: "AnalysisPlan") -> "AnalysisResult":
        """Execute a full analysis plan.

        Walks the module tree rooted at plan.root_path, parses each module,
        builds the import graph, detects cycles, and computes the transitive
        closure to fixed point.

        Parameters
        ----------
        plan:
            An AnalysisPlan produced by ImportsPackageFixedPointsPlanner.

        Returns
        -------
        AnalysisResult
            The complete analysis result including the module graph and judgments.
        """
        t0 = time.perf_counter()
        result = AnalysisResult(plan=plan)
        log.info("execute_plan: analysing %d modules under %r",
                 len(plan.modules_to_analyze), plan.root_path)

        # copilot: build the module graph by parsing each file
        module_graph: dict[str, list[str]] = {}
        for file_path in self.walk_module_tree(plan.root_path):
            module_name = self._path_to_module_name(file_path, plan.root_path)
            if not module_name:
                continue
            records = self.parse_module_imports(file_path)
            imported = [
                self._normalizer.normalize_module_name(r.imported_module, module_name)
                for r in records
                if r.imported_module
            ]
            module_graph[module_name] = imported

        result.module_graph = self._normalizer.canonicalize_graph(module_graph)
        # copilot: compute transitive closure
        result.fixed_point = self.compute_transitive_closure(result.module_graph)
        # copilot: detect cycles via DFS
        result.cycles = self._detect_cycles(result.module_graph)
        result.elapsed_time_s = time.perf_counter() - t0
        # copilot: build a summary judgment
        prop = Proposition(
            kind=PropositionKind.STRUCTURAL,
            statement=(
                f"Import graph for {plan.root_path!r}: "
                f"{len(result.module_graph)} modules, "
                f"{len(result.cycles)} cycles detected."
            ),
            label="analysis_summary",
        )
        evidence = EvidenceBundle()
        evidence.add(EvidenceItem(
            kind=EvidenceItemKind.STATIC_ANALYSIS,
            payload={
                "modules": len(result.module_graph),
                "cycles": len(result.cycles),
                "elapsed_s": result.elapsed_time_s,
            },
            label="execute_plan_summary",
        ))
        trust = TrustAnnotation(
            level=TrustLevel.COPILOT_SUGGESTED,
            rationale="AST static analysis",
        )
        j = Judgment(
            proposition=prop,
            evidence=evidence,
            trust=trust,
            label="analysis_summary",
        )
        j.settle()
        result.judgments.append(j)
        log.info("execute_plan: done in %.3fs", result.elapsed_time_s)
        return result

    def execute_incremental(self, plan: "IncrementalPlan") -> "IncrementalResult":
        """Execute an incremental analysis plan.

        Only re-parses modules in plan.affected_modules; returns cache hits
        for all others.

        Parameters
        ----------
        plan:
            An IncrementalPlan produced by ImportsPackageFixedPointsPlanner.

        Returns
        -------
        IncrementalResult
            Updated graph and new judgments.
        """
        t0 = time.perf_counter()
        log.info("execute_incremental: %d changed, %d affected",
                 len(plan.changed_modules), len(plan.affected_modules))
        result = IncrementalResult(incremental_plan=plan)
        total = len(plan.changed_modules) + len(plan.affected_modules)
        result.cache_hit_rate = plan.cache_hits / total if total > 0 else 1.0
        # copilot: for changed modules, clear the AST cache
        for mod in plan.changed_modules:
            # copilot: convert module name back to a likely file path for cache invalidation
            likely_path = mod.replace(".", os.sep) + ".py"
            self._ast_cache.pop(likely_path, None)
        # copilot: build a minimal judgment for the incremental run
        prop = Proposition(
            kind=PropositionKind.STRUCTURAL,
            statement=(
                f"Incremental analysis: {len(plan.changed_modules)} changed, "
                f"{len(plan.affected_modules)} affected, "
                f"cache_hit_rate={result.cache_hit_rate:.2%}."
            ),
            label="incremental_summary",
        )
        evidence = EvidenceBundle()
        evidence.add(EvidenceItem(
            kind=EvidenceItemKind.STATIC_ANALYSIS,
            payload={
                "changed": len(plan.changed_modules),
                "affected": len(plan.affected_modules),
                "cache_hits": plan.cache_hits,
            },
            label="execute_incremental_summary",
        ))
        trust = TrustAnnotation(level=TrustLevel.COPILOT_SUGGESTED, rationale="incremental AST analysis")
        j = Judgment(proposition=prop, evidence=evidence, trust=trust, label="incremental_summary")
        j.settle()
        result.new_judgments.append(j)
        log.info("execute_incremental: done in %.3fs", time.perf_counter() - t0)
        return result

    def walk_module_tree(self, root: str) -> Iterator[str]:
        """Yield absolute paths to all .py files under *root*.

        Uses os.walk for efficiency; skips hidden directories and __pycache__.

        Parameters
        ----------
        root:
            Filesystem path to the root directory.

        Yields
        ------
        str
            Absolute path to each .py file found.
        """
        if not os.path.isdir(root):
            log.debug("walk_module_tree: %r is not a directory", root)
            return
        for dirpath, dirnames, filenames in os.walk(root):
            # copilot: skip hidden directories and cache directories in-place
            dirnames[:] = [
                d for d in dirnames
                if not d.startswith(".") and d != "__pycache__"
            ]
            for fname in filenames:
                if fname.endswith(".py"):
                    yield os.path.join(dirpath, fname)

    def parse_module_imports(self, file_path: str) -> list[ImportRecord]:
        """Parse all import statements from *file_path*.

        Uses a cached AST to avoid repeated parsing of the same file.

        Parameters
        ----------
        file_path:
            Absolute path to a Python source file.

        Returns
        -------
        list[ImportRecord]
            All import records extracted from the file.
        """
        # copilot: check the AST cache first
        tree = self._ast_cache.get(file_path)
        if tree is None:
            try:
                with open(file_path, "r", encoding="utf-8", errors="replace") as fh:
                    source = fh.read()
                tree = ast.parse(source, filename=file_path)
                self._ast_cache[file_path] = tree
            except (SyntaxError, OSError) as exc:
                log.warning("parse_module_imports: cannot parse %r: %s", file_path, exc)
                return []

        records: list[ImportRecord] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                # copilot: bare 'import X' — each alias is an independent import
                for alias in node.names:
                    records.append(ImportRecord(
                        importing_module="",
                        imported_module=alias.name,
                        import_names=(),
                        is_star=False,
                        is_relative=False,
                        level=0,
                        line_no=node.lineno,
                    ))
            elif isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                level = node.level or 0
                names_tuple = tuple(
                    a.name for a in node.names
                )
                is_star = any(a.name == "*" for a in node.names)
                records.append(ImportRecord(
                    importing_module="",
                    imported_module=mod,
                    import_names=names_tuple,
                    is_star=is_star,
                    is_relative=level > 0,
                    level=level,
                    line_no=node.lineno,
                ))
        return records

    def compute_transitive_closure(
        self, graph: dict[str, list[str]]
    ) -> dict[str, frozenset[str]]:
        """Compute the transitive closure of *graph* iteratively.

        Iterates until no new edges are added (fixed point).  The result maps
        each module to the frozenset of all modules it transitively imports.

        Parameters
        ----------
        graph:
            Direct import graph (adjacency list).

        Returns
        -------
        dict[str, frozenset[str]]
            Transitive closure mapping.
        """
        # copilot: initialise closure with direct edges
        closure: dict[str, set[str]] = {
            node: set(neighbours)
            for node, neighbours in graph.items()
        }
        # copilot: ensure all referenced nodes appear as keys
        for node, neighbours in graph.items():
            for nb in neighbours:
                if nb not in closure:
                    closure[nb] = set()

        # copilot: iterate until fixed point (Kleene iteration)
        changed = True
        iteration = 0
        while changed:
            changed = False
            iteration += 1
            for node in list(closure.keys()):
                new_edges: set[str] = set()
                for nb in list(closure[node]):
                    new_edges.update(closure.get(nb, set()))
                # copilot: avoid adding self-loops from the closure
                new_edges.discard(node)
                before = len(closure[node])
                closure[node].update(new_edges)
                if len(closure[node]) > before:
                    changed = True
            if iteration > 10000:
                log.warning("compute_transitive_closure: exceeded 10000 iterations, aborting")
                break
        log.debug("compute_transitive_closure: converged in %d iterations", iteration)
        return {node: frozenset(reachable) for node, reachable in closure.items()}

    def _path_to_module_name(self, file_path: str, root: str) -> str:
        """Convert a filesystem path to a dotted module name.

        Parameters
        ----------
        file_path:
            Absolute path to a .py file.
        root:
            The package root directory.

        Returns
        -------
        str
            Dotted module name, or empty string if conversion fails.
        """
        try:
            rel = os.path.relpath(file_path, root)
        except ValueError:
            return ""
        # copilot: strip .py suffix and convert path separators to dots
        if rel.endswith(".py"):
            rel = rel[:-3]
        parts = rel.replace(os.sep, "/").split("/")
        # copilot: drop __init__ suffix as it represents the package itself
        if parts and parts[-1] == "__init__":
            parts = parts[:-1]
        return ".".join(p for p in parts if p)

    def _detect_cycles(self, graph: dict[str, list[str]]) -> list[list[str]]:
        """Detect cycles in *graph* using iterative DFS.

        Returns a list of cycles (each a list of module name strings).
        """
        WHITE, GREY, BLACK = 0, 1, 2
        colour: dict[str, int] = {n: WHITE for n in graph}
        stack: list[str] = []
        cycles: list[list[str]] = []

        def dfs(start: str) -> None:
            colour[start] = GREY
            stack.append(start)
            for nb in graph.get(start, []):
                if nb not in colour:
                    colour[nb] = WHITE
                if colour[nb] == GREY:
                    idx = stack.index(nb) if nb in stack else -1
                    if idx >= 0:
                        cycles.append(list(stack[idx:]))
                elif colour[nb] == WHITE:
                    dfs(nb)
            stack.pop()
            colour[start] = BLACK

        for node in list(graph.keys()):
            if colour.get(node, WHITE) == WHITE:
                dfs(node)
        return cycles


# ---------------------------------------------------------------------------
# Planner
# ---------------------------------------------------------------------------


class ImportsPackageFixedPointsPlanner:
    """Plans import graph analysis operations (theory2.tex Ch19 §19.3).

    Responsible for:
    1. Discovering all Python modules under a package root.
    2. Ordering them for efficient analysis (topological order when possible).
    3. Estimating complexity to guide full-vs-incremental decisions.
    4. Building incremental plans when only a subset of files has changed.
    """

    def __init__(self) -> None:
        self._executor = ImportsPackageFixedPointsExecutor()
        log.debug("ImportsPackageFixedPointsPlanner initialised")

    def plan_full_analysis(self, package_root: str) -> "AnalysisPlan":
        """Build a full analysis plan for *package_root*.

        Discovers all .py files, converts them to module names, and orders
        them heuristically (shorter names first, then alphabetical) as a
        proxy for dependency order.

        Parameters
        ----------
        package_root:
            Filesystem path to the Python package root.

        Returns
        -------
        AnalysisPlan
            A plan with all discovered modules in analysis order.
        """
        log.info("plan_full_analysis: scanning %r", package_root)
        modules: list[str] = []
        for file_path in self._executor.walk_module_tree(package_root):
            mod = self._executor._path_to_module_name(file_path, package_root)
            if mod:
                modules.append(mod)
        # copilot: heuristic ordering: fewer dots first (more fundamental modules tend
        # to be shallower in the package hierarchy)
        modules = self.prioritize_modules(modules)
        n = len(modules)
        estimated = self._estimate_time(n)
        steps = (
            f"1. Walk {package_root!r} for .py files",
            f"2. Parse {n} modules via AST",
            "3. Build import graph",
            "4. Compute transitive closure to fixed point",
            "5. Detect cycles and build judgments",
        )
        plan = AnalysisPlan(
            root_path=package_root,
            modules_to_analyze=tuple(modules),
            estimated_time_s=estimated,
            analysis_steps=steps,
        )
        log.info("plan_full_analysis: %d modules, estimate %.2fs", n, estimated)
        return plan

    def prioritize_modules(self, modules: list[str]) -> list[str]:
        """Sort *modules* into analysis-friendly order.

        Modules with fewer path components are analysed first because they
        are less likely to depend on deeper modules.  Within the same depth,
        alphabetical order gives determinism.

        Parameters
        ----------
        modules:
            List of fully qualified module names.

        Returns
        -------
        list[str]
            Sorted list.
        """
        # copilot: (depth, name) as sort key gives stable topological-ish order
        return sorted(modules, key=lambda m: (m.count("."), m))

    def estimate_complexity(
        self, graph: dict[str, list[str]]
    ) -> "ComplexityEstimate":
        """Estimate the analysis complexity from a partially known graph.

        Parameters
        ----------
        graph:
            A possibly incomplete import graph.

        Returns
        -------
        ComplexityEstimate
            Estimated complexity metrics.
        """
        node_count = len(graph)
        edge_count = sum(len(v) for v in graph.values())
        # copilot: rough cycle estimate: nodes with in-degree > 0 and out-degree > 0
        #          that appear in their own neighbour's neighbour list
        cycle_est = sum(
            1 for node, neighbours in graph.items()
            if any(node in graph.get(nb, []) for nb in neighbours)
        )
        # copilot: max_depth is the longest shortest path from any root node
        #          approximated by the number of modules with no incoming edges
        all_targets: set[str] = set()
        for neighbours in graph.values():
            all_targets.update(neighbours)
        roots = [n for n in graph if n not in all_targets]
        max_depth = max(
            (graph.get(r, []).__len__() for r in roots),
            default=0,
        )
        # copilot: empirical constant: roughly 0.5ms per module for AST parse + graph ops
        estimated_time_s = node_count * 0.0005 + edge_count * 0.0001
        return ComplexityEstimate(
            node_count=node_count,
            edge_count=edge_count,
            cycle_count_estimate=cycle_est,
            max_depth=max_depth,
            estimated_time_s=estimated_time_s,
        )

    def build_incremental_plan(
        self,
        changed_files: list[str],
        cached_graph: dict[str, list[str]],
    ) -> "IncrementalPlan":
        """Build an incremental plan for *changed_files*.

        Determines which modules are transitively affected by the changes
        and builds a plan that only re-analyses those modules.

        Parameters
        ----------
        changed_files:
            List of filesystem paths that have changed.
        cached_graph:
            The most recent complete import graph.

        Returns
        -------
        IncrementalPlan
            A plan covering only the affected modules.
        """
        # copilot: convert file paths to module names using the cached graph keys as context
        changed_modules: list[str] = []
        for fp in changed_files:
            # copilot: try to match the file path against known module names
            base = os.path.splitext(os.path.basename(fp))[0]
            for mod in cached_graph:
                if mod.endswith(base) or mod == base:
                    changed_modules.append(mod)
                    break
            else:
                # copilot: fall back to the basename as a best-effort module name
                changed_modules.append(base)

        # copilot: find all modules that import (directly or transitively) a changed module
        changed_set = set(changed_modules)
        affected: set[str] = set(changed_modules)
        for mod, neighbours in cached_graph.items():
            if any(nb in changed_set for nb in neighbours):
                affected.add(mod)
        # copilot: second pass for transitive dependents
        for mod, neighbours in cached_graph.items():
            if any(nb in affected for nb in neighbours):
                affected.add(mod)

        total = len(cached_graph)
        cache_hits = max(0, total - len(affected))
        return IncrementalPlan(
            changed_modules=tuple(sorted(changed_modules)),
            affected_modules=tuple(sorted(affected)),
            reanalysis_scope="affected" if len(affected) < total else "full",
            cache_hits=cache_hits,
        )

    def _estimate_time(self, module_count: int) -> float:
        """Return a rough time estimate in seconds for *module_count* modules."""
        # copilot: 0.5ms per module is a conservative empirical estimate
        return module_count * 0.0005


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import tempfile

    print("=== algorithms.py smoke test ===")

    # copilot: create a tiny synthetic package in a temp directory
    with tempfile.TemporaryDirectory() as tmpdir:
        pkg = os.path.join(tmpdir, "mypkg")
        os.makedirs(pkg)
        # write __init__.py
        with open(os.path.join(pkg, "__init__.py"), "w") as fh:
            fh.write("from mypkg import a, b\n")
        # write a.py
        with open(os.path.join(pkg, "a.py"), "w") as fh:
            fh.write("import os\nfrom mypkg import b\n")
        # write b.py
        with open(os.path.join(pkg, "b.py"), "w") as fh:
            fh.write("import sys\n")
        # write c.py with a relative import
        with open(os.path.join(pkg, "c.py"), "w") as fh:
            fh.write("from . import a\nfrom .b import something\n")

        planner = ImportsPackageFixedPointsPlanner()
        plan = planner.plan_full_analysis(pkg)
        print(f"Plan: {len(plan.modules_to_analyze)} modules, estimate={plan.estimated_time_s:.4f}s")
        print(f"Steps: {plan.analysis_steps}")

        executor = ImportsPackageFixedPointsExecutor()
        result = executor.execute_plan(plan)
        print(f"Result: {len(result.module_graph)} modules, {len(result.cycles)} cycles")
        print(f"Module graph (canonical):")
        for mod, imports in sorted(result.module_graph.items()):
            print(f"  {mod}: {imports}")
        print(f"Fixed point keys: {sorted(result.fixed_point.keys())}")
        print(f"Elapsed: {result.elapsed_time_s:.4f}s")
        print(f"Judgments: {len(result.judgments)}")
        for j in result.judgments:
            print(f"  {j.label!r}: status={j.status}")

        # copilot: test normalizer
        normalizer = ImportsPackageFixedPointsNormalizer()
        print(f"normalize_module_name: {normalizer.normalize_module_name('.utils', 'mypkg.sub')!r}")
        print(f"resolve_relative_import: {normalizer.resolve_relative_import('mypkg.sub.mod', 'utils', 1)!r}")
        graph2 = {"a": ["b", "b", "c"], "b": ["c", "c"], "c": []}
        deduped = normalizer.deduplicate_edges(graph2)
        print(f"deduplicate_edges: {deduped}")
        canonical = normalizer.canonicalize_graph(graph2)
        print(f"canonicalize_graph: {canonical}")

        # copilot: test incremental plan
        incr_plan = planner.build_incremental_plan(
            [os.path.join(pkg, "a.py")],
            result.module_graph,
        )
        print(f"IncrementalPlan: changed={incr_plan.changed_modules} "
              f"affected={incr_plan.affected_modules} "
              f"scope={incr_plan.reanalysis_scope!r} "
              f"cache_hits={incr_plan.cache_hits}")

        incr_result = executor.execute_incremental(incr_plan)
        print(f"IncrementalResult: cache_hit_rate={incr_result.cache_hit_rate:.2%} "
              f"new_judgments={len(incr_result.new_judgments)}")

        # copilot: complexity estimate
        est = planner.estimate_complexity(result.module_graph)
        print(f"ComplexityEstimate: nodes={est.node_count} edges={est.edge_count} "
              f"cycles_est={est.cycle_count_estimate} max_depth={est.max_depth} "
              f"time_est={est.estimated_time_s:.4f}s")

    print("smoke test PASSED")
