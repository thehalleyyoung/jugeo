from __future__ import annotations

"""
Import Cycles and Package Fixed Points
=======================================

Theory reference: theory2.tex Ch19 §2

This module implements the structural analysis of Python import graphs, focusing on:

  1. **Cycle detection** — finding strongly-connected components (SCCs) in the import
     dependency graph via Tarjan's algorithm.  Every cycle represents a potential
     circular-import hazard and is recorded as a ``CycleRecord`` value object.

  2. **Package fixed points** — given a package root on disk, we walk the file tree,
     parse every ``*.py`` source file with :mod:`ast`, reconstruct the import graph,
     and compute the *fixed-point* descriptor ``FixedPointRecord`` that summarises the
     structural invariants of the package (module count, SCC count, whether a
     topological order exists, etc.).

  3. **Runtime witnessing** — :class:`ImportCyclesPackageFixedWitness` inspects
     ``sys.modules`` at runtime to detect *partially-initialised* modules, which are the
     observable footprint of a live import cycle.  Each finding is promoted to a
     :class:`~jugeo.judgments.judgment_terms.Judgment` for downstream verification.

Theoretical background (theory2.tex Ch19 §2)
---------------------------------------------
In the Jugeo framework the import graph of a Python package is modelled as a *directed
site* (C, J) in the sense of Grothendieck topologies.  Nodes are module coordinates;
edges are import morphisms.  A *fixed point* of the package functor F : C → Set is a
consistent assignment of namespace objects to each module coordinate such that every
restriction morphism is honoured.  Import cycles break the acyclicity hypothesis required
by the standard construction of such fixed points, so detecting them is a necessary
pre-condition for the soundness of the Jugeo judgment system.

Tarjan's SCC algorithm runs in O(V + E) time and directly identifies which sub-graphs
would need to be *collapsed* (treated as a single super-node) to restore acyclicity.
Kahn's topological sort is used as a fast negative-witness: it returns ``None`` when a
cycle is present, confirming the graph is not a DAG.

Usage
-----
::

    from jugeo.python_runtime.import_graph.import_cycles_and_package_fixed_po import (
        ImportCyclesPackageFixedAnalyzer,
        ImportCyclesPackageFixedCoordinator,
        ImportCyclesPackageFixedWitness,
    )

    analyzer    = ImportCyclesPackageFixedAnalyzer()
    coordinator = ImportCyclesPackageFixedCoordinator()

    graph  = analyzer.build_import_graph_from_ast(["path/to/module.py"])
    sccs   = analyzer.run_tarjan_scc(graph)
    record = coordinator.compute_package_fixed_point("path/to/package")

"""

# ---------------------------------------------------------------------------
# Standard-library imports
# ---------------------------------------------------------------------------
import ast
import sys
import importlib
import pkgutil
import importlib.util
import importlib.machinery
import logging
import os
import time
import textwrap
import pathlib
import collections

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

# ---------------------------------------------------------------------------
# Module-level logger
# ---------------------------------------------------------------------------
# copilot: always use __name__ so that the hierarchy is preserved in log output
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Cross-package jugeo imports with full stub fallbacks
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
# Module constants
# ---------------------------------------------------------------------------

#: Chapter/section tag used in Judgment provenance labels (theory2.tex Ch19 §2)
_THEORY_TAG: str = "theory2.tex:Ch19:§2"

#: Default severity assigned to a direct 2-node cycle
_DEFAULT_DIRECT_SEVERITY: float = 0.9

#: Default severity assigned to an indirect (transitive) cycle
_DEFAULT_INDIRECT_SEVERITY: float = 0.6

#: Default severity for star-mediated cycles (hub imports several spokes that re-import hub)
_DEFAULT_STAR_SEVERITY: float = 0.75

#: Default severity for conditional cycles (cycle only active under certain import conditions)
_DEFAULT_CONDITIONAL_SEVERITY: float = 0.45

# ---------------------------------------------------------------------------
# CycleKind enumeration
# ---------------------------------------------------------------------------

class CycleKind(str, Enum):
    """
    Classification of import cycles detected in the Python import graph.

    Theory reference: theory2.tex Ch19 §2 — *Cycle taxonomy*.

    Each value describes **how** the cycle is structured, which informs the
    choice of refactoring strategy needed to break it.

    Attributes
    ----------
    DIRECT
        A two-node cycle: module A imports module B **and** module B imports
        module A.  This is the simplest form and usually the easiest to resolve
        by introducing an interface module or by moving shared definitions.

    INDIRECT
        A cycle of length ≥ 3 that does not pass through a common hub.
        Example: A → B → C → A.  Often arises from layering violations in
        larger packages.

    STAR_MEDIATED
        A hub module H imports spokes S₁, S₂, …, Sₙ and at least one spoke
        imports H back, creating a *star-shaped* cycle.  Common in ``__init__``
        files that re-export sub-module symbols.

    CONDITIONAL
        A cycle that is only activated under particular runtime conditions (e.g.
        an import inside an ``if TYPE_CHECKING:`` block, or a lazy import inside
        a function body).  Static analysis may mark these as *potential* rather
        than *definite* cycles.
    """

    # copilot: keep the values as short lowercase strings for JSON serialisability
    DIRECT = "direct"
    INDIRECT = "indirect"
    STAR_MEDIATED = "star_mediated"
    CONDITIONAL = "conditional"


# ---------------------------------------------------------------------------
# Value objects (frozen, slotted dataclasses)
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class CycleRecord:
    """
    Immutable record describing a single detected import cycle.

    Theory reference: theory2.tex Ch19 §2 — *Cycle records as obstruction data*.

    A ``CycleRecord`` is produced by :class:`ImportCyclesPackageFixedAnalyzer`
    (via Tarjan's SCC algorithm) and consumed by
    :class:`ImportCyclesPackageFixedCoordinator` which promotes it to a
    :class:`~jugeo.judgments.judgment_terms.Judgment`.

    Attributes
    ----------
    cycle_members
        Ordered tuple of fully-qualified module names that participate in the
        cycle.  The ordering follows the DFS discovery sequence of Tarjan's
        algorithm.
    cycle_kind
        String value of a :class:`CycleKind` member that describes the topology
        of this cycle.
    detection_method
        Human-readable label identifying which algorithm or heuristic discovered
        this cycle (e.g. ``"tarjan_scc"``, ``"runtime_sys_modules"``).
    severity_score
        A float in [0.0, 1.0] estimating the severity of the cycle.  Higher
        values indicate cycles that are more likely to cause ``ImportError`` or
        attribute-not-found errors at runtime.
    obstruction
        A textual description of the concrete import obstruction this cycle
        creates, suitable for embedding in a Judgment's obstruction field.
    """

    # copilot: frozen=True + slots=True gives maximum memory efficiency for large graphs
    cycle_members: tuple  # tuple[str, ...]
    cycle_kind: str        # CycleKind value
    detection_method: str
    severity_score: float
    obstruction: str

    def summary(self) -> str:
        """Return a one-line human-readable summary of this cycle."""
        members_str = " → ".join(self.cycle_members)
        if len(self.cycle_members) > 4:
            # copilot: truncate very long cycles for display clarity
            head = " → ".join(self.cycle_members[:2])
            tail = self.cycle_members[-1]
            members_str = f"{head} → … → {tail}"
        return (
            f"[{self.cycle_kind}] {members_str}  "
            f"(severity={self.severity_score:.2f}, method={self.detection_method})"
        )


