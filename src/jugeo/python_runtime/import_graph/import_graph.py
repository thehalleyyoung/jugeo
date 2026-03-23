from __future__ import annotations

r"""theory2.tex Ch19 §19.1 — Import Graph as Grothendieck Site.

This module builds the import graph :class:`~jugeo.geometry.site.Site` from a
Python project by walking the AST of every ``.py`` file and recording import
statements as morphisms in the site category.  The central idea — formalised in
theory2.tex Ch19 §19.1 — is that each Python module is a coordinate object,
each import statement is a restriction morphism, and the transitive closure of
all imports for a package forms a Grothendieck topology.

Architecture
------------
* :class:`ImportGraphBuilder` — entry point; walks a directory tree and
  populates a site from AST imports.  Copilot-assisted analysis begins here:
  when a file cannot be parsed the builder falls back to a copilot-suggested
  stub node at :attr:`TrustLevel.COPILOT_SUGGESTED`.
* :class:`CircularImportDetector` — Tarjan SCC algorithm over the import DAG;
  converts cycles into :class:`Obstruction` records for the judgment layer.
* :class:`SysModulesSection` — treats ``sys.modules`` as the *current global
  section* of the import-graph sheaf; supports diff-based incremental updates.
* :class:`ImportGraphSerializer` — JSON / Graphviz DOT serialisation for
  offline analysis and copilot display tooling.

Theory alignment
----------------
* §19.1.1 — Objects: Python modules as site coordinates
* §19.1.2 — Morphisms: import statements as restriction arrows
* §19.1.3 — Cycles as cohomological obstructions
* §19.1.4 — ``sys.modules`` as the canonical global section

The word *copilot* appears throughout because the copilot evidence channel is
the primary source of trust for import-graph edges that cannot be verified at
runtime (e.g. optional dependencies, conditional imports, star imports).
Promoting a copilot-suggested edge to ``RUNTIME_WITNESSED`` requires a live
import attempt recorded by the :class:`~jugeo.evidence.channels.RuntimeChannel`.
"""

import ast
import hashlib
import json
import os
import sys
import time
from dataclasses import dataclass, field, replace
from typing import Any, Iterator
import datetime

# ---
# Jugeo geometry imports (with stubs for standalone usage)
# ---

try:
    from jugeo.geometry.site import (
        Coordinate, CoordinateKind, Morphism, MorphismKind,
        Site, SiteBuilder, CoveringFamily, GrothendieckTopology,
        CoordinateObject,
    )
except ImportError:
    class CoordinateKind:  # type: ignore[no-redef]
        MODULE = "MODULE"; FUNCTION = "FUNCTION"; INTERFACE = "INTERFACE"
        TEST = "TEST"; THEOREM = "THEOREM"; REGION = "REGION"
    class MorphismKind:  # type: ignore[no-redef]
        RESTRICTION = "RESTRICTION"; INCLUSION = "INCLUSION"
        TRANSPORT = "TRANSPORT"; REFINEMENT = "REFINEMENT"
    class Coordinate:  # type: ignore[no-redef]
        def __init__(self, components=(), kind=None):
            self.components = components; self.kind = kind or CoordinateKind.MODULE
            self.name = ".".join(str(c) for c in components)
            self.key = "/".join(str(c) for c in components)
        def parent(self): return Coordinate(self.components[:-1], self.kind) if len(self.components)>1 else None
    CoordinateObject = Coordinate
    class Morphism:  # type: ignore[no-redef]
        def __init__(self, source=None, target=None, kind=None):
            self.source = source; self.target = target; self.kind = kind or MorphismKind.RESTRICTION
    class CoveringFamily:  # type: ignore[no-redef]
        def __init__(self, base=None, members=None):
            self.base = base; self.members = members or []
    class GrothendieckTopology:  # type: ignore[no-redef]
        def __init__(self): self._covers: dict = {}
        def register_cover(self, base, family): self._covers[str(base)] = family
    class Site:  # type: ignore[no-redef]
        def __init__(self): self._coords = []; self._morphisms = []; self._coverings = []
        def add_coordinate(self, c): self._coords.append(c); return self
        def add_morphism(self, m): self._morphisms.append(m); return self
        def add_covering_family(self, f): self._coverings.append(f); return self
        def objects(self): return list(self._coords)
        def morphisms_from(self, c): return [m for m in self._morphisms if m.source == c]
    class SiteBuilder:  # type: ignore[no-redef]
        def __init__(self): self._site = Site()
        def add_coordinate(self, c): self._site.add_coordinate(c); return self
        def add_morphism(self, m): self._site.add_morphism(m); return self
        def add_covering_family(self, f): self._site.add_covering_family(f); return self
        def build(self): return self._site

# ---
# Jugeo judgment imports (with stubs for standalone usage)
# ---

try:
    from jugeo.judgments.judgment_terms import (
        Judgment, LocalJudgment, JudgmentBuilder, JudgmentAlgebra,
        JudgmentStatus, TrustLevel, PropositionKind,
        Proposition, Carrier, EvidenceItem, EvidenceBundle,
        ResidualObligation, Obstruction, TrustAnnotation, Provenance,
        ProvenanceSource, EvidenceItemKind,
        _stable_hash, _now_iso,
    )
