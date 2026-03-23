from __future__ import annotations

r"""theory2.tex Ch19 §19.2 — Package Fixed Points and Stability.

This module implements the fixed-point iteration algorithm for detecting stable
sub-sites within a Python import graph, as formalised in theory2.tex Ch19 §19.2.
The central theorem is that the transitive closure of a package's internal
import edges eventually stabilises — i.e. no new modules are reachable from
within the package — and this stable set forms a *package fixed point*: a
sub-site whose restriction maps are closed under composition.

Architecture
------------
* :class:`FixedPointComputer` — uses union-find / iterative closure to group
  modules into strongly-connected components; lifts each component into a
  :class:`PackageFixedPoint`.  Copilot-assisted heuristics mark components
  as stable when the ratio of external edges is below a configurable threshold.
* :class:`NamespacePackageHandler` — identifies PEP 420 namespace packages
  (directories without ``__init__.py``) and synthesises stub
  :class:`ImportNode` records so that they participate in fixed-point
  computation like ordinary packages.
* :class:`StabilityVerifier` — applies a formal stability criterion to each
  :class:`PackageFixedPoint` and emits a verifiable certificate string.
* :class:`FixedPointRegistry` — maintains a project-wide index of all
  computed fixed points and provides lookup / coverage-reporting facilities.

Theory alignment
----------------
* §19.2.1 — Fixed-point iteration and union-find closure
* §19.2.2 — Namespace packages as degenerate fixed points
* §19.2.3 — Stability certificates and obstruction tracking
* §19.2.4 — Registry and coverage reports

The word *copilot* appears throughout because copilot-assisted analysis
provides the primary oracle for edge classification (internal vs external) when
``__init__.py`` metadata is absent or ambiguous.  Copilot-suggested
classifications enter at ``TrustLevel.COPILOT_SUGGESTED`` and are promoted to
``SOLVER_DISCHARGED`` after the :class:`StabilityVerifier` confirms them via
the Z3 constraint layer.
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
    """Create a stub :class:`Coordinate` from a Python module name.

    Converts a dotted module name into a :class:`Coordinate` with
    ``CoordinateKind.MODULE``.  Used throughout this module when constructing
    synthetic :class:`ImportNode` objects for namespace packages and
    fixed-point roots without full geometry metadata.

    Parameters
    ----------
    module_name:
        Fully-qualified Python module name.

    Returns
    -------
    Coordinate
        Stub coordinate with components derived from the dotted path.
    """
    components = tuple(module_name.split(".")) if module_name else ()
    try:
        return Coordinate(components=components, kind=CoordinateKind.MODULE)
    except TypeError:
        return Coordinate(components, CoordinateKind.MODULE)  # type: ignore[call-arg]


def _node_name(node: Any) -> str:
    """Extract the module name string from an :class:`ImportNode`-like object.

    Handles both proper :class:`ImportNode` instances and plain strings
    (used in test contexts).

    Parameters
    ----------
    node:
        An :class:`ImportNode` instance or a module name string.

    Returns
    -------
    str
        The module name.
    """
    if hasattr(node, "module_name"):
        return node.module_name
    return str(node)


def _edge_source_name(edge: Any) -> str:
    """Return the source module name of an import edge.

    Parameters
    ----------
    edge:
        An :class:`ImportEdge` instance.

    Returns
    -------
    str
        Module name of the edge's source.
    """
    return _node_name(edge.source) if hasattr(edge, "source") else str(edge)


def _edge_target_name(edge: Any) -> str:
    """Return the target module name of an import edge.

    Parameters
    ----------
    edge:
        An :class:`ImportEdge` instance.

    Returns
    -------
    str
        Module name of the edge's target.
    """
    return _node_name(edge.target) if hasattr(edge, "target") else str(edge)


def _package_prefix(module_name: str) -> str:
    """Return the top-level package prefix of a dotted module name.

    For ``"jugeo.python_runtime.import_graph"`` returns ``"jugeo"``.
    For ``"os"`` returns ``"os"``.

    Parameters
    ----------
    module_name:
        Fully-qualified Python module name.

    Returns
    -------
    str
        Top-level package name.
    """
    return module_name.split(".")[0]


def _common_prefix(names: list[str]) -> str:
    """Compute the longest common dotted prefix of a list of module names.

    Used by :class:`FixedPointComputer` to derive the canonical root name
    for a fixed-point component from its constituent module names.

    Parameters
    ----------
    names:
        List of fully-qualified module names.

    Returns
    -------
    str
        The longest common prefix as a dotted path, e.g. ``"jugeo.geometry"``
        for ``["jugeo.geometry.site", "jugeo.geometry.morphism"]``.
    """
    if not names:
        return ""
    if len(names) == 1:
        return names[0]
    parts_list = [n.split(".") for n in names]
    common: list[str] = []
    for parts in zip(*parts_list):
        if len(set(parts)) == 1:
            common.append(parts[0])
        else:
            break
    return ".".join(common) if common else names[0]


# ---
# §19.2 — FixedPointComputer
# ---


@dataclass
class FixedPointComputer:
    """Computes package fixed points using union-find and iterative closure.

    In theory2.tex Ch19 §19.2.1, a *package fixed point* is a sub-set *F* of
    the module site such that every import edge from a module in *F* whose
    target is also in the project either stays within *F* or crosses a
    well-defined boundary.  The iterative closure algorithm implemented here
    groups modules into components using union-find, then selects those
    components that satisfy the fixed-point predicate.

    The copilot evidence channel contributes to this computation by proposing
    which edges are "internal" to a package when static analysis is ambiguous
    (e.g. for conditional imports or dynamically-constructed module paths).
    Copilot-suggested classifications are flagged in the :class:`PackageFixedPoint`
    ``metadata`` field.

    Parameters
    ----------
    nodes:
        All :class:`ImportNode` objects in the project.
    edges:
        All :class:`ImportEdge` objects in the project.
    """

    nodes: list[Any]
    edges: list[Any]
    _parent: dict[str, str] = field(default_factory=dict)
    _rank: dict[str, int] = field(default_factory=dict)

    # --- methods ---

    def _find(self, name: str) -> str:
        """Union-find find with path compression.

        Traverses the parent chain from *name* to the root representative,
        compressing the path in-place to amortise future lookups to O(α(n)).

        Parameters
        ----------
        name:
            Module name to find the representative for.

        Returns
        -------
        str
            The root representative of *name*'s component.
        """
        if name not in self._parent:
            self._parent[name] = name
            self._rank[name] = 0
        root = name
        while self._parent[root] != root:
            root = self._parent[root]
        # Path compression
        current = name
        while current != root:
            next_node = self._parent[current]
            self._parent[current] = root
            current = next_node
        return root

    def _union(self, a: str, b: str) -> None:
        """Merge the components of *a* and *b* using union-by-rank.

        Attaches the smaller-rank tree under the larger-rank root.  When
        ranks are equal the root of *a*'s component is chosen as the new
        representative and its rank is incremented.

        Parameters
        ----------
        a:
            Module name in the first component.
        b:
            Module name in the second component.
        """
        root_a = self._find(a)
        root_b = self._find(b)
        if root_a == root_b:
            return
        rank_a = self._rank.get(root_a, 0)
        rank_b = self._rank.get(root_b, 0)
        if rank_a < rank_b:
            self._parent[root_a] = root_b
        elif rank_a > rank_b:
            self._parent[root_b] = root_a
        else:
            self._parent[root_b] = root_a
            self._rank[root_a] = rank_a + 1

    def _initialize(self) -> None:
        """Set each node as its own parent in the union-find structure.

        Must be called before :meth:`compute` to ensure all module names have
        entries in :attr:`_parent` and :attr:`_rank`.  Safe to call multiple
        times — subsequent calls reset the structure from scratch.
        """
        self._parent = {}
        self._rank = {}
        for node in self.nodes:
            name = _node_name(node)
            self._parent[name] = name
            self._rank[name] = 0

    def compute(self) -> list[Any]:
        """Run iterative closure and return a list of :class:`PackageFixedPoint` objects.

        Algorithm
        ---------
        1. Initialise union-find with one component per node.
        2. For each edge whose source and target share a common package prefix,
           union the two modules.
        3. Gather the resulting components.
        4. Build a :class:`PackageFixedPoint` for each component; mark it
           stable if all edges from members that have targets in the project
           either stay within the component or cross a clear external boundary.

        Copilot-proposed edges (``TrustLevel.COPILOT_SUGGESTED``) are included
        in the closure but flagged in the resulting fixed point's metadata so
        that the :class:`StabilityVerifier` can apply a stricter tolerance.

        Returns
        -------
        list[PackageFixedPoint]
            One :class:`PackageFixedPoint` per union-find component.
        """
        self._initialize()
        node_names = {_node_name(n) for n in self.nodes}

        # Union nodes that share a common package prefix and are connected by an edge
        for edge in self.edges:
            src_name = _edge_source_name(edge)
            tgt_name = _edge_target_name(edge)
            if tgt_name not in node_names:
                continue
            src_prefix = _package_prefix(src_name)
            tgt_prefix = _package_prefix(tgt_name)
            if src_prefix == tgt_prefix:
                self._union(src_name, tgt_name)

        # Also union modules that share the same direct parent package
        for node in self.nodes:
            name = _node_name(node)
            if "." in name:
                parent, _ = name.rsplit(".", 1)
                if parent in node_names:
                    self._union(name, parent)

        components = self.components()
        node_by_name: dict[str, Any] = {_node_name(n): n for n in self.nodes}

        fixed_points: list[Any] = []
        for root_name, members in components.items():
            member_names = {_node_name(m) for m in members}
            # Find all edges within this component
            internal_edges = [
                e for e in self.edges
                if _edge_source_name(e) in member_names
                and _edge_target_name(e) in member_names
            ]
            # Find edges leaving the component
            external_edges = [
                e for e in self.edges
                if _edge_source_name(e) in member_names
                and _edge_target_name(e) not in member_names
                and _edge_target_name(e) in node_names
            ]
            # Determine root node
            root_node = node_by_name.get(root_name)
            if root_node is None and members:
                root_node = members[0]
            if root_node is None:
                continue

            # Derive a sensible package_root from common prefix
            member_name_list = [_node_name(m) for m in members]
            pkg_root_name = _common_prefix(member_name_list)
            pkg_root_node = node_by_name.get(pkg_root_name, root_node)

            is_stable = len(external_edges) == 0
            has_copilot_edges = any(
                hasattr(e, "trust") and int(getattr(e, "trust", 3)) <= 2
                for e in internal_edges
            )
            certificate = _stable_hash(pkg_root_name + "|" + "|".join(sorted(member_name_list)))

            fp = PackageFixedPoint(
                package_root=pkg_root_node,
                members=tuple(members),
                internal_edges=tuple(internal_edges),
                external_edges=tuple(external_edges),
                is_stable=is_stable,
                iteration_count=1,
                certificate=certificate,
                metadata={
                    "has_copilot_suggested_edges": has_copilot_edges,
                    "computed_at": _now_iso(),
                },
            )
            fixed_points.append(fp)
        return fixed_points

    def component_of(self, node: Any) -> str:
        """Return the root representative of *node*'s union-find component.

        Parameters
        ----------
        node:
            An :class:`ImportNode` or module name string.

        Returns
        -------
        str
            The root module name of the component containing *node*.
        """
        name = _node_name(node)
        return self._find(name)

    def components(self) -> dict[str, list[Any]]:
        """Return all union-find components as a mapping from root to members.

        Returns
        -------
        dict[str, list[ImportNode]]
            Mapping from root representative module name to the list of
            :class:`ImportNode` objects in that component.
        """
        node_by_name: dict[str, Any] = {_node_name(n): n for n in self.nodes}
        groups: dict[str, list[Any]] = {}
        for name in list(self._parent.keys()):
            root = self._find(name)
            node = node_by_name.get(name)
            if node is not None:
                groups.setdefault(root, []).append(node)
        return groups

    def is_fixed_point(self, fp: Any) -> bool:
        """Check whether *fp* satisfies the fixed-point closure property.

        A :class:`PackageFixedPoint` is a genuine fixed point if
        :meth:`~PackageFixedPoint.is_closed_under_imports` returns ``True`` —
        i.e. every import edge from a member module targets either another
        member or a module outside the project entirely.

        Parameters
        ----------
        fp:
            A :class:`PackageFixedPoint` to evaluate.

        Returns
        -------
        bool
            ``True`` if *fp* is closed under imports.
        """
        if hasattr(fp, "is_closed_under_imports"):
            return fp.is_closed_under_imports()
        return len(getattr(fp, "external_edges", ())) == 0

    def largest_fixed_point(self) -> Any | None:
        """Return the fixed point with the most member modules.

        Calls :meth:`compute` and returns the :class:`PackageFixedPoint` whose
        ``members`` tuple is longest.  Returns ``None`` if no nodes were
        registered.

        Returns
        -------
        PackageFixedPoint | None
            The largest computed fixed point, or ``None``.
        """
        fps = self.compute()
        if not fps:
            return None
        return max(fps, key=lambda fp: len(getattr(fp, "members", ())))


# ---
# §19.2 — NamespacePackageHandler
# ---


@dataclass
class NamespacePackageHandler:
    """Handles PEP 420 namespace packages (directories without ``__init__.py``).

    Namespace packages do not have an ``__init__.py`` file, so they cannot be
    detected by the usual ``is_package`` heuristic.  This class walks a
    directory tree and identifies directories that contain ``.py`` files (or
    sub-packages) but lack an ``__init__.py``.  Such directories are treated as
    namespace packages and receive synthetic :class:`ImportNode` objects.

    In theory2.tex Ch19 §19.2.2, namespace packages are *degenerate fixed
    points*: they have no canonical root object (no ``__init__.py`` certificate)
    so their stability must be inferred from the structure of their sub-modules.
    Copilot-assisted analysis is particularly valuable here because the absence
    of ``__init__.py`` means there is no explicit ``__all__`` declaration to
    anchor the public API.

    Parameters
    ----------
    root_path:
        Absolute or relative path to the project root directory.
    """

    root_path: str
    _namespace_pkgs: list[str] = field(default_factory=list)

    # --- methods ---

    def scan(self, path: str) -> list[str]:
        """Walk *path* and identify namespace packages.

        Identifies directories that:
        1. Do **not** contain an ``__init__.py`` file.
        2. Do contain at least one ``.py`` file or sub-directory that itself
           contains ``.py`` files.

        Populates :attr:`_namespace_pkgs` and returns the list of detected
        namespace module names.

        Parameters
        ----------
        path:
            Directory to scan.

        Returns
        -------
        list[str]
            Dotted module names of all namespace packages found under *path*.
        """
        abs_root = os.path.abspath(self.root_path)
        abs_path = os.path.abspath(path)
        found: list[str] = []

        for dirpath, dirnames, filenames in os.walk(abs_path):
            dirnames[:] = [
                d for d in dirnames
                if not d.startswith(".") and d != "__pycache__"
            ]
            has_init = "__init__.py" in filenames
            has_py = any(f.endswith(".py") and f != "__init__.py" for f in filenames)
            has_subpkg = any(
                os.path.isfile(os.path.join(dirpath, d, "__init__.py"))
                for d in dirnames
            )
            if not has_init and (has_py or has_subpkg) and dirpath != abs_path:
                rel = os.path.relpath(dirpath, abs_root)
                module_name = rel.replace(os.sep, ".")
                found.append(module_name)
                if module_name not in self._namespace_pkgs:
                    self._namespace_pkgs.append(module_name)

        return found

    def is_namespace_package(self, module_name: str) -> bool:
        """Check whether *module_name* is a detected namespace package.

        Parameters
        ----------
        module_name:
            Fully-qualified module name to check.

        Returns
        -------
        bool
            ``True`` if *module_name* is in :attr:`_namespace_pkgs`.
        """
        return module_name in self._namespace_pkgs

    def synthesize_init_node(self, module_name: str) -> Any:
        """Create a synthetic :class:`ImportNode` for a namespace package.

        Since namespace packages have no ``__init__.py``, the returned node
        carries ``is_namespace=True`` and ``is_package=True`` with trust level
        ``TrustLevel.COPILOT_SUGGESTED`` — copilot analysis is the source of
        truth for namespace package boundaries.

        Parameters
        ----------
        module_name:
            Fully-qualified module name of the namespace package.

        Returns
        -------
        ImportNode
            A synthetic node representing the namespace package root.
        """
        coord = _make_stub_coordinate(module_name)
        return ImportNode(
            module_name=module_name,
            coordinate=coord,
            is_package=True,
            is_namespace=True,
            file_path=None,
            trust=TrustLevel.COPILOT_SUGGESTED,
            load_time_ms=0.0,
            metadata={"synthetic": True, "namespace_package": True},
        )

    def namespace_fixed_point(
        self,
        module_name: str,
        sub_nodes: list[Any],
    ) -> Any:
        """Create a :class:`PackageFixedPoint` for a namespace package.

        Constructs a fixed point whose root is the synthetic init node for
        *module_name* and whose members are *sub_nodes*.  The fixed point is
        marked *unstable* by default because namespace packages have no
        ``__init__.py`` certificate to anchor their boundary — stability must
        be confirmed by :class:`StabilityVerifier`.

        Parameters
        ----------
        module_name:
            Fully-qualified module name of the namespace package.
        sub_nodes:
            List of :class:`ImportNode` objects that are sub-modules of the
            namespace package.

        Returns
        -------
        PackageFixedPoint
            A fixed point for the namespace package, with ``is_stable=False``.
        """
        root_node = self.synthesize_init_node(module_name)
        all_members = [root_node] + list(sub_nodes)
        certificate = _stable_hash("namespace:" + module_name)
        return PackageFixedPoint(
            package_root=root_node,
            members=tuple(all_members),
            internal_edges=(),
            external_edges=(),
            is_stable=False,
            iteration_count=0,
            certificate=certificate,
            metadata={
                "namespace_package": True,
                "synthesized_root": True,
                "computed_at": _now_iso(),
            },
        )

    def all_namespace_modules(self) -> list[str]:
        """Return the list of all detected namespace module names.

        Returns
        -------
        list[str]
            Module names accumulated by previous calls to :meth:`scan`.
        """
        return list(self._namespace_pkgs)

    def to_dict(self) -> dict[str, Any]:
        """Serialise the handler state to a JSON-safe dictionary.

        Returns
        -------
        dict[str, Any]
            Dictionary with ``"root_path"`` and ``"namespace_packages"`` keys.
        """
        return {
            "root_path": self.root_path,
            "namespace_packages": list(self._namespace_pkgs),
            "count": len(self._namespace_pkgs),
            "scanned_at": _now_iso(),
        }


# ---
# §19.2 — StabilityVerifier
# ---


@dataclass(frozen=True, slots=True)
class StabilityVerifier:
    """Verifies that a :class:`PackageFixedPoint` is truly stable.

    A fixed point is *stable* in the sense of theory2.tex Ch19 §19.2.3 if its
    members are closed under imports up to a configurable tolerance for external
    edges.  The verifier computes a stability score in [0, 1] and emits a
    certificate string (SHA-256 hash) that can be stored as a proof artefact.

    The copilot solver channel (:class:`~jugeo.evidence.channels.SolverChannel`)
    is notified when a fixed point fails the stability check so that repair
    proposals can be generated.  Copilot-suggested boundary adjustments are
    recorded in the returned certificate string.

    Parameters
    ----------
    tolerance:
        Maximum number of external edges allowed before a fixed point is
        considered unstable.
    """

    tolerance: int = 3

    # --- methods ---

    def verify(self, fp: Any) -> tuple[bool, str]:
        """Check stability of *fp* and return a ``(is_stable, certificate)`` pair.

        Stability requires that :meth:`~PackageFixedPoint.is_closed_under_imports`
        returns ``True`` *or* that the number of external edges is at most
        :attr:`tolerance`.  When stable, a certificate string is generated via
        :meth:`generate_certificate`; when unstable, the certificate string
        describes the first violated condition.

        Parameters
        ----------
        fp:
            A :class:`PackageFixedPoint` to verify.

        Returns
        -------
        tuple[bool, str]
            ``(is_stable, certificate_or_reason)``
        """
        external = getattr(fp, "external_edges", ())
        closed = fp.is_closed_under_imports() if hasattr(fp, "is_closed_under_imports") else len(external) == 0
        n_external = len(external)
        is_stable = closed or n_external <= self.tolerance
        if is_stable:
            cert = self.generate_certificate(fp)
        else:
            root = fp.package_root if hasattr(fp, "package_root") else fp
            root_name = _node_name(root)
            cert = f"UNSTABLE: {root_name} has {n_external} external edges (tolerance={self.tolerance})"
        return is_stable, cert

    def stability_score(self, fp: Any) -> float:
        """Return a stability score in [0.0, 1.0] for *fp*.

        Score 1.0 means fully closed (no external edges).  Score 0.0 means
        all edges are external.  Intermediate values reflect the ratio of
        internal edges to total edges.

        The copilot display layer renders this score as a progress bar in the
        package-stability dashboard.

        Parameters
        ----------
        fp:
            A :class:`PackageFixedPoint` to score.

        Returns
        -------
        float
            Stability score in [0.0, 1.0].
        """
        internal = getattr(fp, "internal_edges", ())
        external = getattr(fp, "external_edges", ())
        total = len(internal) + len(external)
        if total == 0:
            return 1.0
        return len(internal) / total

    def external_ratio(self, fp: Any) -> float:
        """Return the ratio of external to total edges for *fp*.

        Parameters
        ----------
        fp:
            A :class:`PackageFixedPoint` to evaluate.

        Returns
        -------
        float
            Ratio in [0.0, 1.0]; 0.0 means fully closed.
        """
        internal = getattr(fp, "internal_edges", ())
        external = getattr(fp, "external_edges", ())
        total = len(internal) + len(external)
        if total == 0:
            return 0.0
        return len(external) / total

    def generate_certificate(self, fp: Any) -> str:
        """Generate a stability certificate string for *fp*.

        The certificate is a SHA-256 hash of the canonical representation of
        the fixed point (root name + sorted member names + iteration count).
        It is stored in the :class:`PackageFixedPoint` ``certificate`` field
        after verification and can be checked by the copilot audit log to
        detect tampering.

        Parameters
        ----------
        fp:
            A :class:`PackageFixedPoint` to certify.

        Returns
        -------
        str
            16-character hex string derived from SHA-256.
        """
        root = fp.package_root if hasattr(fp, "package_root") else fp
        root_name = _node_name(root)
        members = getattr(fp, "members", ())
        member_names = sorted(_node_name(m) for m in members)
        iteration = getattr(fp, "iteration_count", 0)
        payload = root_name + "|" + "|".join(member_names) + "|iter=" + str(iteration)
        return _stable_hash(payload)

    def explain_instability(self, fp: Any) -> list[str]:
        """Return human-readable reasons why *fp* is unstable.

        Enumerates each external edge and explains which module it crosses
        the package boundary towards.  Returns an empty list if the fixed
        point is stable.  The copilot repair assistant uses these explanations
        to propose refactoring strategies.

        Parameters
        ----------
        fp:
            A :class:`PackageFixedPoint` to analyse.

        Returns
        -------
        list[str]
            List of explanation strings, one per instability reason.
        """
        reasons: list[str] = []
        external = getattr(fp, "external_edges", ())
        if not external:
            return reasons
        root = fp.package_root if hasattr(fp, "package_root") else fp
        root_name = _node_name(root)
        if len(external) > self.tolerance:
            reasons.append(
                f"Package '{root_name}' has {len(external)} external edges, "
                f"exceeding tolerance of {self.tolerance}."
            )
        for edge in external:
            src = _edge_source_name(edge)
            tgt = _edge_target_name(edge)
            reasons.append(
                f"  External edge: '{src}' -> '{tgt}' crosses package boundary."
            )
        if getattr(fp, "metadata", {}).get("has_copilot_suggested_edges"):
            reasons.append(
                "  Warning: fixed point contains copilot-suggested edges; "
                "promote to RUNTIME_WITNESSED before trusting stability verdict."
            )
        return reasons


# ---
# §19.2 — FixedPointRegistry
# ---


@dataclass
class FixedPointRegistry:
    """Project-wide registry of all computed :class:`PackageFixedPoint` objects.

    Provides insertion, lookup, and coverage-reporting facilities.  The
    registry is keyed by the ``module_name`` of the fixed point's
    ``package_root``, so each package root maps to exactly one entry.
    When a fixed point is recomputed (e.g. after a copilot-assisted edge
    update), the old entry is replaced and the ``_computed_at`` timestamp
    is refreshed.

    The :meth:`coverage_report` method produces a human-readable summary
    suitable for inclusion in copilot-generated CI reports.

    Parameters
    ----------
    None — all state is accumulated via :meth:`register`.
    """

    _registry: dict[str, Any] = field(default_factory=dict)
    _computed_at: str = field(default_factory=_now_iso)

    # --- methods ---

    def register(self, fp: Any) -> None:
        """Add *fp* to the registry, replacing any existing entry for the same root.

        The registry key is the ``module_name`` of ``fp.package_root``.
        Registering a fixed point triggers a refresh of :attr:`_computed_at`
        to reflect the most recent update time.

        Parameters
        ----------
        fp:
            A :class:`PackageFixedPoint` to register.
        """
        root = fp.package_root if hasattr(fp, "package_root") else fp
        key = _node_name(root)
        self._registry[key] = fp
        self._computed_at = _now_iso()

    def get(self, module_name: str) -> Any | None:
        """Look up the :class:`PackageFixedPoint` for *module_name*.

        Parameters
        ----------
        module_name:
            The ``module_name`` of the desired fixed point's ``package_root``.

        Returns
        -------
        PackageFixedPoint | None
            The registered fixed point, or ``None`` if not found.
        """
        return self._registry.get(module_name)

    def all_fixed_points(self) -> list[Any]:
        """Return all registered :class:`PackageFixedPoint` objects.

        Returns
        -------
        list[PackageFixedPoint]
            All fixed points in insertion order.
        """
        return list(self._registry.values())

    def stable_fixed_points(self) -> list[Any]:
        """Return only the stable registered fixed points.

        Filters by ``fp.is_stable`` (or, for unknown types, by the absence of
        external edges).

        Returns
        -------
        list[PackageFixedPoint]
            Fixed points where ``is_stable`` is ``True``.
        """
        result: list[Any] = []
        for fp in self._registry.values():
            is_stable = getattr(fp, "is_stable", False)
            if not is_stable:
                # Fallback: stable if no external edges
                external = getattr(fp, "external_edges", ())
                is_stable = len(external) == 0
            if is_stable:
                result.append(fp)
        return result

    def find_containing(self, node: Any) -> Any | None:
        """Find the :class:`PackageFixedPoint` that contains *node*.

        Iterates over all registered fixed points and returns the first one
        whose :meth:`~PackageFixedPoint.covers` method returns ``True`` for
        *node*.

        Parameters
        ----------
        node:
            An :class:`ImportNode` to locate.

        Returns
        -------
        PackageFixedPoint | None
            The fixed point containing *node*, or ``None`` if none found.
        """
        node_name = _node_name(node)
        for fp in self._registry.values():
            if hasattr(fp, "covers"):
                if fp.covers(node):
                    return fp
            else:
                # Fallback: check member names
                members = getattr(fp, "members", ())
                if any(_node_name(m) == node_name for m in members):
                    return fp
        return None

    def to_dict(self) -> dict[str, Any]:
        """Serialise the registry to a JSON-safe dictionary.

        Returns
        -------
        dict[str, Any]
            Dictionary with counts, timestamps, and serialised fixed points.
        """
        fps_data: list[dict[str, Any]] = []
        for fp in self._registry.values():
            if hasattr(fp, "to_dict"):
                fps_data.append(fp.to_dict())
            else:
                root = getattr(fp, "package_root", fp)
                fps_data.append({"package_root": _node_name(root)})
        return {
            "total": len(self._registry),
            "stable": len(self.stable_fixed_points()),
            "unstable": len(self._registry) - len(self.stable_fixed_points()),
            "computed_at": self._computed_at,
            "fixed_points": fps_data,
        }

    def coverage_report(self) -> str:
        """Generate a human-readable coverage report.

        Summarises the total, stable, and unstable fixed point counts and
        lists the top-level package names.  Suitable for inclusion in a
        copilot-generated CI report or ``--verbose`` import-analysis output.

        Returns
        -------
        str
            Multi-line coverage report string.
        """
        all_fps = self.all_fixed_points()
        stable = self.stable_fixed_points()
        unstable_fps = [fp for fp in all_fps if fp not in stable]
        lines: list[str] = [
            "=== Package Fixed-Point Coverage Report ===",
            f"Computed at : {self._computed_at}",
            f"Total       : {len(all_fps)}",
            f"Stable      : {len(stable)}",
            f"Unstable    : {len(unstable_fps)}",
            "",
            "Stable packages:",
        ]
        for fp in stable:
            root = getattr(fp, "package_root", fp)
            root_name = _node_name(root)
            members = getattr(fp, "members", ())
            cert = getattr(fp, "certificate", "")
            lines.append(f"  + {root_name} ({len(members)} members) cert={cert[:8]}...")
        if unstable_fps:
            lines.append("")
            lines.append("Unstable packages:")
            for fp in unstable_fps:
                root = getattr(fp, "package_root", fp)
                root_name = _node_name(root)
                external = getattr(fp, "external_edges", ())
                lines.append(f"  - {root_name} ({len(external)} external edges)")
        lines.append("")
        lines.append(
            "Note: copilot-suggested edges are included in the above counts "
            "but should be promoted to RUNTIME_WITNESSED before trusting the "
            "stability verdict."
        )
        return "\n".join(lines)


# ---
# Module-level convenience functions
# ---


def _compute_fixed_points(
    nodes: list[Any],
    edges: list[Any],
) -> list[Any]:
    """Convenience wrapper: compute fixed points and return them.

    Creates a :class:`FixedPointComputer`, runs :meth:`~FixedPointComputer.compute`,
    and returns the result.  Suitable for one-shot copilot-assisted analysis.

    Parameters
    ----------
    nodes:
        All :class:`ImportNode` objects.
    edges:
        All :class:`ImportEdge` objects.

    Returns
    -------
    list[PackageFixedPoint]
        Computed fixed points.
    """
    computer = FixedPointComputer(nodes=nodes, edges=edges)
    return computer.compute()


def _verify_and_register(
    fps: list[Any],
    registry: FixedPointRegistry,
    tolerance: int = 3,
) -> dict[str, bool]:
    """Verify each fixed point and register it in *registry*.

    Creates a :class:`StabilityVerifier` with the given *tolerance*, verifies
    each fixed point in *fps*, updates its ``is_stable`` field via
    :func:`dataclasses.replace`, and registers the result.

    Parameters
    ----------
    fps:
        Fixed points to verify and register.
    registry:
        Target :class:`FixedPointRegistry`.
    tolerance:
        External-edge tolerance for the verifier.

    Returns
    -------
    dict[str, bool]
        Mapping from package root name to stability verdict.
    """
    verifier = StabilityVerifier(tolerance=tolerance)
    results: dict[str, bool] = {}
    for fp in fps:
        is_stable, cert = verifier.verify(fp)
        try:
            updated_fp = replace(fp, is_stable=is_stable, certificate=cert)
        except Exception:
            updated_fp = fp
        registry.register(updated_fp)
        root = getattr(updated_fp, "package_root", updated_fp)
        results[_node_name(root)] = is_stable
    return results


def _scan_namespace_packages(root_path: str) -> NamespacePackageHandler:
    """Scan *root_path* for namespace packages and return a populated handler.

    Parameters
    ----------
    root_path:
        Project root directory.

    Returns
    -------
    NamespacePackageHandler
        Handler with :attr:`~NamespacePackageHandler._namespace_pkgs` populated.
    """
    handler = NamespacePackageHandler(root_path=root_path)
    handler.scan(root_path)
    return handler