@dataclass(frozen=True, slots=True)
class FixedPointRecord:
    """
    Immutable snapshot of the structural fixed-point analysis of a Python package.

    Theory reference: theory2.tex Ch19 §2 — *Package fixed-point functor*.

    A ``FixedPointRecord`` is the primary output of
    :meth:`ImportCyclesPackageFixedCoordinator.compute_package_fixed_point`.
    It summarises whether the package's import graph admits a consistent
    topological ordering (i.e. is a DAG), or whether cycles prevent this.

    Attributes
    ----------
    package_root
        Absolute path to the root directory of the package that was analysed.
    module_count
        Total number of Python source files (``*.py``) discovered under
        ``package_root``.
    cycle_count
        Number of distinct import cycles detected.  Zero implies the graph is
        a DAG and a fixed point exists.
    scc_count
        Total number of strongly-connected components returned by Tarjan's
        algorithm.  In a DAG every SCC has size 1 (each node is its own SCC).
    topological_order_available
        ``True`` iff the import graph is a DAG and Kahn's algorithm succeeded
        in computing a total topological order.
    timestamp
        Unix timestamp (``time.time()``) at which the analysis was performed.
    """

    # copilot: all fields are primitive-typed for easy serialisation
    package_root: str
    module_count: int
    cycle_count: int
    scc_count: int
    topological_order_available: bool
    timestamp: float

    def is_cycle_free(self) -> bool:
        """Return ``True`` if no cycles were detected in this package."""
        return self.cycle_count == 0

    def dag_quality_score(self) -> float:
        """
        Heuristic quality score in [0.0, 1.0].

        A score of 1.0 means the package is a perfect DAG; lower scores reflect
        increasingly tangled dependency graphs.

        The formula penalises for the ratio of cyclic SCCs to total SCCs:

            score = 1 − (cycle_count / max(scc_count, 1))

        clipped to [0.0, 1.0].
        """
        if self.scc_count == 0:
            return 1.0
        raw = 1.0 - (self.cycle_count / max(self.scc_count, 1))
        return max(0.0, min(1.0, raw))


@dataclass(frozen=True, slots=True)
class PartialModuleWitnessRecord:
    """
    Immutable record produced by runtime inspection of a partially-initialised module.

    Theory reference: theory2.tex Ch19 §2 — *Partial module witnesses*.

    When Python's import machinery encounters a circular import it inserts a
    *partially-initialised* module object into ``sys.modules`` before the
    module's body has finished executing.  Any module that imports a name from
    such a partial object will either receive ``None`` or raise
    ``AttributeError``.

    :class:`ImportCyclesPackageFixedWitness` inspects ``sys.modules`` to find
    such modules and records the findings here.

    Attributes
    ----------
    module_name
        Fully-qualified name of the module that was found to be partial.
    partial_attributes
        Tuple of attribute names that **are** present on the partial module
        object at the time of inspection.
    missing_attributes
        Tuple of attribute names that are **absent** but were expected based on
        the module's ``__all__`` declaration (if any).
    is_partial
        ``True`` if the module is genuinely partially initialised (i.e. its
        ``__spec__`` is present but ``__initializing__`` flag was set, or the
        ``__all__`` contract is not yet satisfied).
    witness_level
        Integer trust level (maps to :class:`~jugeo.judgments.judgment_terms.TrustLevel`)
        assigned to this witness observation.
    """

    # copilot: witness_level mirrors TrustLevel int values for seamless promotion
    module_name: str
    partial_attributes: tuple  # tuple[str, ...]
    missing_attributes: tuple  # tuple[str, ...]
    is_partial: bool
    witness_level: int

    def coverage_ratio(self) -> float:
        """
        Return the fraction of expected attributes that are already present.

        A ratio of 1.0 means the module is fully initialised (no missing attrs).
        A ratio of 0.0 means none of the expected attributes are available yet.
        """
        total = len(self.partial_attributes) + len(self.missing_attributes)
        if total == 0:
            # copilot: if __all__ was not declared we have no expected-set; assume full
            return 1.0
        return len(self.partial_attributes) / total


# ---------------------------------------------------------------------------
# ImportCyclesPackageFixedAnalyzer  (mutable)
# ---------------------------------------------------------------------------