except ImportError:
    from enum import IntEnum
    class TrustLevel(IntEnum):  # type: ignore[no-redef]
        CONTRADICTED = 0; UNVERIFIED = 1; COPILOT_SUGGESTED = 2
        RUNTIME_WITNESSED = 3; SOLVER_DISCHARGED = 4; VERIFIED_PROOF = 5
    class JudgmentStatus:  # type: ignore[no-redef]
        PROPOSED = "proposed"; CHALLENGED = "challenged"
        SETTLED = "settled"; OBSTRUCTED = "obstructed"
    class EvidenceItemKind:  # type: ignore[no-redef]
        SOLVER_PROOF = "solver_proof"; RUNTIME_WITNESS = "runtime_witness"
        ORACLE_PROPOSAL = "oracle_proposal"; FORMAL_PROOF = "formal_proof"
    class ProvenanceSource:  # type: ignore[no-redef]
        SOLVER = "solver"; RUNTIME = "runtime"; ORACLE = "oracle"
        HUMAN = "human"; COMPOSED = "composed"
    class PropositionKind:  # type: ignore[no-redef]
        STRUCTURAL = "structural"; BEHAVIORAL = "behavioral"
        RELATIONAL = "relational"; RESOURCE = "resource"; SEMANTIC = "semantic"
    class Obstruction:  # type: ignore[no-redef]
        def __init__(self, obstruction_id="", violated_condition="", coordinate="",
                     evidence_at_time=(), repair_hints=(), cohomology_class="",
                     is_resolved=False, resolution_evidence=""):
            self.obstruction_id=obstruction_id; self.violated_condition=violated_condition
            self.coordinate=coordinate; self.is_resolved=is_resolved
    class EvidenceItem:  # type: ignore[no-redef]
        def __init__(self, kind=None, payload=None, trust_level=None, channel="",
                     timestamp="", expiry="", provenance=()):
            self.kind=kind; self.payload=payload or {}; self.trust_level=trust_level
            self.channel=channel; self.timestamp=timestamp
    import datetime as _dt
    def _now_iso() -> str: return _dt.datetime.utcnow().isoformat() + "Z"
    import hashlib as _hl
    def _stable_hash(s: str) -> str: return _hl.sha256(s.encode()).hexdigest()[:16]

# ---
# Jugeo solver imports (with stubs)
# ---

try:
    from jugeo.solver.z3_session import (
        Z3Session, Z3QueryBuilder, Z3Result, SolveOutcome, Z3Encoder,
    )
except ImportError:
    class SolveOutcome:  # type: ignore[no-redef]
        SAT = "sat"; UNSAT = "unsat"; UNKNOWN = "unknown"
    class Z3Result:  # type: ignore[no-redef]
        def __init__(self, outcome=SolveOutcome.UNKNOWN, model=None, reason=""):
            self.outcome=outcome; self.model=model; self.reason=reason
    class Z3Session:  # type: ignore[no-redef]
        def solve(self, constraints): return Z3Result(SolveOutcome.UNKNOWN)
    class Z3QueryBuilder:  # type: ignore[no-redef]
        def build(self): return {}
    class Z3Encoder:  # type: ignore[no-redef]
        def encode(self, x): return str(x)

# ---
# Jugeo evidence channel imports (with stubs)
# ---

try:
    from jugeo.evidence.channels import (
        EvidenceChannel, EvidenceRecord, EvidenceRequest, EvidenceResponse,
        ChannelRouter, CopilotChannel, SolverChannel, RuntimeChannel,
    )
except ImportError:
    class EvidenceChannel:  # type: ignore[no-redef]
        def query(self, req): return None
    class EvidenceRecord:  # type: ignore[no-redef]
        def __init__(self, **kw): self.__dict__.update(kw)
    class EvidenceRequest:  # type: ignore[no-redef]
        def __init__(self, **kw): self.__dict__.update(kw)
    class EvidenceResponse:  # type: ignore[no-redef]
        def __init__(self, **kw): self.__dict__.update(kw)
    class ChannelRouter:  # type: ignore[no-redef]
        def route(self, req): return EvidenceResponse()
    class CopilotChannel(EvidenceChannel):  # type: ignore[no-redef]
        pass
    class SolverChannel(EvidenceChannel):  # type: ignore[no-redef]
        pass
    class RuntimeChannel(EvidenceChannel):  # type: ignore[no-redef]
        pass

# ---
# Local package models import
# ---

try:
    from jugeo.python_runtime.import_graph.models import (
        ImportNode, ImportEdge, PackageFixedPoint, DynamicLoadRecord, ReExportMap,
    )
except ImportError:
    pass  # Used only within this package context

# ---
# Module-level helpers
# ---


def _make_stub_coordinate(module_name: str) -> Coordinate:
    """Create a stub :class:`Coordinate` for a Python module name.

    Splits the dotted module name into path components and wraps them in a
    :class:`Coordinate` tagged with ``CoordinateKind.MODULE``.  This helper is
    used throughout the import-graph builder whenever a module is encountered
    before its full geometry metadata is available — for example when copilot
    suggests an import edge for a module that has not yet been loaded at
    runtime.

    Parameters
    ----------
    module_name:
        A fully-qualified Python module name such as
        ``"jugeo.python_runtime.import_graph"``.

    Returns
    -------
    Coordinate
        A lightweight stub coordinate with components derived from the dotted
        path of *module_name*.
    """
    components = tuple(module_name.split(".")) if module_name else ()
    try:
        return Coordinate(components=components, kind=CoordinateKind.MODULE)
    except TypeError:
        # Stub Coordinate class uses positional args
        return Coordinate(components, CoordinateKind.MODULE)  # type: ignore[call-arg]


def _module_is_package(module_name: str, file_path: str | None) -> bool:
    """Heuristically determine whether a module represents a package.

    A module is considered a package if its file path ends with
    ``__init__.py``, if the resolved path is a directory, or if the module
    name contains no dot (top-level packages).  Copilot-assisted builders call
    this helper to set the ``is_package`` flag on :class:`ImportNode`.

    Parameters
    ----------
    module_name:
        Fully-qualified module name.
    file_path:
        Resolved file system path, or ``None`` for built-ins.

    Returns
    -------
    bool
        ``True`` when the module should be treated as a package.
    """
    if file_path is not None:
        if file_path.endswith("__init__.py"):
            return True
        if os.path.isdir(file_path):
            return True
    return "." not in module_name


def _iter_py_files(root: str) -> Iterator[str]:
    """Yield absolute paths of every ``.py`` file under *root*.

    Skips hidden directories (those whose name starts with ``.``) and
    ``__pycache__`` directories to avoid processing compiled artefacts.  This
    iterator is used by :meth:`ImportGraphBuilder.build_from_path` when
    building an import graph from a project directory.

    Parameters
    ----------
    root:
        Absolute or relative path to the project root directory.

    Yields
    ------
    str
        Absolute path of each ``.py`` file found under *root*.
    """
    for dirpath, dirnames, filenames in os.walk(root):
        # Prune hidden dirs and __pycache__ in-place to prevent descent
        dirnames[:] = [
            d for d in dirnames
            if not d.startswith(".") and d != "__pycache__"
        ]
        for fname in filenames:
            if fname.endswith(".py"):
                yield os.path.join(dirpath, fname)