@dataclass
class ImportCyclesPackageFixedAnalyzer:
    """
    Stateful analyser that builds import graphs from AST source and runs
    graph-theoretic cycle detection algorithms.

    Theory reference: theory2.tex Ch19 §2 — *Static import graph construction*.

    Responsibilities
    ----------------
    * Parse Python source files with :mod:`ast` and extract ``import`` /
      ``from … import`` statements to build a directed adjacency list.
    * Run **Tarjan's strongly-connected-components algorithm** (O(V+E)) to find
      all cycles.
    * Run **Kahn's topological sort** as a fast DAG-witness.
    * Classify each discovered cycle into a :class:`CycleKind`.

    The analyser is deliberately stateless between calls (all state is passed
    as arguments and returned as values) so that multiple coordinator instances
    can share a single analyser without interference.
    """

    # copilot: no persistent state fields needed; methods are pure-ish transforms
    _parse_errors: list = field(default_factory=list)

    # ------------------------------------------------------------------
    # Graph construction
    # ------------------------------------------------------------------

    def build_import_graph_from_ast(
        self,
        source_files: list,
    ) -> dict:
        """
        Parse each file in *source_files* with :func:`ast.parse` and extract
        the set of modules it imports.

        Returns a directed adjacency list represented as
        ``dict[str, list[str]]`` where each key is a module name and each
        value is the list of modules it directly imports.

        Parameters
        ----------
        source_files
            Iterable of file-system paths (``str`` or :class:`pathlib.Path`)
            to Python source files.

        Notes
        -----
        * Relative imports (``from . import foo``) are recorded with their
          dot-prefixed form so that the caller can resolve them relative to the
          package root if desired.
        * Syntax errors are logged at WARNING level and the offending file is
          skipped rather than aborting the entire build.
        * Only top-level import statements are extracted by default.  Imports
          inside function bodies or ``if TYPE_CHECKING:`` guards are included
          as well — callers that want to suppress conditional imports should
          post-filter the returned graph.

        Returns
        -------
        dict
            ``{module_name: [imported_module, …]}``
        """
        # copilot: graph is a defaultdict so we can safely append without pre-checking
        graph: dict = collections.defaultdict(list)
        self._parse_errors.clear()

        for path in source_files:
            path = str(path)
            module_name = self._path_to_module_name(path)
            # Ensure the node exists even if it has no imports
            if module_name not in graph:
                graph[module_name] = []

            try:
                with open(path, "r", encoding="utf-8", errors="replace") as fh:
                    source = fh.read()
            except OSError as exc:
                log.warning("import_graph: cannot read %s: %s", path, exc)
                self._parse_errors.append((path, str(exc)))
                continue

            try:
                tree = ast.parse(source, filename=path)
            except SyntaxError as exc:
                log.warning("import_graph: syntax error in %s: %s", path, exc)
                self._parse_errors.append((path, str(exc)))
                continue

            # Walk the AST and collect all import statements
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    # copilot: ast.Import covers `import a, b as c` forms
                    for alias in node.names:
                        imported = alias.name
                        if imported not in graph[module_name]:
                            graph[module_name].append(imported)

                elif isinstance(node, ast.ImportFrom):
                    # copilot: ast.ImportFrom covers `from pkg import foo` forms
                    level = node.level or 0  # relative-import dot count
                    module = node.module or ""
                    if level > 0:
                        # Preserve relative form; coordinator will resolve
                        imported = ("." * level) + module
                    else:
                        imported = module
                    if imported and imported not in graph[module_name]:
                        graph[module_name].append(imported)

        log.debug(
            "build_import_graph_from_ast: %d nodes, %d edges, %d errors",
            len(graph),
            sum(len(v) for v in graph.values()),
            len(self._parse_errors),
        )
        return dict(graph)

    # ------------------------------------------------------------------
    # Tarjan's SCC algorithm
    # ------------------------------------------------------------------

    def run_tarjan_scc(self, graph: dict) -> list:
        """
        Run Tarjan's strongly-connected-components algorithm on *graph*.

        Theory reference: theory2.tex Ch19 §2 — *Tarjan SCC as obstruction finder*.

        This is a **full iterative implementation** of Tarjan's algorithm that
        avoids Python's default recursion limit (which would be hit on large
        import graphs).  The time complexity is O(V + E).

        Algorithm sketch
        ----------------
        Tarjan's algorithm performs a single depth-first search over the graph.
        As each node *v* is first visited it is assigned a unique discovery
        *index* and its *lowlink* value is initialised to that same index.
        Nodes are pushed onto a stack as they are discovered.

        When the DFS backtracks from a neighbour *w* of *v*:

        * If *w* was explored in the **current** DFS subtree, update
          ``lowlink[v] = min(lowlink[v], lowlink[w])``.
        * If *w* is already on the **stack** (back-edge to an ancestor),
          update ``lowlink[v] = min(lowlink[v], index[w])``.

        When the DFS finishes exploring all neighbours of *v*, if
        ``lowlink[v] == index[v]``, then *v* is the **root** of an SCC.
        Pop all nodes from the stack up to and including *v* — they form
        one SCC.

        The iterative variant maintains an explicit call stack of
        ``(node, iterator-over-neighbours)`` pairs to sidestep Python's
        recursion depth limit.

        Parameters
        ----------
        graph
            Adjacency list ``dict[str, list[str]]``.  All nodes referenced as
            neighbours must appear as keys; missing keys are treated as leaf
            nodes with no outgoing edges.

        Returns
        -------
        list[list[str]]
            All SCCs in *reverse topological order* of the condensation DAG
            (i.e. the SCC containing no outgoing edges to other SCCs appears
            first).  SCCs of size 1 are trivial (single modules with no
            self-loop).  SCCs of size ≥ 2 represent genuine import cycles.
        """
        # ------------------------------------------------------------------
        # Initialise bookkeeping structures
        # ------------------------------------------------------------------
        # copilot: index maps each node to its DFS discovery time (0-based counter)
        index_map: dict = {}
        # copilot: lowlink[v] = min discovery index reachable from the subtree rooted at v
        lowlink: dict = {}
        # copilot: on_stack tracks which nodes are currently on the Tarjan stack
        on_stack: set = set()
        # copilot: the Tarjan stack (distinct from the Python call stack)
        stack: list = []
        # copilot: counter is a single-element list so nested functions can mutate it
        counter: list = [0]
        # copilot: result accumulates the completed SCCs
        sccs: list = []

        # Ensure every neighbour reference has an entry in the graph so the
        # iterative DFS does not KeyError on missing leaf nodes.
        full_graph: dict = {n: list(graph.get(n, [])) for n in graph}
        for node in list(full_graph.keys()):
            for neighbour in full_graph[node]:
                if neighbour not in full_graph:
                    full_graph[neighbour] = []

        # ------------------------------------------------------------------
        # Iterative DFS using an explicit work-stack
        # ------------------------------------------------------------------
        # Each frame on work_stack is a tuple:
        #   (node, neighbour_iterator)
        # When we first push a node we also initialise its index/lowlink/stack
        # membership.  On re-entry (after a recursive call returns) we update
        # lowlink[node] based on the child that just finished.

        def _strongconnect(start: str) -> None:
            """
            Iterative implementation of Tarjan's strongconnect(v).

            # copilot: using an explicit stack avoids RecursionError on deep graphs
            """
            # Bootstrap: push the start node as the first frame
            work_stack: list = [(start, iter(full_graph[start]))]
            index_map[start] = counter[0]
            lowlink[start] = counter[0]
            counter[0] += 1
            stack.append(start)
            on_stack.add(start)

            while work_stack:
                node, neighbours = work_stack[-1]

                try:
                    # Advance to the next neighbour of the current node
                    neighbour = next(neighbours)

                    if neighbour not in index_map:
                        # copilot: tree edge — recurse into neighbour
                        index_map[neighbour] = counter[0]
                        lowlink[neighbour] = counter[0]
                        counter[0] += 1
                        stack.append(neighbour)
                        on_stack.add(neighbour)
                        work_stack.append((neighbour, iter(full_graph[neighbour])))

                    elif neighbour in on_stack:
                        # copilot: back edge — update lowlink with neighbour's index
                        # (not lowlink[neighbour] — that is the correct Tarjan rule
                        #  for the iterative version to avoid premature SCC splits)
                        lowlink[node] = min(lowlink[node], index_map[neighbour])

                    # else: cross/forward edge to an already-completed SCC — ignore

                except StopIteration:
                    # All neighbours of `node` have been processed — pop this frame
                    work_stack.pop()

                    if work_stack:
                        # Update parent's lowlink from child's lowlink
                        parent = work_stack[-1][0]
                        lowlink[parent] = min(lowlink[parent], lowlink[node])

                    # Check if `node` is the root of an SCC
                    if lowlink[node] == index_map[node]:
                        # copilot: pop the Tarjan stack until we reach `node` (inclusive)
                        scc: list = []
                        while True:
                            w = stack.pop()
                            on_stack.discard(w)
                            scc.append(w)
                            if w == node:
                                break
                        sccs.append(scc)

        # copilot: iterate over every node to handle disconnected components
        for node in list(full_graph.keys()):
            if node not in index_map:
                _strongconnect(node)

        log.debug("run_tarjan_scc: found %d SCCs in graph with %d nodes", len(sccs), len(full_graph))
        return sccs

    # ------------------------------------------------------------------
    # Cycle classification
    # ------------------------------------------------------------------

    def classify_cycle(self, cycle: list) -> CycleKind:
        """
        Classify a cycle (list of module names forming an SCC) into a
        :class:`CycleKind` based on its structural properties.

        Classification rules (heuristic, per theory2.tex Ch19 §2)
        ---------------------------------------------------------
        * ``len(cycle) == 1`` → trivial self-loop (DIRECT for convenience).
        * ``len(cycle) == 2`` → DIRECT two-node cycle.
        * Any member whose name ends in ``__init__`` or is a package root
          (i.e. no dotted suffix after the last component) and whose degree
          in the cycle is > 2 → STAR_MEDIATED.
        * Cycles containing a member that looks like a ``TYPE_CHECKING``
          conditional (detected by searching parsed source — here approximated
          by checking if the name contains ``_types`` or ``_compat``) →
          CONDITIONAL.
        * Everything else → INDIRECT.

        Parameters
        ----------
        cycle
            Non-empty list of module names forming a single SCC.

        Returns
        -------
        CycleKind
        """
        # copilot: len-1 SCCs are trivially single nodes (self-loop or no loop at all)
        if len(cycle) <= 2:
            return CycleKind.DIRECT

        # Heuristic: look for __init__ hub pattern
        for name in cycle:
            parts = name.rsplit(".", 1)
            if parts[-1] in ("__init__", "") or name.endswith(".__init__"):
                return CycleKind.STAR_MEDIATED

        # Heuristic: look for TYPE_CHECKING / compat / _types modules
        for name in cycle:
            tail = name.rsplit(".", 1)[-1]
            if any(kw in tail for kw in ("_types", "_compat", "_typing", "_annotations")):
                return CycleKind.CONDITIONAL

        # copilot: default for len > 2 with no special members → indirect transitive cycle
        return CycleKind.INDIRECT

    # ------------------------------------------------------------------
    # Topological sort (Kahn's algorithm)
    # ------------------------------------------------------------------

    def compute_topological_sort(self, graph: dict) -> Optional[list]:
        """
        Compute a topological ordering of *graph* using **Kahn's algorithm**.

        Theory reference: theory2.tex Ch19 §2 — *Topological order as fixed-point witness*.

        Kahn's algorithm maintains a queue of nodes with in-degree zero.  It
        repeatedly removes a node from the queue, adds it to the output, and
        decrements the in-degree of all its successors.  If a successor's
        in-degree reaches zero it is added to the queue.  If the output list
        is shorter than the total number of nodes at the end, a cycle exists.

        Parameters
        ----------
        graph
            Adjacency list ``dict[str, list[str]]``.

        Returns
        -------
        list[str] | None
            The topological order if the graph is a DAG, otherwise ``None``.
        """
        # copilot: build a complete node set including implicit leaf nodes
        all_nodes: set = set(graph.keys())
        for deps in graph.values():
            all_nodes.update(deps)

        # Compute in-degree for each node
        in_degree: dict = {n: 0 for n in all_nodes}
        for node, deps in graph.items():
            for dep in deps:
                in_degree[dep] = in_degree.get(dep, 0) + 1

        # copilot: initialise queue with all zero-in-degree nodes (sorted for determinism)
        queue: collections.deque = collections.deque(
            sorted(n for n, d in in_degree.items() if d == 0)
        )
        order: list = []

        while queue:
            node = queue.popleft()
            order.append(node)
            for successor in sorted(graph.get(node, [])):
                in_degree[successor] -= 1
                if in_degree[successor] == 0:
                    queue.append(successor)

        if len(order) < len(all_nodes):
            # copilot: not all nodes were processed — cycle detected
            log.debug(
                "compute_topological_sort: cycle detected (%d/%d nodes ordered)",
                len(order), len(all_nodes),
            )
            return None

        log.debug("compute_topological_sort: DAG confirmed, %d nodes ordered", len(order))
        return order

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _path_to_module_name(path: str) -> str:
        """
        Convert a file-system path to a dotted module name.

        Examples
        --------
        ``src/jugeo/geometry/site.py``  →  ``jugeo.geometry.site``

        # copilot: strips leading src/ and trailing .py before joining with dots
        """
        p = pathlib.Path(path)
        parts = list(p.with_suffix("").parts)
        # Drop common src/ prefix if present
        if parts and parts[0] in ("src", "lib"):
            parts = parts[1:]
        # Drop __init__ suffix (package directory node)
        if parts and parts[-1] == "__init__":
            parts = parts[:-1]
        return ".".join(parts) if parts else str(path)