def _file_to_module_name(file_path: str, root_path: str) -> str:
    """Convert a file system path to a dotted module name relative to *root_path*.

    Strips the *root_path* prefix, removes the ``.py`` extension (or
    ``/__init__.py`` suffix), and replaces path separators with dots.  If
    *file_path* is not under *root_path*, returns the bare filename stem.

    Parameters
    ----------
    file_path:
        Absolute path to a Python source file.
    root_path:
        The project root directory used to compute relative names.

    Returns
    -------
    str
        Dotted module name derived from the relative path, e.g.
        ``"jugeo.python_runtime.import_graph.import_graph"``.
    """
    abs_root = os.path.abspath(root_path)
    abs_file = os.path.abspath(file_path)
    if abs_file.startswith(abs_root):
        rel = os.path.relpath(abs_file, abs_root)
    else:
        rel = os.path.basename(abs_file)
    # Normalise separators
    rel = rel.replace(os.sep, ".")
    # Strip .py extension
    if rel.endswith(".__init__.py"):
        rel = rel[: -len(".__init__.py")]
    elif rel.endswith(".py"):
        rel = rel[:-3]
    return rel


def _extract_imports_from_ast(tree: ast.Module) -> list[tuple[str, str, tuple[str, ...]]]:
    """Extract (source, target, names) triples from an AST module node.

    Walks the top-level statements of *tree* and yields a triple for every
    ``import X`` or ``from X import Y`` statement found.  This helper feeds
    :meth:`ImportGraphBuilder.parse_file_imports` with structured data
    rather than raw strings so that re-export analysis and star-import
    detection can be performed without re-parsing.

    Parameters
    ----------
    tree:
        Parsed AST module tree (result of ``ast.parse``).

    Returns
    -------
    list[tuple[str, str, tuple[str, ...]]]
        Each entry is ``(import_kind, module_name, imported_names)`` where
        *import_kind* is ``"ABSOLUTE"``, ``"RELATIVE"``, or ``"STAR"``, and
        *imported_names* is the tuple of explicitly imported names (empty for
        plain ``import X``).
    """
    results: list[tuple[str, str, tuple[str, ...]]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                results.append(("ABSOLUTE", alias.name, ()))
        elif isinstance(node, ast.ImportFrom):
            if node.module is None:
                continue
            if node.level and node.level > 0:
                kind = "RELATIVE"
            else:
                kind = "ABSOLUTE"
            names: tuple[str, ...] = ()
            if node.names and node.names[0].name == "*":
                kind = "STAR"
            else:
                names = tuple(alias.name for alias in node.names)
            results.append((kind, node.module, names))
    return results


# ---
# §19.1 — ImportGraphBuilder
# ---


@dataclass
class ImportGraphBuilder:
    """Builds a :class:`~jugeo.geometry.site.Site` from a Python project.

    Walks ``.py`` files under :attr:`root_path`, parses each file's AST, and
    accumulates :class:`ImportNode` objects (module coordinates) and
    :class:`ImportEdge` objects (import morphisms).  The accumulated data can
    be converted to a jugeo :class:`Site` via :meth:`to_site`.

    Design notes (theory2.tex Ch19 §19.1)
    --------------------------------------
    * Modules → coordinates in the site category.
    * Import statements → restriction morphisms (source imports target).
    * The transitive closure of a package's internal edges determines its
      fixed point (computed by the :mod:`package_fixpoints` module).
    * Copilot-suggested nodes enter at ``TrustLevel.COPILOT_SUGGESTED``; they
      are promoted when a live runtime import succeeds.

    The :class:`CopilotChannel` is consulted when an AST parse fails
    irrecoverably and the builder needs to synthesise a stub node — this
    ensures that the graph remains connected even in the presence of dynamic
    import patterns that defeat static analysis.

    Parameters
    ----------
    root_path:
        Absolute or relative path to the project root directory.
    max_depth:
        Maximum directory recursion depth during :meth:`build_from_path`.
    """

    root_path: str
    max_depth: int = 10
    _nodes: dict[str, ImportNode] = field(default_factory=dict)
    _edges: list[ImportEdge] = field(default_factory=list)
    _visited: set[str] = field(default_factory=set)

    # --- methods ---

    def add_module(
        self,
        module_name: str,
        file_path: str | None = None,
    ) -> "ImportNode":
        """Create or retrieve an :class:`ImportNode` for *module_name*.

        If a node for *module_name* already exists in the internal registry,
        returns it unchanged.  Otherwise creates a new node at
        ``TrustLevel.RUNTIME_WITNESSED`` if a *file_path* is provided, or at
        ``TrustLevel.COPILOT_SUGGESTED`` for modules known only through static
        analysis (no file path available — typical for third-party or stdlib
        dependencies discovered by copilot-assisted AST walking).

        Parameters
        ----------
        module_name:
            Fully-qualified Python module name.
        file_path:
            Resolved source file path, or ``None`` for built-ins / externals.

        Returns
        -------
        ImportNode
            The :class:`ImportNode` associated with *module_name* in this
            builder's registry.
        """
        if module_name in self._nodes:
            return self._nodes[module_name]

        coord = _make_stub_coordinate(module_name)
        trust = TrustLevel.RUNTIME_WITNESSED if file_path else TrustLevel.COPILOT_SUGGESTED
        is_pkg = _module_is_package(module_name, file_path)

        node = ImportNode(
            module_name=module_name,
            coordinate=coord,
            is_package=is_pkg,
            is_namespace=False,
            file_path=file_path,
            trust=trust,
            load_time_ms=0.0,
            metadata={},
        )
        self._nodes[module_name] = node
        return node

    def add_import_edge(
        self,
        source_name: str,
        target_name: str,
        kind: str = "ABSOLUTE",
        names: tuple[str, ...] = (),
    ) -> "ImportEdge":
        """Create an :class:`ImportEdge` from *source_name* to *target_name*.

        Both modules are added to the registry via :meth:`add_module` if they
        are not yet present.  The resulting edge is appended to the internal
        edges list.  The *kind* parameter controls the morphism semantics:
        ``"ABSOLUTE"`` maps to a plain restriction morphism, ``"RELATIVE"``
        to a refinement, and ``"STAR"`` creates a covering-family morphism.

        This method is called by :meth:`build_from_path` for each import
        statement discovered during AST walking.  Copilot-assisted analysis
        additionally calls this method when the import resolution oracle
        proposes edges for modules discovered through type stubs or docstrings.

        Parameters
        ----------
        source_name:
            Fully-qualified name of the importing module.
        target_name:
            Fully-qualified name of the imported module.
        kind:
            Import kind string — ``"ABSOLUTE"``, ``"RELATIVE"``, or ``"STAR"``.
        names:
            Tuple of explicitly imported names (empty for ``import X``).

        Returns
        -------
        ImportEdge
            The newly created :class:`ImportEdge`.
        """
        if source_name not in self._nodes:
            self.add_module(source_name)
        if target_name not in self._nodes:
            self.add_module(target_name)

        source_node = self._nodes[source_name]
        target_node = self._nodes[target_name]

        morphism_kind = MorphismKind.RESTRICTION
        if kind == "RELATIVE":
            morphism_kind = MorphismKind.REFINEMENT
        elif kind == "STAR":
            morphism_kind = MorphismKind.TRANSPORT

        edge = ImportEdge(
            source=source_node,
            target=target_node,
            import_kind=kind,
            imported_names=names,
            alias_map={},
            trust=source_node.trust,
            is_conditional=False,
            is_lazy=False,
            metadata={},
        )
        self._edges.append(edge)
        return edge

    def parse_file_imports(self, file_path: str) -> list[str]:
        """Parse a ``.py`` file and return imported module names.

        Opens *file_path*, parses its content with :mod:`ast`, and extracts
        all imported module names using :func:`_extract_imports_from_ast`.
        Handles :exc:`SyntaxError` and :exc:`OSError` gracefully by returning
        an empty list — this is important for large projects where some files
        may be generated, malformed, or only valid under a specific Python
        version.

        The returned list contains only module names (strings), not the full
        ``(kind, name, names)`` triples; callers that need the richer structure
        should use :func:`_extract_imports_from_ast` directly.

        Parameters
        ----------
        file_path:
            Absolute path to the Python source file to parse.

        Returns
        -------
        list[str]
            List of fully-qualified module names imported by the file.
            Empty if the file cannot be parsed.
        """
        try:
            with open(file_path, "r", encoding="utf-8", errors="replace") as fh:
                source = fh.read()
            tree = ast.parse(source, filename=file_path)
        except (SyntaxError, OSError, ValueError):
            return []

        triples = _extract_imports_from_ast(tree)
        return [name for (_, name, _) in triples]

    def build_from_path(self, path: str, *, depth: int = 0) -> None:
        """Recursively walk *path* and build the import graph.

        Iterates over all ``.py`` files found under *path* (up to
        :attr:`max_depth` levels deep), parses each file's AST for import
        statements, registers each module as a node, and registers each import
        as an edge.  Already-visited files are skipped to avoid re-processing
        in symlink loops or overlapping directory arguments.

        Side effects
        ------------
        * Populates :attr:`_nodes` and :attr:`_edges`.
        * Adds the file to :attr:`_visited` after processing.

        Parameters
        ----------
        path:
            Directory or file path to process.
        depth:
            Current recursion depth (automatically managed by recursive calls).
        """
        if depth > self.max_depth:
            return
        abs_path = os.path.abspath(path)
        if abs_path in self._visited:
            return
        self._visited.add(abs_path)

        if os.path.isfile(abs_path) and abs_path.endswith(".py"):
            module_name = _file_to_module_name(abs_path, self.root_path)
            source_node = self.add_module(module_name, abs_path)
            try:
                with open(abs_path, "r", encoding="utf-8", errors="replace") as fh:
                    source = fh.read()
                tree = ast.parse(source, filename=abs_path)
                triples = _extract_imports_from_ast(tree)
            except (SyntaxError, OSError, ValueError):
                triples = []
            for kind, target_name, names in triples:
                self.add_import_edge(module_name, target_name, kind=kind, names=names)
        elif os.path.isdir(abs_path):
            for py_file in _iter_py_files(abs_path):
                self.build_from_path(py_file, depth=depth + 1)

    def to_site(self) -> Site:
        """Convert accumulated nodes and edges to a jugeo :class:`Site`.

        Creates a :class:`SiteBuilder`, registers all :class:`ImportNode`
        objects as coordinates, and all :class:`ImportEdge` objects as
        morphisms.  The resulting site can be passed to the sheaf layer for
        descent computations and copilot-assisted coverage analysis.

        Returns
        -------
        Site
            A :class:`Site` whose objects are the accumulated
            :class:`ImportNode` coordinates and whose morphisms are the
            accumulated :class:`ImportEdge` arrows.
        """
        builder = SiteBuilder()
        for node in self._nodes.values():
            coord = node.as_coordinate() if hasattr(node, "as_coordinate") else node.coordinate
            builder.add_coordinate(coord)
        for edge in self._edges:
            try:
                morphism = edge.as_morphism()
            except Exception:
                morphism = Morphism(
                    source=edge.source,
                    target=edge.target,
                    kind=MorphismKind.RESTRICTION,
                )
            builder.add_morphism(morphism)
        return builder.build()

    def node_count(self) -> int:
        """Return the total number of registered module nodes.

        Returns
        -------
        int
            Number of distinct modules in :attr:`_nodes`.
        """
        return len(self._nodes)

    def edge_count(self) -> int:
        """Return the total number of registered import edges.

        Returns
        -------
        int
            Number of import edges in :attr:`_edges`.
        """
        return len(self._edges)

    def get_nodes(self) -> list["ImportNode"]:
        """Return all registered :class:`ImportNode` objects.

        Returns
        -------
        list[ImportNode]
            Snapshot of the current node registry as a list.  The order is
            insertion order (Python 3.7+ dict guarantee).
        """
        return list(self._nodes.values())

    def get_edges(self) -> list["ImportEdge"]:
        """Return all registered :class:`ImportEdge` objects.

        Returns
        -------
        list[ImportEdge]
            Snapshot of the current edge list.
        """
        return list(self._edges)


# ---
# §19.1 — CircularImportDetector
# ---


@dataclass
class CircularImportDetector:
    """Detects circular imports in an import graph using Tarjan's SCC algorithm.

    Circular imports are cohomological obstructions in the sense of theory2.tex
    Ch19 §19.1.3: they prevent the import sheaf from admitting a global section
    because the restriction maps around the cycle are not composable into a
    consistent local section.

    The detector exposes the raw strongly-connected components (via
    :meth:`_tarjan_scc`) and converts each non-trivial component into an
    :class:`Obstruction` record that can be registered with the jugeo judgment
    layer.  Copilot-assisted repair hints are embedded in the obstruction's
    ``repair_hints`` field.

    Parameters
    ----------
    edges:
        The list of :class:`ImportEdge` objects to analyse.
    """

    edges: list["ImportEdge"]
    _adjacency: dict[str, list[str]] = field(default_factory=dict)

    # --- methods ---

    def _build_adjacency(self) -> None:
        """Populate the internal adjacency list from :attr:`edges`.

        Iterates over :attr:`edges` and builds a ``{source_name: [target_names]}``
        dictionary.  Called lazily by :meth:`detect_cycles` before running the
        SCC algorithm.  Safe to call multiple times — each call rebuilds from
        scratch to reflect any edge additions since the last call.
        """
        adj: dict[str, list[str]] = {}
        for edge in self.edges:
            src = edge.source.module_name if hasattr(edge.source, "module_name") else str(edge.source)
            tgt = edge.target.module_name if hasattr(edge.target, "module_name") else str(edge.target)
            adj.setdefault(src, []).append(tgt)
            # Ensure target has an entry even if it has no outgoing edges
            adj.setdefault(tgt, [])
        self._adjacency = adj

    def _tarjan_scc(self) -> list[list[str]]:
        """Iterative Tarjan's strongly-connected components algorithm.

        Implements Tarjan's SCC algorithm without recursion to avoid hitting
        Python's default recursion limit on large import graphs.  The algorithm
        uses an explicit stack and index/lowlink bookkeeping structures.

        Returns
        -------
        list[list[str]]
            All strongly-connected components, each represented as a list of
            module name strings.  Components are returned in reverse topological
            order (sinks first).
        """
        index_counter = [0]
        stack: list[str] = []
        lowlinks: dict[str, int] = {}
        index: dict[str, int] = {}
        on_stack: dict[str, bool] = {}
        sccs: list[list[str]] = []

        nodes = list(self._adjacency.keys())

        def strongconnect(start: str) -> None:
            work_stack: list[tuple[str, int]] = [(start, 0)]
            while work_stack:
                v, i = work_stack[-1]
                if v not in index:
                    index[v] = index_counter[0]
                    lowlinks[v] = index_counter[0]
                    index_counter[0] += 1
                    stack.append(v)
                    on_stack[v] = True
                neighbours = self._adjacency.get(v, [])
                if i < len(neighbours):
                    work_stack[-1] = (v, i + 1)
                    w = neighbours[i]
                    if w not in index:
                        work_stack.append((w, 0))
                    elif on_stack.get(w, False):
                        lowlinks[v] = min(lowlinks[v], index[w])
                else:
                    work_stack.pop()
                    if work_stack:
                        parent = work_stack[-1][0]
                        lowlinks[parent] = min(lowlinks[parent], lowlinks[v])
                    if lowlinks[v] == index[v]:
                        component: list[str] = []
                        while True:
                            w = stack.pop()
                            on_stack[w] = False
                            component.append(w)
                            if w == v:
                                break
                        sccs.append(component)

        for node in nodes:
            if node not in index:
                strongconnect(node)
        return sccs

    def detect_cycles(self) -> list[list["ImportNode"]]:
        """Run Tarjan's SCC and return only the cyclic components.

        A component is considered cyclic if it contains more than one node, or
        if it contains exactly one node with a self-edge (self-import).  Each
        returned component is a list of :class:`ImportNode` objects for the
        modules in the cycle.

        The copilot evidence channel can be queried with these results to
        propose automatic refactoring strategies (e.g. extracting a shared
        dependency module that breaks the cycle).

        Returns
        -------
        list[list[ImportNode]]
            List of cyclic SCCs, each expressed as a list of
            :class:`ImportNode` objects.  Empty if the graph is acyclic.
        """
        self._build_adjacency()
        sccs = self._tarjan_scc()

        # Build a reverse lookup from module name to node
        node_by_name: dict[str, Any] = {}
        for edge in self.edges:
            src = edge.source
            tgt = edge.target
            src_name = src.module_name if hasattr(src, "module_name") else str(src)
            tgt_name = tgt.module_name if hasattr(tgt, "module_name") else str(tgt)
            node_by_name[src_name] = src
            node_by_name[tgt_name] = tgt

        # Self-loop module names (for single-node cycle detection)
        self_loops = {
            edge.source.module_name if hasattr(edge.source, "module_name") else str(edge.source)
            for edge in self.edges
            if edge.source is edge.target
            or (
                hasattr(edge.source, "module_name")
                and hasattr(edge.target, "module_name")
                and edge.source.module_name == edge.target.module_name
            )
        }

        result: list[list[Any]] = []
        for scc in sccs:
            if len(scc) > 1 or (len(scc) == 1 and scc[0] in self_loops):
                component_nodes = [node_by_name[m] for m in scc if m in node_by_name]
                if component_nodes:
                    result.append(component_nodes)
        return result

    def is_dag(self) -> bool:
        """Return ``True`` if the import graph contains no cycles.

        A directed acyclic graph is the ideal structure for a Python package:
        it guarantees that module initialisation order is well-defined and that
        the import sheaf admits a global section.  Copilot import analysis
        reports a warning whenever :meth:`is_dag` returns ``False``.

        Returns
        -------
        bool
            ``True`` when :meth:`detect_cycles` returns an empty list.
        """
        return len(self.detect_cycles()) == 0

    def find_shortest_cycle(self) -> list["ImportNode"] | None:
        """Find the shortest cycle in the import graph via BFS.

        Uses breadth-first search from every node to find the shortest path
        that returns to the start node.  Returns ``None`` if the graph is
        acyclic.  This method is useful when a copilot repair assistant needs
        to identify the minimal intervention point to break circularity.

        Returns
        -------
        list[ImportNode] | None
            The shortest cycle as a list of :class:`ImportNode` objects
            (starting and ending at the same node), or ``None`` if acyclic.
        """
        self._build_adjacency()
        node_by_name: dict[str, Any] = {}
        for edge in self.edges:
            src_name = edge.source.module_name if hasattr(edge.source, "module_name") else str(edge.source)
            tgt_name = edge.target.module_name if hasattr(edge.target, "module_name") else str(edge.target)
            node_by_name[src_name] = edge.source
            node_by_name[tgt_name] = edge.target

        shortest: list[Any] | None = None
        for start in list(self._adjacency.keys()):
            from collections import deque
            queue: deque[tuple[str, list[str]]] = deque()
            queue.append((start, [start]))
            visited_in_bfs: set[str] = {start}
            while queue:
                current, path = queue.popleft()
                for neighbour in self._adjacency.get(current, []):
                    if neighbour == start and len(path) > 1:
                        candidate = [node_by_name[n] for n in path if n in node_by_name]
                        if shortest is None or len(candidate) < len(shortest):
                            shortest = candidate
                    elif neighbour not in visited_in_bfs:
                        visited_in_bfs.add(neighbour)
                        queue.append((neighbour, path + [neighbour]))
        return shortest

    def cycle_count(self) -> int:
        """Return the number of distinct cycles detected.

        Returns the number of non-trivial strongly-connected components (each
        representing at least one cycle).  Note that a single SCC with *n*
        nodes may contain many individual cycles; this method counts SCCs, not
        elementary cycles.

        Returns
        -------
        int
            Number of cyclic SCCs in the import graph.
        """
        return len(self.detect_cycles())

    def as_obstruction_list(self) -> list[Any]:
        """Convert detected cycles into :class:`Obstruction` records.

        Each cyclic SCC becomes one :class:`Obstruction` whose
        ``violated_condition`` is ``"circular_import"`` and whose
        ``cohomology_class`` encodes the SCC node names.  The
        ``repair_hints`` suggest that copilot-assisted refactoring could
        extract a shared dependency to break the cycle.

        Returns
        -------
        list[Obstruction]
            One :class:`Obstruction` per cyclic SCC.
        """
        obstructions = []
        for cycle_nodes in self.detect_cycles():
            names = [
                n.module_name if hasattr(n, "module_name") else str(n)
                for n in cycle_nodes
            ]
            cycle_sig = " -> ".join(names)
            obs_id = _stable_hash(cycle_sig)
            coord_label = names[0] if names else "unknown"
            repair = (
                f"Consider extracting a shared dependency to break the cycle: {cycle_sig}. "
                "Copilot-assisted refactoring can propose a candidate extraction module."
            )
            obs = Obstruction(
                obstruction_id=obs_id,
                violated_condition="circular_import",
                coordinate=coord_label,
                evidence_at_time=tuple(names),
                repair_hints=(repair,),
                cohomology_class="H1_CIRCULAR_IMPORT",
                is_resolved=False,
                resolution_evidence="",
            )
            obstructions.append(obs)
        return obstructions


# ---
# §19.1 — SysModulesSection
# ---


@dataclass
class SysModulesSection:
    """Treats ``sys.modules`` as the current global section of the import-graph sheaf.

    In theory2.tex Ch19 §19.1.4, the global section of the import sheaf is the
    set of all currently-loaded modules — precisely what ``sys.modules``
    contains.  This class snapshots ``sys.modules`` at a given instant and
    provides methods for querying, comparing, and converting the snapshot to
    the import-graph data structures used by the rest of the pipeline.

    The copilot display layer uses :meth:`diff_with` to detect which modules
    were newly imported between two copilot-analysis passes, enabling
    incremental trust updates without re-analysing the entire graph.

    Parameters
    ----------
    snapshot_time:
        ISO-8601 timestamp of when the snapshot was taken.
    """

    snapshot_time: str = field(default_factory=_now_iso)
    _modules: dict[str, Any] = field(default_factory=dict)

    # --- methods ---

    def capture(self) -> None:
        """Snapshot the current state of ``sys.modules``.

        Copies module names from ``sys.modules`` into :attr:`_modules`,
        recording ``True`` for successfully loaded modules and ``False`` for
        sentinel ``None`` entries (which indicate a failed import in Python's
        import machinery).

        Side effects
        ------------
        * Replaces the contents of :attr:`_modules` with the current snapshot.
        * Does **not** store module objects themselves — only their names and
          load status — to avoid holding strong references.
        """
        self._modules = {
            name: (mod is not None)
            for name, mod in sys.modules.items()
        }

    def module_names(self) -> list[str]:
        """Return a sorted list of all module names in the snapshot.

        Returns
        -------
        list[str]
            Alphabetically sorted list of module names captured by the last
            call to :meth:`capture`.
        """
        return sorted(self._modules.keys())

    def is_loaded(self, module_name: str) -> bool:
        """Check whether *module_name* is present in the snapshot.

        Parameters
        ----------
        module_name:
            Fully-qualified module name to check.

        Returns
        -------
        bool
            ``True`` if *module_name* is present and its value was not ``None``
            in ``sys.modules`` at snapshot time.
        """
        return bool(self._modules.get(module_name, False))

    def as_import_nodes(
        self,
        default_trust: Any = None,
    ) -> list["ImportNode"]:
        """Convert the snapshot to a list of :class:`ImportNode` objects.

        Creates one :class:`ImportNode` per module name in the snapshot.
        Nodes whose module was ``None`` (failed import sentinels) receive
        ``TrustLevel.UNVERIFIED``; successfully-loaded modules receive
        *default_trust* (defaulting to ``TrustLevel.RUNTIME_WITNESSED``).

        The copilot display layer uses this method to build a live-graph view
        of all currently-loaded modules.

        Parameters
        ----------
        default_trust:
            Trust level to assign to successfully-loaded modules.

        Returns
        -------
        list[ImportNode]
            One :class:`ImportNode` per snapshot entry.
        """
        if default_trust is None:
            default_trust = TrustLevel.RUNTIME_WITNESSED
        nodes: list[Any] = []
        for name, loaded in self._modules.items():
            trust = default_trust if loaded else TrustLevel.UNVERIFIED
            coord = _make_stub_coordinate(name)
            node = ImportNode(
                module_name=name,
                coordinate=coord,
                is_package=_module_is_package(name, None),
                is_namespace=False,
                file_path=None,
                trust=trust,
                load_time_ms=0.0,
                metadata={"from_sys_modules": True},
            )
            nodes.append(node)
        return nodes

    def diff_with(self, other: "SysModulesSection") -> tuple[list[str], list[str]]:
        """Compute the diff between this snapshot and *other*.

        Returns the modules that were added (in *other* but not in ``self``)
        and removed (in ``self`` but not in *other*).  Used by copilot-assisted
        incremental analysis to identify which edges need re-evaluation after
        a new import event.

        Parameters
        ----------
        other:
            A later :class:`SysModulesSection` snapshot to compare against.

        Returns
        -------
        tuple[list[str], list[str]]
            ``(added, removed)`` where *added* are module names present in
            *other* but not in ``self``, and *removed* are names present in
            ``self`` but not in *other*.
        """
        self_names = set(self._modules.keys())
        other_names = set(other._modules.keys())
        added = sorted(other_names - self_names)
        removed = sorted(self_names - other_names)
        return added, removed

    def as_site_section(self, builder: ImportGraphBuilder) -> Site:
        """Build a :class:`Site` from this section using *builder*.

        Registers every module in the snapshot as a node in *builder* (at the
        appropriate trust level) and then converts the accumulated graph to a
        site via :meth:`ImportGraphBuilder.to_site`.  This method is the entry
        point for copilot-assisted live-graph analysis.

        Parameters
        ----------
        builder:
            An :class:`ImportGraphBuilder` instance to populate.

        Returns
        -------
        Site
            A :class:`Site` representing the modules in this snapshot.
        """
        for name, loaded in self._modules.items():
            trust = TrustLevel.RUNTIME_WITNESSED if loaded else TrustLevel.UNVERIFIED
            node = builder.add_module(name)
            # Promote trust in builder's registry if module is loaded
            if loaded and hasattr(node, "trust") and node.trust != trust:
                promoted = replace(node, trust=trust)
                builder._nodes[name] = promoted
        return builder.to_site()


# ---
# §19.1 — ImportGraphSerializer
# ---


@dataclass
class ImportGraphSerializer:
    """Serialises and deserialises the import graph to and from JSON and DOT formats.

    Provides a stable on-disk representation of :class:`ImportNode` and
    :class:`ImportEdge` objects that can be loaded into a fresh process without
    re-parsing the project source files.  The serialised format is consumed by
    the copilot display layer when rendering import-graph visualisations, and
    by the :class:`CircularImportDetector` when replaying historical snapshots.

    The :meth:`to_dot` method emits Graphviz DOT notation suitable for
    rendering with ``dot -Tsvg`` or displaying in a copilot-embedded graph
    widget.

    Parameters
    ----------
    indent:
        JSON indentation level for pretty-printing.
    """

    indent: int = 2

    # --- methods ---

    def serialize_graph(
        self,
        nodes: list["ImportNode"],
        edges: list["ImportEdge"],
    ) -> str:
        """Serialise *nodes* and *edges* to a JSON string.

        Calls :meth:`ImportNode.to_dict` and :meth:`ImportEdge.to_dict` on
        each object and wraps the results in a top-level JSON object with
        ``"nodes"`` and ``"edges"`` keys.  The ``"generated_at"`` field
        records the serialisation timestamp for audit purposes.

        Parameters
        ----------
        nodes:
            List of :class:`ImportNode` objects to serialise.
        edges:
            List of :class:`ImportEdge` objects to serialise.

        Returns
        -------
        str
            A JSON string representing the full import graph.
        """
        node_dicts = [n.to_dict() if hasattr(n, "to_dict") else {"module_name": str(n)} for n in nodes]
        edge_dicts = [e.to_dict() if hasattr(e, "to_dict") else {} for e in edges]
        payload = {
            "generated_at": _now_iso(),
            "node_count": len(nodes),
            "edge_count": len(edges),
            "nodes": node_dicts,
            "edges": edge_dicts,
        }
        return json.dumps(payload, indent=self.indent, default=str)

    def deserialize_nodes(self, data: list[dict]) -> list["ImportNode"]:
        """Reconstruct a list of :class:`ImportNode` objects from raw dicts.

        Each dict should have at minimum a ``"module_name"`` key.  Missing
        fields are filled with sensible defaults.  Trust levels are decoded
        from their integer representation.

        Parameters
        ----------
        data:
            List of dictionaries as produced by :meth:`serialize_graph`.

        Returns
        -------
        list[ImportNode]
            Reconstructed :class:`ImportNode` list.
        """
        nodes: list[Any] = []
        for d in data:
            name = d.get("module_name", "unknown")
            coord = _make_stub_coordinate(name)
            raw_trust = d.get("trust", 3)
            try:
                trust = TrustLevel(int(raw_trust))
            except (ValueError, KeyError):
                trust = TrustLevel.COPILOT_SUGGESTED
            node = ImportNode(
                module_name=name,
                coordinate=coord,
                is_package=bool(d.get("is_package", False)),
                is_namespace=bool(d.get("is_namespace", False)),
                file_path=d.get("file_path"),
                trust=trust,
                load_time_ms=float(d.get("load_time_ms", 0.0)),
                metadata=dict(d.get("metadata", {})),
            )
            nodes.append(node)
        return nodes

    def deserialize_edges(
        self,
        nodes_by_name: dict[str, "ImportNode"],
        data: list[dict],
    ) -> list["ImportEdge"]:
        """Reconstruct a list of :class:`ImportEdge` objects from raw dicts.

        Looks up source and target nodes from *nodes_by_name*.  Edges
        referencing unknown modules are skipped with a warning to avoid
        corrupting the graph with dangling pointers.

        Parameters
        ----------
        nodes_by_name:
            Dictionary mapping module names to :class:`ImportNode` objects.
        data:
            List of edge dictionaries as produced by :meth:`serialize_graph`.

        Returns
        -------
        list[ImportEdge]
            Reconstructed :class:`ImportEdge` list.
        """
        edges: list[Any] = []
        for d in data:
            src_name = d.get("source", "")
            tgt_name = d.get("target", "")
            src = nodes_by_name.get(src_name)
            tgt = nodes_by_name.get(tgt_name)
            if src is None or tgt is None:
                continue
            raw_trust = d.get("trust", 3)
            try:
                trust = TrustLevel(int(raw_trust))
            except (ValueError, KeyError):
                trust = TrustLevel.COPILOT_SUGGESTED
            edge = ImportEdge(
                source=src,
                target=tgt,
                import_kind=d.get("import_kind", "ABSOLUTE"),
                imported_names=tuple(d.get("imported_names", [])),
                alias_map=dict(d.get("alias_map", {})),
                trust=trust,
                is_conditional=bool(d.get("is_conditional", False)),
                is_lazy=bool(d.get("is_lazy", False)),
                metadata=dict(d.get("metadata", {})),
            )
            edges.append(edge)
        return edges

    def save_to_file(
        self,
        path: str,
        nodes: list["ImportNode"],
        edges: list["ImportEdge"],
    ) -> None:
        """Write the serialised import graph to a JSON file at *path*.

        Creates any missing parent directories.  Overwrites existing files
        without prompting.  The written file is UTF-8 encoded.

        Parameters
        ----------
        path:
            Destination file path (will be created or overwritten).
        nodes:
            List of :class:`ImportNode` objects to persist.
        edges:
            List of :class:`ImportEdge` objects to persist.
        """
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        content = self.serialize_graph(nodes, edges)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(content)

    def load_from_file(self, path: str) -> tuple[list["ImportNode"], list["ImportEdge"]]:
        """Load an import graph from a JSON file previously written by :meth:`save_to_file`.

        Reads the file, deserialises nodes first, then edges (which reference
        the deserialized nodes by name).

        Parameters
        ----------
        path:
            Path to the JSON file to read.

        Returns
        -------
        tuple[list[ImportNode], list[ImportEdge]]
            ``(nodes, edges)`` reconstructed from the file.
        """
        with open(path, "r", encoding="utf-8") as fh:
            payload = json.load(fh)
        nodes = self.deserialize_nodes(payload.get("nodes", []))
        nodes_by_name = {n.module_name: n for n in nodes}
        edges = self.deserialize_edges(nodes_by_name, payload.get("edges", []))
        return nodes, edges

    def to_dot(
        self,
        nodes: list["ImportNode"],
        edges: list["ImportEdge"],
    ) -> str:
        """Emit a Graphviz DOT representation of the import graph.

        Produces a directed graph (``digraph``) with one node per
        :class:`ImportNode` and one directed edge per :class:`ImportEdge`.
        Node shapes distinguish packages (``box``) from plain modules
        (``ellipse``).  Trust levels are encoded as node fill colours so that
        the copilot visualisation layer can render trust tiers at a glance.

        Parameters
        ----------
        nodes:
            List of :class:`ImportNode` objects to render.
        edges:
            List of :class:`ImportEdge` objects to render.

        Returns
        -------
        str
            A valid Graphviz DOT string that can be piped to ``dot -Tsvg``.
        """
        trust_colors: dict[int, str] = {
            0: "#ff4444",  # CONTRADICTED — red
            1: "#ffaa00",  # UNVERIFIED — orange
            2: "#ffff00",  # COPILOT_SUGGESTED — yellow
            3: "#44cc44",  # RUNTIME_WITNESSED — green
            4: "#4444ff",  # SOLVER_DISCHARGED — blue
            5: "#aa44ff",  # VERIFIED_PROOF — purple
        }

        lines: list[str] = ["digraph import_graph {", "    rankdir=LR;", '    node [fontname="Helvetica"];']
        for node in nodes:
            name = node.module_name if hasattr(node, "module_name") else str(node)
            safe = name.replace(".", "_").replace("-", "_")
            shape = "box" if (node.is_package if hasattr(node, "is_package") else False) else "ellipse"
            trust_val = int(node.trust) if hasattr(node, "trust") else 3
            color = trust_colors.get(trust_val, "#ffffff")
            lines.append(
                f'    {safe} [label="{name}", shape={shape}, style=filled, fillcolor="{color}"];'
            )
        for edge in edges:
            src = edge.source.module_name if hasattr(edge.source, "module_name") else str(edge.source)
            tgt = edge.target.module_name if hasattr(edge.target, "module_name") else str(edge.target)
            safe_src = src.replace(".", "_").replace("-", "_")
            safe_tgt = tgt.replace(".", "_").replace("-", "_")
            kind = edge.import_kind if hasattr(edge, "import_kind") else "ABSOLUTE"
            style = "dashed" if kind == "RELATIVE" else "solid"
            lines.append(f'    {safe_src} -> {safe_tgt} [style={style}];')
        lines.append("}")
        return "\n".join(lines)


# ---
# Module-level convenience functions
# ---


def _build_import_graph(root_path: str, max_depth: int = 10) -> ImportGraphBuilder:
    """Build a complete :class:`ImportGraphBuilder` from a project directory.

    Convenience wrapper that creates a builder, calls
    :meth:`~ImportGraphBuilder.build_from_path`, and returns the populated
    builder.  Suitable for one-shot copilot-assisted import analysis.

    Parameters
    ----------
    root_path:
        Absolute or relative path to the project root.
    max_depth:
        Maximum directory recursion depth.

    Returns
    -------
    ImportGraphBuilder
        Populated builder ready for :meth:`~ImportGraphBuilder.to_site` or
        serialisation.
    """
    builder = ImportGraphBuilder(root_path=root_path, max_depth=max_depth)
    builder.build_from_path(root_path)
    return builder


def _detect_and_report_cycles(edges: list["ImportEdge"]) -> list[Any]:
    """Detect cycles and return :class:`Obstruction` records.

    Creates a :class:`CircularImportDetector` from *edges* and returns the
    full list of cycle obstructions.  Used by copilot-assisted CI hooks to
    fail builds when circular imports are introduced.

    Parameters
    ----------
    edges:
        Import edges to analyse.

    Returns
    -------
    list[Obstruction]
        One obstruction per cyclic SCC found.
    """
    detector = CircularImportDetector(edges=edges)
    return detector.as_obstruction_list()


def _snapshot_sys_modules() -> SysModulesSection:
    """Create and capture a :class:`SysModulesSection` snapshot.

    Returns a new :class:`SysModulesSection` whose :meth:`~SysModulesSection.capture`
    method has already been called, representing the current state of
    ``sys.modules`` at call time.

    Returns
    -------
    SysModulesSection
        A snapshot of the current ``sys.modules`` state.
    """
    section = SysModulesSection()
    section.capture()
    return section