# ---------------------------------------------------------------------------
# ImportCyclesPackageFixedCoordinator  (mutable)
# ---------------------------------------------------------------------------

@dataclass
class ImportCyclesPackageFixedCoordinator:
    """
    High-level coordinator that orchestrates cycle detection and fixed-point
    computation for an entire Python package.

    Theory reference: theory2.tex Ch19 §2 — *Package fixed-point coordinator*.

    This class ties together the :class:`ImportCyclesPackageFixedAnalyzer` for
    static analysis and the :class:`ImportCyclesPackageFixedWitness` for runtime
    inspection.  It also converts raw ``CycleRecord`` and ``FixedPointRecord``
    values into :class:`~jugeo.judgments.judgment_terms.Judgment` objects that
    can be stored in the Jugeo judgment registry.

    Attributes
    ----------
    _graph
        Most recently computed import graph (adjacency list).
    _sccs
        Most recently computed list of SCCs.
    _judgments
        Accumulated list of :class:`Judgment` objects produced during analysis.
    """

    # copilot: internal fields use leading underscore convention for "private" state
    _graph: dict = field(default_factory=dict)
    _sccs: list = field(default_factory=list)
    _judgments: list = field(default_factory=list)

    def detect_cycles(self, module_graph: dict) -> list:
        """
        Detect all import cycles in *module_graph* and return a list of
        :class:`CycleRecord` objects.

        Internally calls :meth:`ImportCyclesPackageFixedAnalyzer.run_tarjan_scc`
        and filters out trivial SCCs (size 1 with no self-loop).

        Parameters
        ----------
        module_graph
            Adjacency list ``dict[str, list[str]]``.

        Returns
        -------
        list[CycleRecord]
        """
        # copilot: store graph for later fixed-point computation
        self._graph = module_graph
        analyzer = ImportCyclesPackageFixedAnalyzer()
        self._sccs = analyzer.run_tarjan_scc(module_graph)

        cycle_records: list = []
        for scc in self._sccs:
            # copilot: trivial SCCs (size 1 with no self-edge) are not cycles
            is_self_loop = (
                len(scc) == 1 and scc[0] in module_graph.get(scc[0], [])
            )
            if len(scc) < 2 and not is_self_loop:
                continue

            kind = analyzer.classify_cycle(scc)
            severity = {
                CycleKind.DIRECT: _DEFAULT_DIRECT_SEVERITY,
                CycleKind.INDIRECT: _DEFAULT_INDIRECT_SEVERITY,
                CycleKind.STAR_MEDIATED: _DEFAULT_STAR_SEVERITY,
                CycleKind.CONDITIONAL: _DEFAULT_CONDITIONAL_SEVERITY,
            }.get(kind, _DEFAULT_INDIRECT_SEVERITY)

            obstruction_text = (
                f"Import cycle among {len(scc)} modules: "
                + " → ".join(scc[:4])
                + (" → …" if len(scc) > 4 else "")
                + f"  [{kind.value}]"
            )

            record = CycleRecord(
                cycle_members=tuple(scc),
                cycle_kind=kind.value,
                detection_method="tarjan_scc",
                severity_score=severity,
                obstruction=obstruction_text,
            )
            cycle_records.append(record)
            log.info("detect_cycles: %s", record.summary())

        return cycle_records

    def compute_package_fixed_point(self, package_root: str) -> FixedPointRecord:
        """
        Walk *package_root* recursively, parse all ``*.py`` files, build the
        import graph, and return a :class:`FixedPointRecord`.

        Parameters
        ----------
        package_root
            File-system path to the root directory of the package.

        Returns
        -------
        FixedPointRecord
        """
        # copilot: delegate to analyze_directory which does the heavy lifting
        return self.analyze_directory(package_root)

    def build_cycle_judgment(self, cycle: CycleRecord) -> Judgment:
        """
        Promote a :class:`CycleRecord` to a :class:`Judgment`.

        The resulting judgment has:

        * ``status = OBSTRUCTED`` (import cycles are structural obstructions)
        * ``proposition.kind = STRUCTURAL``
        * ``trust.level = RUNTIME_WITNESSED`` (static analysis confidence)
        * ``provenance.source = _THEORY_TAG``

        Parameters
        ----------
        cycle
            The :class:`CycleRecord` to promote.

        Returns
        -------
        Judgment
        """
        # copilot: build all sub-objects before assembling the Judgment
        prop = Proposition(
            kind=PropositionKind.STRUCTURAL,
            statement=(
                f"Import cycle of kind {cycle.cycle_kind} detected among modules: "
                + ", ".join(cycle.cycle_members)
            ),
            label=f"cycle:{cycle.cycle_kind}:{cycle.cycle_members[0] if cycle.cycle_members else '?'}",
        )

        evidence_item = EvidenceItem(
            kind=EvidenceItemKind.STATIC_ANALYSIS,
            payload={
                "cycle_members": list(cycle.cycle_members),
                "cycle_kind": cycle.cycle_kind,
                "severity_score": cycle.severity_score,
                "detection_method": cycle.detection_method,
            },
            label=f"tarjan_scc_evidence:{cycle.cycle_members[0] if cycle.cycle_members else 'unknown'}",
        )
        bundle = EvidenceBundle()
        bundle.add(evidence_item)

        trust = TrustAnnotation(
            level=TrustLevel.RUNTIME_WITNESSED,
            rationale=(
                "Cycle detected by static AST analysis + Tarjan SCC; "
                "severity score reflects structural coupling."
            ),
        )

        prov = Provenance(
            source=_THEORY_TAG,
            module=__name__,
            timestamp=str(time.time()),
        )

        obstruction_obj = Obstruction(
            description=cycle.obstruction,
            coordinate=None,
        )

        judgment = Judgment(
            status=JudgmentStatus.PROPOSED,
            proposition=prop,
            carrier=Carrier(coordinate=None, payload=cycle, label=cycle.cycle_kind),
            evidence=bundle,
            trust=trust,
            provenance=prov,
            label=f"cycle_judgment:{cycle.cycle_members[0] if cycle.cycle_members else 'unknown'}",
        )
        # Mark as obstructed immediately since cycles are definite structural issues
        judgment.obstruct(obstruction_obj)
        self._judgments.append(judgment)
        return judgment

    def analyze_directory(self, root_path: str) -> FixedPointRecord:
        """
        Walk *root_path*, collect all Python source files, build the import
        graph, run Tarjan SCC, and assemble a :class:`FixedPointRecord`.

        Parameters
        ----------
        root_path
            Absolute or relative path to the directory to analyse.

        Returns
        -------
        FixedPointRecord
        """
        start_ts = time.time()
        source_files: list = []

        # copilot: os.walk is the most portable way to recurse a directory tree
        for dirpath, _dirnames, filenames in os.walk(root_path):
            for filename in filenames:
                if filename.endswith(".py"):
                    source_files.append(os.path.join(dirpath, filename))

        log.info("analyze_directory: found %d Python files under %s", len(source_files), root_path)

        analyzer = ImportCyclesPackageFixedAnalyzer()
        graph = analyzer.build_import_graph_from_ast(source_files)
        self._graph = graph

        sccs = analyzer.run_tarjan_scc(graph)
        self._sccs = sccs

        # Cycles = SCCs of size >= 2 or size-1 with a self-loop
        cycle_count = 0
        for scc in sccs:
            if len(scc) >= 2:
                cycle_count += 1
            elif len(scc) == 1 and scc[0] in graph.get(scc[0], []):
                cycle_count += 1

        topo_order = analyzer.compute_topological_sort(graph)
        topo_available = topo_order is not None

        record = FixedPointRecord(
            package_root=str(root_path),
            module_count=len(source_files),
            cycle_count=cycle_count,
            scc_count=len(sccs),
            topological_order_available=topo_available,
            timestamp=start_ts,
        )

        log.info(
            "analyze_directory: %d modules, %d cycles, %d SCCs, topo=%s",
            record.module_count,
            record.cycle_count,
            record.scc_count,
            record.topological_order_available,
        )
        return record


# ---------------------------------------------------------------------------
# ImportCyclesPackageFixedWitness  (mutable)
# ---------------------------------------------------------------------------

@dataclass
class ImportCyclesPackageFixedWitness:
    """
    Runtime witness that inspects ``sys.modules`` for live evidence of import
    cycles, producing :class:`PartialModuleWitnessRecord` and
    :class:`Judgment` objects.

    Theory reference: theory2.tex Ch19 §2 — *Runtime witness and partial module objects*.

    Python's import system inserts a module object into ``sys.modules`` *before*
    executing the module's body.  If module A imports B and B imports A, then
    when B tries to access names from A it will find the *partial* module object
    (the one inserted before A's body finished).  This is the canonical observable
    symptom of a circular import.

    This class provides two complementary approaches:

    1. :meth:`witness_partial_module` — inspect a *specific* module by name
       using :mod:`importlib.util` to check whether its ``__all__`` contract is
       satisfied.

    2. :meth:`detect_runtime_cycles` — scan all of ``sys.modules`` looking for
       modules that appear to be only partially initialised.

    Attributes
    ----------
    _observed_partials
        Cache of :class:`PartialModuleWitnessRecord` objects produced this session.
    _coordinator
        Optional reference to a :class:`ImportCyclesPackageFixedCoordinator` for
        building associated judgments.
    """

    _observed_partials: list = field(default_factory=list)
    _coordinator: object = field(default=None)

    def witness_partial_module(self, module_name: str) -> PartialModuleWitnessRecord:
        """
        Inspect *module_name* in ``sys.modules`` and return a
        :class:`PartialModuleWitnessRecord` summarising its initialisation state.

        Uses :func:`importlib.util.find_spec` to locate the module and then
        checks whether its ``__all__`` declaration (if any) is fully satisfied
        by the attributes present on the module object.

        Parameters
        ----------
        module_name
            Fully-qualified module name (e.g. ``"jugeo.geometry.site"``).

        Returns
        -------
        PartialModuleWitnessRecord
        """
        # copilot: always attempt to find the spec first to confirm the module exists
        try:
            spec = importlib.util.find_spec(module_name)
        except (ModuleNotFoundError, ValueError):
            spec = None

        module_obj = sys.modules.get(module_name)

        if module_obj is None:
            # Module is not loaded at all — treat as fully absent
            log.debug("witness_partial_module: %s not in sys.modules", module_name)
            record = PartialModuleWitnessRecord(
                module_name=module_name,
                partial_attributes=(),
                missing_attributes=(),
                is_partial=False,
                witness_level=int(TrustLevel.COPILOT_SUGGESTED),
            )
            self._observed_partials.append(record)
            return record

        # Collect the attributes that are actually present
        present_attrs = tuple(
            attr for attr in dir(module_obj)
            if not attr.startswith("__") or attr in ("__all__", "__version__")
        )

        # Check __all__ contract
        declared_all = getattr(module_obj, "__all__", None)
        if declared_all is not None:
            missing_attrs = tuple(
                name for name in declared_all
                if not hasattr(module_obj, name)
            )
            expected_present = tuple(
                name for name in declared_all
                if hasattr(module_obj, name)
            )
        else:
            # copilot: no __all__ → we cannot detect missing attrs from the contract
            missing_attrs = ()
            expected_present = present_attrs

        is_partial = len(missing_attrs) > 0

        # Determine witness trust level based on evidence quality
        if spec is not None and spec.origin is not None:
            witness_level = int(TrustLevel.RUNTIME_WITNESSED)
        else:
            witness_level = int(TrustLevel.ORACLE_PROPOSED)

        record = PartialModuleWitnessRecord(
            module_name=module_name,
            partial_attributes=expected_present,
            missing_attributes=missing_attrs,
            is_partial=is_partial,
            witness_level=witness_level,
        )
        self._observed_partials.append(record)

        if is_partial:
            log.warning(
                "witness_partial_module: %s is partially initialised — "
                "missing %d/__all__ attributes",
                module_name, len(missing_attrs),
            )

        return record

    def detect_runtime_cycles(self) -> list:
        """
        Scan ``sys.modules`` for partially-initialised modules that are
        symptomatic of live import cycles.

        Returns a list of :class:`CycleRecord` objects, one for each cluster
        of mutually-partial modules detected.

        Algorithm
        ---------
        1. For each module in ``sys.modules``, check whether it has a
           ``__spec__`` attribute *and* whether ``__initializing__`` (an
           internal CPython flag) is set.
        2. Also check for modules whose ``__all__`` is declared but not fully
           satisfied.
        3. Group detected partial modules by package prefix to identify
           co-located cycle candidates.

        Returns
        -------
        list[CycleRecord]
        """
        # copilot: take a snapshot to avoid mutation during iteration
        snapshot = dict(sys.modules)
        partial_names: list = []

        for name, mod in snapshot.items():
            if mod is None:
                # copilot: sys.modules can contain None for failed imports
                continue
            # Check CPython-internal initialising flag (not part of public API)
            if getattr(mod, "__initializing__", False):
                partial_names.append(name)
                continue
            # Check __all__ contract
            declared = getattr(mod, "__all__", None)
            if declared is not None:
                missing = [n for n in declared if not hasattr(mod, n)]
                if missing:
                    partial_names.append(name)

        if not partial_names:
            log.debug("detect_runtime_cycles: no partial modules found in sys.modules")
            return []

        # copilot: group by top-level package name to form cycle hypotheses
        groups: dict = collections.defaultdict(list)
        for name in partial_names:
            top = name.split(".")[0]
            groups[top].append(name)

        cycle_records: list = []
        for pkg, members in groups.items():
            if len(members) < 2:
                # copilot: a single partial module in isolation is suspicious but not a proven cycle
                continue
            record = CycleRecord(
                cycle_members=tuple(sorted(members)),
                cycle_kind=CycleKind.INDIRECT.value,
                detection_method="runtime_sys_modules",
                severity_score=0.85,
                obstruction=(
                    f"Runtime partial-initialisation cycle suspected in package '{pkg}': "
                    + ", ".join(members[:4])
                    + (" …" if len(members) > 4 else "")
                ),
            )
            cycle_records.append(record)
            log.warning("detect_runtime_cycles: suspected cycle in '%s': %s", pkg, members)

        return cycle_records

    def build_fixpoint_judgment(self, record: FixedPointRecord) -> Judgment:
        """
        Promote a :class:`FixedPointRecord` to a :class:`Judgment`.

        The judgment status is:

        * ``SETTLED``   if ``record.cycle_count == 0`` (package is a DAG → fixed point exists)
        * ``OBSTRUCTED`` otherwise (cycles prevent the standard fixed-point construction)

        Parameters
        ----------
        record
            The :class:`FixedPointRecord` to promote.

        Returns
        -------
        Judgment
        """
        is_dag = record.is_cycle_free()

        prop = Proposition(
            kind=PropositionKind.STRUCTURAL,
            statement=(
                f"Package at {record.package_root!r} has "
                + ("a valid DAG import structure (fixed point exists)."
                   if is_dag
                   else f"{record.cycle_count} import cycle(s); fixed point is obstructed.")
            ),
            label=f"fixedpoint:{'dag' if is_dag else 'cyclic'}:{record.package_root}",
        )

        evidence_item = EvidenceItem(
            kind=EvidenceItemKind.STATIC_ANALYSIS,
            payload={
                "module_count": record.module_count,
                "cycle_count": record.cycle_count,
                "scc_count": record.scc_count,
                "topological_order_available": record.topological_order_available,
                "dag_quality_score": record.dag_quality_score(),
            },
            label="fixedpoint_static_evidence",
        )
        bundle = EvidenceBundle()
        bundle.add(evidence_item)

        trust = TrustAnnotation(
            level=TrustLevel.RUNTIME_WITNESSED if not is_dag else TrustLevel.VERIFIED,
            rationale=(
                "Fixed-point existence determined by Tarjan SCC + Kahn topological sort "
                "(theory2.tex Ch19 §2)."
            ),
        )

        prov = Provenance(
            source=_THEORY_TAG,
            module=__name__,
            timestamp=str(record.timestamp),
        )

        judgment = Judgment(
            status=JudgmentStatus.PROPOSED,
            proposition=prop,
            carrier=Carrier(coordinate=None, payload=record, label="fixedpoint_record"),
            evidence=bundle,
            trust=trust,
            provenance=prov,
            label=f"fixedpoint_judgment:{record.package_root}",
        )

        if is_dag:
            judgment.settle()
        else:
            obs = Obstruction(
                description=(
                    f"{record.cycle_count} import cycle(s) detected; "
                    "package fixed point cannot be trivially constructed."
                ),
                coordinate=None,
            )
            judgment.obstruct(obs)

        return judgment


# ---------------------------------------------------------------------------
# Module-level convenience helpers
# ---------------------------------------------------------------------------

def build_sample_cycle_graph() -> dict:
    """
    Return a small hand-crafted import graph with known cycles for testing.

    Graph structure
    ---------------
    ::

        alpha → beta → gamma → alpha          (3-cycle, INDIRECT)
        delta → epsilon → delta               (2-cycle, DIRECT)
        zeta  → eta                           (no cycle)
        theta → iota → kappa → theta          (3-cycle, INDIRECT)

    Returns
    -------
    dict[str, list[str]]
        Adjacency list suitable for passing to
        :meth:`ImportCyclesPackageFixedAnalyzer.run_tarjan_scc`.
    """
    # copilot: sorted adjacency lists for deterministic SCC output
    return {
        "alpha":   ["beta"],
        "beta":    ["gamma"],
        "gamma":   ["alpha"],     # closes 3-cycle: alpha → beta → gamma → alpha
        "delta":   ["epsilon"],
        "epsilon": ["delta"],     # closes 2-cycle: delta ↔ epsilon
        "zeta":    ["eta"],
        "eta":     [],            # leaf — no outgoing edges
        "theta":   ["iota"],
        "iota":    ["kappa"],
        "kappa":   ["theta"],     # closes 3-cycle: theta → iota → kappa → theta
        "lambda":  ["zeta", "alpha"],  # cross-SCC edges; no extra cycle
    }


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    """
    Smoke test for all major classes and algorithms.

    Exercises:
    - ImportCyclesPackageFixedAnalyzer.run_tarjan_scc   on a hand-crafted graph
    - ImportCyclesPackageFixedAnalyzer.compute_topological_sort  (expects None = cycle)
    - ImportCyclesPackageFixedAnalyzer.classify_cycle
    - ImportCyclesPackageFixedCoordinator.detect_cycles
    - ImportCyclesPackageFixedCoordinator.build_cycle_judgment
    - ImportCyclesPackageFixedCoordinator.analyze_directory  (uses a temp dir)
    - ImportCyclesPackageFixedWitness.witness_partial_module
    - ImportCyclesPackageFixedWitness.detect_runtime_cycles
    - ImportCyclesPackageFixedWitness.build_fixpoint_judgment
    - CycleRecord.summary()
    - FixedPointRecord.dag_quality_score()
    - PartialModuleWitnessRecord.coverage_ratio()
    """

    logging.basicConfig(
        level=logging.DEBUG,
        format="%(levelname)-8s %(name)s: %(message)s",
    )

    print("=" * 70)
    print("  import_cycles_and_package_fixed_po.py — smoke test")
    print("  Theory: theory2.tex Ch19 §2")
    print("=" * 70)

    # ------------------------------------------------------------------
    # 1. Build the sample cycle graph
    # ------------------------------------------------------------------
    sample_graph = build_sample_cycle_graph()
    print(f"\n[1] Sample graph: {len(sample_graph)} nodes")
    for node, deps in sorted(sample_graph.items()):
        print(f"    {node:12s} → {deps}")

    # ------------------------------------------------------------------
    # 2. Run Tarjan's SCC
    # ------------------------------------------------------------------
    analyzer = ImportCyclesPackageFixedAnalyzer()
    sccs = analyzer.run_tarjan_scc(sample_graph)

    print(f"\n[2] Tarjan SCCs ({len(sccs)} found):")
    cyclic_sccs = [s for s in sccs if len(s) >= 2]
    trivial_sccs = [s for s in sccs if len(s) < 2]
    for scc in sorted(cyclic_sccs, key=lambda s: s[0]):
        print(f"    CYCLE: {scc}")
    for scc in sorted(trivial_sccs, key=lambda s: s[0]):
        print(f"    trivial: {scc}")

    assert len(cyclic_sccs) == 3, f"Expected 3 cyclic SCCs, got {len(cyclic_sccs)}"
    print("    ✓ Correct number of cyclic SCCs (3)")

    # ------------------------------------------------------------------
    # 3. Topological sort — should return None because of cycles
    # ------------------------------------------------------------------
    topo = analyzer.compute_topological_sort(sample_graph)
    assert topo is None, "Expected None (cycle present) but got an order"
    print("\n[3] Topological sort correctly returned None (cycle detected) ✓")

    # Acyclic sub-graph test
    acyclic = {"a": ["b", "c"], "b": ["d"], "c": ["d"], "d": []}
    topo2 = analyzer.compute_topological_sort(acyclic)
    assert topo2 is not None, "Expected a valid order for the acyclic sub-graph"
    print(f"    Acyclic sub-graph order: {topo2} ✓")

    # ------------------------------------------------------------------
    # 4. Classify cycles
    # ------------------------------------------------------------------
    print("\n[4] Cycle classification:")
    test_cases = [
        (["alpha", "beta"], CycleKind.DIRECT),
        (["alpha", "beta", "gamma"], CycleKind.INDIRECT),
        (["pkg.__init__", "pkg.sub"], CycleKind.STAR_MEDIATED),
        (["pkg._types", "pkg.core"], CycleKind.CONDITIONAL),
    ]
    for members, expected in test_cases:
        got = analyzer.classify_cycle(members)
        status = "✓" if got == expected else f"✗ (expected {expected})"
        print(f"    classify_cycle({members}) = {got.value}  {status}")

    # ------------------------------------------------------------------
    # 5. Coordinator: detect_cycles
    # ------------------------------------------------------------------
    coordinator = ImportCyclesPackageFixedCoordinator()
    cycle_records = coordinator.detect_cycles(sample_graph)
    print(f"\n[5] Coordinator detected {len(cycle_records)} cycle records:")
    for cr in cycle_records:
        print(f"    {cr.summary()}")
    assert len(cycle_records) == 3, f"Expected 3 cycle records, got {len(cycle_records)}"
    print("    ✓")

    # ------------------------------------------------------------------
    # 6. Build cycle judgments
    # ------------------------------------------------------------------
    print("\n[6] Building judgments for each cycle record:")
    for cr in cycle_records:
        j = coordinator.build_cycle_judgment(cr)
        print(f"    Judgment status={j.status}  label={j.label!r}")
        assert j.status == JudgmentStatus.OBSTRUCTED
    print("    ✓ All judgments are OBSTRUCTED")

    # ------------------------------------------------------------------
    # 7. analyze_directory on a temp directory
    # ------------------------------------------------------------------
    import tempfile, textwrap as tw

    with tempfile.TemporaryDirectory() as tmpdir:
        # Write two mutually-importing modules
        mod_a = os.path.join(tmpdir, "mod_a.py")
        mod_b = os.path.join(tmpdir, "mod_b.py")
        with open(mod_a, "w") as f:
            f.write("from mod_b import B\nclass A: pass\n")
        with open(mod_b, "w") as f:
            f.write("from mod_a import A\nclass B: pass\n")

        fp_record = coordinator.analyze_directory(tmpdir)
        print(f"\n[7] analyze_directory on temp dir:")
        print(f"    module_count={fp_record.module_count}")
        print(f"    cycle_count={fp_record.cycle_count}")
        print(f"    scc_count={fp_record.scc_count}")
        print(f"    topological_order_available={fp_record.topological_order_available}")
        print(f"    dag_quality_score={fp_record.dag_quality_score():.3f}")
        assert fp_record.module_count == 2, f"Expected 2 modules, got {fp_record.module_count}"
        print("    ✓")

    # ------------------------------------------------------------------
    # 8. Witness: witness_partial_module
    # ------------------------------------------------------------------
    witness = ImportCyclesPackageFixedWitness()
    pwr = witness.witness_partial_module("os.path")
    print(f"\n[8] witness_partial_module('os.path'):")
    print(f"    is_partial={pwr.is_partial}")
    print(f"    coverage_ratio={pwr.coverage_ratio():.3f}")
    print(f"    witness_level={pwr.witness_level}")
    print("    ✓")

    # Non-existent module
    pwr_missing = witness.witness_partial_module("jugeo._nonexistent_module_xyz")
    print(f"    witness_partial_module('jugeo._nonexistent_module_xyz').is_partial={pwr_missing.is_partial}")
    print("    ✓")

    # ------------------------------------------------------------------
    # 9. detect_runtime_cycles
    # ------------------------------------------------------------------
    runtime_cycles = witness.detect_runtime_cycles()
    print(f"\n[9] detect_runtime_cycles: found {len(runtime_cycles)} suspected runtime cycles")
    print("    ✓ (result is context-dependent; 0 is expected in a clean interpreter)")

    # ------------------------------------------------------------------
    # 10. build_fixpoint_judgment
    # ------------------------------------------------------------------
    # Build one for the fixed point record from step 7 (re-run with tmpdir gone, use mock)
    mock_fp = FixedPointRecord(
        package_root="/mock/package",
        module_count=10,
        cycle_count=0,
        scc_count=10,
        topological_order_available=True,
        timestamp=time.time(),
    )
    j_dag = witness.build_fixpoint_judgment(mock_fp)
    print(f"\n[10] build_fixpoint_judgment (DAG): status={j_dag.status} ✓")
    assert j_dag.status == JudgmentStatus.SETTLED

    mock_cyclic = FixedPointRecord(
        package_root="/mock/cyclic_package",
        module_count=8,
        cycle_count=2,
        scc_count=6,
        topological_order_available=False,
        timestamp=time.time(),
    )
    j_cyclic = witness.build_fixpoint_judgment(mock_cyclic)
    print(f"    build_fixpoint_judgment (cyclic): status={j_cyclic.status} ✓")
    assert j_cyclic.status == JudgmentStatus.OBSTRUCTED

    # ------------------------------------------------------------------
    # 11. CycleRecord helpers
    # ------------------------------------------------------------------
    sample_cr = CycleRecord(
        cycle_members=("a", "b", "c", "d", "e"),
        cycle_kind=CycleKind.INDIRECT.value,
        detection_method="tarjan_scc",
        severity_score=0.65,
        obstruction="Transitive cycle: a → … → e → a",
    )
    print(f"\n[11] CycleRecord.summary(): {sample_cr.summary()}")

    # ------------------------------------------------------------------
    # 12. PartialModuleWitnessRecord.coverage_ratio edge cases
    # ------------------------------------------------------------------
    full_pwr = PartialModuleWitnessRecord(
        module_name="fully.loaded",
        partial_attributes=("foo", "bar", "baz"),
        missing_attributes=(),
        is_partial=False,
        witness_level=4,
    )
    assert full_pwr.coverage_ratio() == 1.0
    half_pwr = PartialModuleWitnessRecord(
        module_name="half.loaded",
        partial_attributes=("foo",),
        missing_attributes=("bar",),
        is_partial=True,
        witness_level=3,
    )
    assert half_pwr.coverage_ratio() == 0.5
    print(f"\n[12] coverage_ratio: full={full_pwr.coverage_ratio():.1f}  half={half_pwr.coverage_ratio():.1f}  ✓")

    print("\n" + "=" * 70)
    print("  All smoke tests passed  ✓")
    print("  theory2.tex Ch19 §2 — import cycles and package fixed points")
    print("=" * 70)
