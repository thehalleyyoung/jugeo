"""
jugeo.python_runtime.generated_contracts.algorithms

theory2.tex Ch21 — Core algorithms for analyzing annotations, decorators, and
registries together.

The planner-executor-normalizer pattern mirrors the morphism-composition-
normalization triad in the coordinate category:

  - The Planner constructs an AnalysisPlan (a morphism target specification).
  - The Executor applies the plan to concrete Python objects (morphism application).
  - The Normalizer deduplicates and canonicalises the result (co-equalizer step).

Together the three components form a pipeline:

    target_objects
        → Planner.create_plan()
        → Executor.execute(plan, target_objects)
        → Normalizer.normalize(result)
        → final AnalysisResult

The AnnotationGraph provides a DAG representation of annotation dependencies
(forward references, generic nesting) used by the annotations phase.

Exports:
    AnnotationsDecoratorsRegistriesGeneratedPlanner
    AnnotationsDecoratorsRegistriesGeneratedExecutor
    AnnotationsDecoratorsRegistriesGeneratedNormalizer
    AnnotationGraph, AnnotationGraphBuilder
    AnalysisPlan, AnalysisResult
"""

from __future__ import annotations

import abc
import enum
import functools
import inspect
import logging
import threading
import time
import typing
import uuid
from dataclasses import dataclass, field, replace
from typing import Any, Iterator, Optional

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────────────
# Standard jugeo imports with inline stubs
# ──────────────────────────────────────────────────────────────────────────────

try:
    from jugeo.geometry.site import (
        CoordinateObject, CoordinateKind, CoordinateMorphism, MorphismKind,
        Site, SiteBuilder,
    )
except Exception:
    # copilot: geometry stubs keep algorithms.py portable
    class CoordinateKind(enum.Enum):
        MODULE = "module"; FUNCTION = "function"; INTERFACE = "interface"
        TEST = "test"; THEOREM = "theorem"; REGION = "region"
    class MorphismKind(enum.Enum):
        RESTRICTION = "restriction"; INCLUSION = "inclusion"
        TRANSPORT = "transport"; REFINEMENT = "refinement"
    @dataclass(frozen=True, slots=True)
    class CoordinateObject:
        components: tuple[str, ...] = ()
        kind: Any = None
        support_labels: frozenset[str] = field(default_factory=frozenset)
        metadata: dict = field(default_factory=dict)
    class CoordinateMorphism:
        def __init__(self, source, target, reason=""): self.source=source; self.target=target; self.reason=reason
    class Site: pass
    class SiteBuilder: pass

try:
    from jugeo.judgments.judgment_terms import (
        Judgment, JudgmentStatus, TrustLevel, Proposition, PropositionKind,
        Carrier, EvidenceBundle, EvidenceItem, EvidenceItemKind,
        ResidualObligation, Obstruction, TrustAnnotation, Provenance, ProvenanceSource,
    )
except Exception:
    class TrustLevel(enum.IntEnum):
        CONTRADICTED=0; UNVERIFIED=1; ORACLE_PROPOSED=2
        RUNTIME_WITNESSED=3; SOLVER_DISCHARGED=4; VERIFIED_PROOF=5
    class JudgmentStatus(enum.Enum):
        PROPOSED="proposed"; CHALLENGED="challenged"; SETTLED="settled"; OBSTRUCTED="obstructed"
    class PropositionKind(enum.Enum):
        STRUCTURAL="structural"; BEHAVIORAL="behavioral"; RELATIONAL="relational"
        RESOURCE="resource"; SEMANTIC="semantic"
    class EvidenceItemKind(enum.Enum):
        SOLVER_PROOF="solver_proof"; RUNTIME_WITNESS="runtime_witness"
        ORACLE_PROPOSAL="oracle_proposal"; FORMAL_PROOF="formal_proof"
    class ProvenanceSource(enum.Enum):
        SOLVER="solver"; RUNTIME="runtime"; ORACLE="oracle"; HUMAN="human"; COMPOSED="composed"
    @dataclass(frozen=True, slots=True)
    class Proposition:
        kind: Any = None; formula: str = ""; free_variables: tuple[str,...] = ()
        metadata: dict = field(default_factory=dict)
    @dataclass(frozen=True, slots=True)
    class Carrier:
        name: str = ""; parameters: tuple[str,...] = (); is_dependent: bool = False
        metadata: dict = field(default_factory=dict)
    @dataclass(frozen=True, slots=True)
    class EvidenceItem:
        kind: Any = None; payload: dict = field(default_factory=dict); trust_level: Any = None
        channel: str = ""; timestamp: str = ""; expiry: str = ""; provenance: tuple[str,...] = ()
    @dataclass(frozen=True, slots=True)
    class EvidenceBundle:
        items: tuple[Any,...] = ()
    @dataclass(frozen=True, slots=True)
    class ResidualObligation:
        description: str = ""; obligation_id: str = ""; priority: int = 1
        is_discharged: bool = False
        def discharge(self, evidence=""): return replace(self, is_discharged=True)
    @dataclass(frozen=True, slots=True)
    class Obstruction:
        description: str = ""; obstruction_id: str = ""; severity: int = 1
    @dataclass(frozen=True, slots=True)
    class TrustAnnotation:
        level: Any = None; rationale: str = ""
    @dataclass(frozen=True, slots=True)
    class Provenance:
        sources: tuple[Any,...] = (); chain: tuple[str,...] = ()
    @dataclass(frozen=True, slots=True)
    class Judgment:
        coordinate: Any = None; proposition: Any = None; carrier: Any = None
        evidence: Any = None; obligations: tuple = (); obstructions: tuple = ()
        trust: Any = None; provenance: Any = None

try:
    from jugeo.python_runtime.generated_contracts.models import (
        AnnotationContract, ContractRecord, DecoratorTransformer, RegistrySection,
    )
except ImportError:
    @dataclass(frozen=True, slots=True)
    class AnnotationContract:
        symbol_name: str = ""; annotation_text: str = ""; trust_level: Any = None
        is_discharged: bool = False
    @dataclass(frozen=True, slots=True)
    class ContractRecord:
        coordinate_key: str = ""; contracts: tuple = (); is_complete: bool = False
    @dataclass(frozen=True, slots=True)
    class DecoratorTransformer:
        decorator_name: str = ""; source_qualname: str = ""; target_qualname: str = ""
        morphism_kind: str = "REFINEMENT"
    @dataclass(frozen=True, slots=True)
    class RegistrySection:
        registry_name: str = ""; entries: tuple = (); is_covering: bool = False


# ──────────────────────────────────────────────────────────────────────────────
# Module-level constants
# ──────────────────────────────────────────────────────────────────────────────

# copilot: ordered list of analysis phases; Executor iterates this list
_ANALYSIS_PHASES: list[str] = [
    "annotations",
    "decorators",
    "registries",
    "contracts",
    "burden",
]

# copilot: the default trust ceiling limits how high automatic discharge can promote
_DEFAULT_TRUST_CEILING: Any = TrustLevel.SOLVER_DISCHARGED

# copilot: known decorator names mapped to their MorphismKind equivalent
_KNOWN_DECORATORS: dict[str, str] = {
    "staticmethod":       "INCLUSION",
    "classmethod":        "TRANSPORT",
    "property":           "REFINEMENT",
    "functools.wraps":    "INCLUSION",
    "functools.lru_cache": "RESTRICTION",
    "functools.cache":    "RESTRICTION",
    "abc.abstractmethod": "REFINEMENT",
    "dataclasses.dataclass": "RESTRICTION",
    "typing.overload":    "REFINEMENT",
    "contextlib.contextmanager": "TRANSPORT",
}

# copilot: heuristics for estimating annotation count on an object
_ANNOTATION_ESTIMATE_PER_ARG = 3   # existence + consistency + completeness
_REGISTRY_ESTIMATE_BASE      = 5   # conservative floor for registry obligations


# ──────────────────────────────────────────────────────────────────────────────
# AnalysisPlan — immutable plan specification (frozen dataclass)
# ──────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True, slots=True)
class AnalysisPlan:
    """
    Immutable specification of an analysis run.

    theory2.tex Ch21 defines a plan as a pre-morphism: it specifies the
    category of objects to be analysed (targets), the sequence of
    transformations to apply (phases), and an upper bound on the trust
    level that automatic discharge may reach (trust_ceiling).

    Fields:
        plan_id:                UUID string for this plan
        targets:                qualified names of objects to analyse
        phases:                 ordered sequence of analysis phase names
        estimated_obligations:  rough upper bound on obligations expected
        trust_ceiling:          maximum TrustLevel for auto-discharge
        created_at:             ISO-8601 creation timestamp
        metadata:               arbitrary extra fields
    """
    plan_id:                str   = field(default_factory=lambda: str(uuid.uuid4()))
    targets:                tuple[str,...] = ()
    phases:                 tuple[str,...] = tuple(_ANALYSIS_PHASES)
    estimated_obligations:  int   = 0
    trust_ceiling:          Any   = None
    created_at:             str   = field(
        default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    )
    metadata:               dict  = field(default_factory=dict)

    def phase_count(self) -> int:
        """Return the number of analysis phases in this plan."""
        return len(self.phases)

    def target_count(self) -> int:
        """Return the number of analysis targets."""
        return len(self.targets)

    def summary(self) -> str:
        """Return a compact one-line summary of this plan."""
        return (
            f"AnalysisPlan({self.plan_id[:8]}…) "
            f"targets={self.target_count()} phases={self.phase_count()} "
            f"est={self.estimated_obligations} ceiling={self.trust_ceiling}"
        )

    def to_dict(self) -> dict:
        """Serialize this plan to a plain-Python dict."""
        return {
            "plan_id":               self.plan_id,
            "targets":               list(self.targets),
            "phases":                list(self.phases),
            "estimated_obligations": self.estimated_obligations,
            "trust_ceiling":         str(self.trust_ceiling),
            "created_at":            self.created_at,
            "metadata":              self.metadata,
        }


# ──────────────────────────────────────────────────────────────────────────────
# AnalysisResult — mutable aggregation of findings
# ──────────────────────────────────────────────────────────────────────────────

@dataclass(slots=True)
class AnalysisResult:
    """
    Mutable container accumulating all findings from an Executor run.

    Mutable because the Executor appends to it incrementally as each
    phase completes.  The Normalizer then produces a deduplicated,
    immutable snapshot (also an AnalysisResult, but with stable counts).

    theory2.tex Ch21 §21.6 uses AnalysisResult as the image of the analysis
    functor: it is the colimit of all per-phase sub-results.
    """
    plan_id:           str         = ""
    judgments:         list        = field(default_factory=list)
    obligations:       list        = field(default_factory=list)
    obstructions:      list        = field(default_factory=list)
    annotations_found: int         = 0
    contracts_found:   int         = 0
    registries_found:  int         = 0
    burden_score:      float       = 0.0
    elapsed_seconds:   float       = 0.0
    errors:            list[str]   = field(default_factory=list)

    def add_judgment(self, j: Any) -> None:
        """Append a Judgment to the result."""
        self.judgments.append(j)

    def add_obligation(self, ob: Any) -> None:
        """Append an obligation (ResidualObligation or ProofObligation) to the result."""
        self.obligations.append(ob)

    def add_obstruction(self, ob: Any) -> None:
        """Append an Obstruction to the result."""
        self.obstructions.append(ob)

    def add_error(self, e: str) -> None:
        """Record an error string encountered during analysis."""
        self.errors.append(e)
        logger.warning("AnalysisResult error: %s", e)

    def total_findings(self) -> int:
        """Return the total count of judgments + obligations + obstructions."""
        return len(self.judgments) + len(self.obligations) + len(self.obstructions)

    def summary(self) -> str:
        """Return a multi-line human-readable summary of this result."""
        return "\n".join([
            f"AnalysisResult(plan={self.plan_id[:8]}…)",
            f"  annotations_found: {self.annotations_found}",
            f"  contracts_found:   {self.contracts_found}",
            f"  registries_found:  {self.registries_found}",
            f"  judgments:         {len(self.judgments)}",
            f"  obligations:       {len(self.obligations)}",
            f"  obstructions:      {len(self.obstructions)}",
            f"  burden_score:      {self.burden_score:.4f}",
            f"  elapsed_seconds:   {self.elapsed_seconds:.4f}",
            f"  errors:            {len(self.errors)}",
        ])

    def to_dict(self) -> dict:
        """Serialize a lightweight representation of this result to a dict."""
        return {
            "plan_id":           self.plan_id,
            "annotations_found": self.annotations_found,
            "contracts_found":   self.contracts_found,
            "registries_found":  self.registries_found,
            "judgments_count":   len(self.judgments),
            "obligations_count": len(self.obligations),
            "obstructions_count":len(self.obstructions),
            "burden_score":      self.burden_score,
            "elapsed_seconds":   self.elapsed_seconds,
            "errors":            self.errors,
        }


# ──────────────────────────────────────────────────────────────────────────────
# AnnotationGraphNode and AnnotationGraph
# ──────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True, slots=True)
class AnnotationGraphNode:
    """
    A single node in the annotation dependency graph.

    theory2.tex Ch21 models annotation dependencies as a DAG; each node
    corresponds to a (symbol, annotation) pair.  Edges represent
    "annotation A depends on type B" (e.g. List[B] depends on B).

    Fields:
        symbol_name:    the Python name carrying this annotation
        annotation_text: string representation of the annotation
        dependencies:   frozenset of annotation_text strings this depends on
        is_forward_ref: True if annotation is a string (PEP 563 forward ref)
        is_generic:     True if annotation is a parameterised generic
        depth:          nesting depth in the annotation graph
        metadata:       arbitrary extra fields
    """
    symbol_name:     str            = ""
    annotation_text: str            = ""
    dependencies:    frozenset[str] = field(default_factory=frozenset)
    is_forward_ref:  bool           = False
    is_generic:      bool           = False
    depth:           int            = 0
    metadata:        dict           = field(default_factory=dict)

    def has_dependencies(self) -> bool:
        """Return True if this node depends on other annotations."""
        return len(self.dependencies) > 0

    def is_leaf(self) -> bool:
        """Return True if this node has no dependencies (base type)."""
        return len(self.dependencies) == 0

    def summary(self) -> str:
        """Return a compact one-line summary of this node."""
        tags = []
        if self.is_forward_ref:
            tags.append("fwd")
        if self.is_generic:
            tags.append("generic")
        tag_str = f" [{','.join(tags)}]" if tags else ""
        dep_str = f" deps={len(self.dependencies)}" if self.dependencies else ""
        return f"Node({self.symbol_name}: {self.annotation_text}{tag_str}{dep_str})"


class AnnotationGraph:
    """
    Directed acyclic graph (DAG) of annotation dependencies.

    Each node is an AnnotationGraphNode keyed by symbol_name.
    Directed edges (from_sym → to_sym) represent "from_sym's annotation
    depends on to_sym's annotation".

    theory2.tex Ch21 uses the annotation graph to detect cycles (which
    indicate mutually recursive type annotations) and to compute a
    topological evaluation order for burden discharge.
    """

    def __init__(self) -> None:
        # copilot: _nodes keyed by symbol_name; _edges is adjacency list
        self._nodes: dict[str, AnnotationGraphNode] = {}
        self._edges: dict[str, set[str]] = {}

    def add_node(self, node: AnnotationGraphNode) -> None:
        """Add or replace a node in the graph."""
        self._nodes[node.symbol_name] = node
        if node.symbol_name not in self._edges:
            self._edges[node.symbol_name] = set()

    def add_edge(self, from_sym: str, to_sym: str) -> None:
        """Add a directed dependency edge from from_sym to to_sym."""
        if from_sym not in self._edges:
            self._edges[from_sym] = set()
        if to_sym not in self._edges:
            self._edges[to_sym] = set()
        self._edges[from_sym].add(to_sym)

    def topological_sort(self) -> list[str]:
        """
        Return nodes in topological order using Kahn's algorithm.

        Nodes with no incoming edges come first (base types).
        If a cycle is detected, the remaining nodes are appended in
        arbitrary order and a warning is logged.

        theory2.tex Ch21 requires topological order so that existence
        burdens for generic types are discharged bottom-up.
        """
        # copilot: build in-degree counts for Kahn's algorithm
        in_degree: dict[str, int] = {n: 0 for n in self._nodes}
        for from_sym, to_set in self._edges.items():
            for to_sym in to_set:
                if to_sym in in_degree:
                    in_degree[to_sym] += 1

        queue = [n for n, deg in in_degree.items() if deg == 0]
        result: list[str] = []

        while queue:
            node = queue.pop(0)
            result.append(node)
            for neighbour in sorted(self._edges.get(node, set())):
                in_degree[neighbour] -= 1
                if in_degree[neighbour] == 0:
                    queue.append(neighbour)

        if len(result) < len(self._nodes):
            # copilot: cycle detected; append remaining nodes unsorted
            remaining = set(self._nodes.keys()) - set(result)
            logger.warning(
                "AnnotationGraph: cycle detected; %d nodes not in topological order",
                len(remaining),
            )
            result.extend(sorted(remaining))

        return result

    def find_cycles(self) -> list[list[str]]:
        """
        Find all simple cycles in the annotation graph using DFS.

        Returns a list of cycle paths, where each path is a list of
        symbol names forming a cycle.  An empty list means the graph is
        a DAG.
        """
        # copilot: iterative DFS with coloring: white=0, grey=1, black=2
        WHITE, GREY, BLACK = 0, 1, 2
        color: dict[str, int] = {n: WHITE for n in self._nodes}
        path: list[str] = []
        cycles: list[list[str]] = []

        def dfs(node: str) -> None:
            color[node] = GREY
            path.append(node)
            for neighbour in self._edges.get(node, set()):
                if neighbour not in color:
                    continue
                if color[neighbour] == GREY:
                    # copilot: found a back edge → cycle
                    cycle_start = path.index(neighbour)
                    cycles.append(list(path[cycle_start:]))
                elif color[neighbour] == WHITE:
                    dfs(neighbour)
            path.pop()
            color[node] = BLACK

        for node in list(self._nodes):
            if color.get(node, BLACK) == WHITE:
                dfs(node)

        return cycles

    def forward_refs(self) -> list[AnnotationGraphNode]:
        """Return all nodes where is_forward_ref is True."""
        return [n for n in self._nodes.values() if n.is_forward_ref]

    def reachable_from(self, symbol: str) -> set[str]:
        """
        Return the set of all symbol names reachable from the given symbol
        via directed edges (BFS).
        """
        visited: set[str] = set()
        queue = [symbol]
        while queue:
            current = queue.pop(0)
            if current in visited:
                continue
            visited.add(current)
            for neighbour in self._edges.get(current, set()):
                if neighbour not in visited:
                    queue.append(neighbour)
        visited.discard(symbol)
        return visited

    def node_count(self) -> int:
        """Return the number of nodes in the graph."""
        return len(self._nodes)

    def edge_count(self) -> int:
        """Return the total number of directed edges."""
        return sum(len(v) for v in self._edges.values())

    def summary(self) -> str:
        """Return a human-readable summary of the graph."""
        cycles = self.find_cycles()
        fwd    = len(self.forward_refs())
        return (
            f"AnnotationGraph: nodes={self.node_count()} edges={self.edge_count()} "
            f"forward_refs={fwd} cycles={len(cycles)}"
        )


# ──────────────────────────────────────────────────────────────────────────────
# AnnotationGraphBuilder — constructs an AnnotationGraph from a Python object
# ──────────────────────────────────────────────────────────────────────────────

class AnnotationGraphBuilder:
    """
    Builds an AnnotationGraph by inspecting a Python object's type hints.

    theory2.tex Ch21 §21.3 describes the annotation graph as the site
    topology induced by the annotation functor.  The builder is the
    functor application: it maps a Python object to its annotation graph.

    Usage::

        builder = AnnotationGraphBuilder()
        graph   = builder.build(MyClass)
        order   = graph.topological_sort()
    """

    def build(self, obj: Any) -> AnnotationGraph:
        """
        Inspect obj and construct an AnnotationGraph of its annotations.

        For each annotated symbol:
          1. Determine annotation text (forward ref detection).
          2. Extract dependency set (generic arg names).
          3. Create AnnotationGraphNode and add to graph.
          4. Add dependency edges.

        Falls back to __annotations__ if typing.get_type_hints() fails.
        """
        graph = AnnotationGraph()

        try:
            hints = typing.get_type_hints(obj)
        except Exception:
            hints = getattr(obj, "__annotations__", {})

        # copilot: also inspect class methods for richer dependency info
        if inspect.isclass(obj):
            for name, member in inspect.getmembers(obj, predicate=inspect.isfunction):
                try:
                    for k, v in typing.get_type_hints(member).items():
                        hints[f"{name}.{k}"] = v
                except Exception:
                    pass

        for symbol_name, annotation in hints.items():
            ann_text = self._annotation_to_str(annotation)
            is_fwd   = self._is_forward_ref(annotation)
            is_gen   = self._is_generic(annotation)
            deps     = self._extract_deps(annotation)

            node = AnnotationGraphNode(
                symbol_name     = symbol_name,
                annotation_text = ann_text,
                dependencies    = deps,
                is_forward_ref  = is_fwd,
                is_generic      = is_gen,
                depth           = len(deps),
                metadata        = {"source": repr(obj)[:64]},
            )
            graph.add_node(node)

        # copilot: add dependency edges after all nodes exist
        for symbol_name, node in graph._nodes.items():
            for dep in node.dependencies:
                # copilot: edges go to any node whose annotation_text matches dep
                for other_name, other_node in graph._nodes.items():
                    if other_name != symbol_name and other_node.annotation_text == dep:
                        graph.add_edge(symbol_name, other_name)

        logger.debug(
            "AnnotationGraphBuilder: built graph with %d nodes for %s",
            graph.node_count(), getattr(obj, "__qualname__", repr(obj))
        )
        return graph

    def _annotation_to_str(self, annotation: Any) -> str:
        """Convert an annotation object to its string representation."""
        if isinstance(annotation, str):
            return annotation
        name = getattr(annotation, "__name__", None)
        if name:
            return name
        # copilot: handle typing generics like List[int], Optional[str]
        _name = getattr(annotation, "_name", None)
        if _name:
            args = typing.get_args(annotation)
            if args:
                arg_strs = ", ".join(self._annotation_to_str(a) for a in args)
                return f"{_name}[{arg_strs}]"
            return _name
        return repr(annotation)

    def _is_forward_ref(self, annotation: Any) -> bool:
        """Return True if the annotation is a forward reference (string or ForwardRef)."""
        if isinstance(annotation, str):
            return True
        if isinstance(annotation, typing.ForwardRef):
            return True
        return False

    def _is_generic(self, annotation: Any) -> bool:
        """Return True if the annotation is a parameterised generic type."""
        try:
            args = typing.get_args(annotation)
            return len(args) > 0
        except Exception:
            return False

    def _extract_deps(self, annotation: Any) -> frozenset[str]:
        """
        Extract the set of type names that this annotation depends on.

        For List[int], returns frozenset({"int"}).
        For Optional[str], returns frozenset({"str"}).
        For Union[int, str], returns frozenset({"int", "str"}).
        """
        deps: set[str] = set()
        try:
            args = typing.get_args(annotation)
            for arg in args:
                if arg is type(None):
                    continue
                name = getattr(arg, "__name__", None) or getattr(arg, "_name", None)
                if name:
                    deps.add(name)
                # copilot: recurse into nested generics
                sub_deps = self._extract_deps(arg)
                deps.update(sub_deps)
        except Exception:
            pass
        return frozenset(deps)


# ──────────────────────────────────────────────────────────────────────────────
# DecoratorStackAnalyzer — detects and analyses decorator stacks
# ──────────────────────────────────────────────────────────────────────────────

class DecoratorStackAnalyzer:
    """
    Detects the stack of decorators applied to a callable and models each
    decorator as a DecoratorTransformer (morphism in the coordinate category).

    theory2.tex Ch21 §21.2 proves that decorators are morphisms; this class
    implements the morphism extraction algorithm.

    The key insight: Python decorators that use @functools.wraps set
    __wrapped__ on the result, creating a linked list of wrappers.
    By following the __wrapped__ chain we can recover the full decorator stack.
    """

    def analyze(self, func: Any) -> list[DecoratorTransformer]:
        """
        Inspect func and return a list of DecoratorTransformer records.

        Traverses the __wrapped__ chain; for each (inner, outer) pair,
        creates a DecoratorTransformer with:
          - source_qualname = inner.__qualname__
          - target_qualname = outer.__qualname__
          - morphism_kind   = _morphism_kind_for_decorator(decorator_name)
        """
        transformers: list[DecoratorTransformer] = []
        decorator_names = self._detect_decorators(func)

        current = func
        depth   = 0
        while True:
            wrapped = getattr(current, "__wrapped__", None)
            if wrapped is None:
                break

            outer_qualname = getattr(current, "__qualname__", repr(current))
            inner_qualname = getattr(wrapped, "__qualname__", repr(wrapped))
            dec_name = decorator_names[depth] if depth < len(decorator_names) else "unknown"
            kind     = self._morphism_kind_for_decorator(dec_name)

            transformer = DecoratorTransformer(
                decorator_name  = dec_name,
                source_qualname = inner_qualname,
                target_qualname = outer_qualname,
                morphism_kind   = kind,
            )
            transformers.append(transformer)
            current = wrapped
            depth  += 1
            if depth > 32:  # copilot: guard against infinite __wrapped__ cycles
                break

        # copilot: if no __wrapped__ chain found, try closure inspection
        if not transformers:
            transformers = self._analyze_via_closure(func)

        logger.debug(
            "DecoratorStackAnalyzer: found %d transformers for %s",
            len(transformers), getattr(func, "__qualname__", repr(func))
        )
        return transformers

    def _detect_decorators(self, func: Any) -> list[str]:
        """
        Heuristically detect decorator names applied to func.

        Traverses the __wrapped__ chain and inspects closure variables to
        find references to known decorators.  Returns decorator names in
        application order (outermost first).
        """
        names: list[str] = []
        current = func

        while True:
            # copilot: check if this is a known wrapper type
            for known_name in _KNOWN_DECORATORS:
                simple_name = known_name.split(".")[-1]
                if simple_name in getattr(current, "__qualname__", ""):
                    if known_name not in names:
                        names.append(known_name)

            try:
                cv = inspect.getclosurevars(current)
                for name, val in cv.nonlocals.items():
                    if callable(val) and name in _KNOWN_DECORATORS:
                        if name not in names:
                            names.append(name)
                for name, val in cv.globals.items():
                    if callable(val) and name in _KNOWN_DECORATORS:
                        if name not in names:
                            names.append(name)
            except (TypeError, AttributeError):
                pass

            wrapped = getattr(current, "__wrapped__", None)
            if wrapped is None:
                break
            current = wrapped

        return names

    def _analyze_via_closure(self, func: Any) -> list[DecoratorTransformer]:
        """
        Fallback decorator analysis when __wrapped__ chain is absent.

        Inspects closure variables for callable objects that match known
        decorator patterns.  Creates a single synthetic transformer if any
        are found.
        """
        transformers: list[DecoratorTransformer] = []
        try:
            cv = inspect.getclosurevars(func)
            for name, val in {**cv.nonlocals, **cv.globals}.items():
                if callable(val) and name in _KNOWN_DECORATORS:
                    kind = self._morphism_kind_for_decorator(name)
                    qualname = getattr(func, "__qualname__", repr(func))
                    transformer = DecoratorTransformer(
                        decorator_name  = name,
                        source_qualname = qualname,
                        target_qualname = f"{name}({qualname})",
                        morphism_kind   = kind,
                    )
                    transformers.append(transformer)
        except (TypeError, AttributeError):
            pass
        return transformers

    def compose_morphisms(
        self, transformers: list[DecoratorTransformer]
    ) -> DecoratorTransformer:
        """
        Compose a list of DecoratorTransformers into a single composed transformer.

        theory2.tex Ch21 §21.2 states that decorator composition is
        associative: (D_n ∘ … ∘ D_1)(S) = D_n(D_{n-1}(…D_1(S)…)).
        The composed transformer has:
          - source_qualname = first.source_qualname
          - target_qualname = last.target_qualname
          - morphism_kind   = "REFINEMENT" (composition of morphisms is a refinement)
          - decorator_name  = "composed"
        """
        if not transformers:
            return DecoratorTransformer(
                decorator_name  = "identity",
                source_qualname = "",
                target_qualname = "",
                morphism_kind   = "INCLUSION",
            )
        if len(transformers) == 1:
            return transformers[0]

        # copilot: composition: chain source of first to target of last
        source = transformers[0].source_qualname
        target = transformers[-1].target_qualname
        names  = " ∘ ".join(t.decorator_name for t in reversed(transformers))

        return DecoratorTransformer(
            decorator_name  = f"composed({names})",
            source_qualname = source,
            target_qualname = target,
            morphism_kind   = "REFINEMENT",
        )

    def _morphism_kind_for_decorator(self, name: str) -> str:
        """
        Map a decorator name to its MorphismKind string.

        Uses _KNOWN_DECORATORS for well-known decorators; falls back to
        "REFINEMENT" for unrecognised decorators (conservative choice).
        """
        # copilot: exact match first, then suffix match
        if name in _KNOWN_DECORATORS:
            return _KNOWN_DECORATORS[name]
        for known, kind in _KNOWN_DECORATORS.items():
            if name.endswith(known.split(".")[-1]):
                return kind
        return "REFINEMENT"


# ──────────────────────────────────────────────────────────────────────────────
# Planner
# ──────────────────────────────────────────────────────────────────────────────

class AnnotationsDecoratorsRegistriesGeneratedPlanner:
    """
    Creates AnalysisPlans for sets of analysis targets.

    theory2.tex Ch21 §21.6 describes the planner as the initial object in
    the analysis category: all analysis runs begin with a plan.

    The planner estimates the number of obligations (3 per annotation ×
    annotation count), assigns a default phase sequence, and can prioritise
    targets by trust deficit.
    """

    def __init__(self) -> None:
        # copilot: _plans stores all plans created by this planner instance
        self._plans: dict[str, AnalysisPlan] = {}

    def create_plan(
        self,
        targets: list[str],
        trust_ceiling: Any = None,
        phases: list[str] | None = None,
    ) -> AnalysisPlan:
        """
        Create and register an AnalysisPlan.

        Defaults:
          - trust_ceiling = _DEFAULT_TRUST_CEILING (SOLVER_DISCHARGED)
          - phases        = _ANALYSIS_PHASES

        estimated_obligations is computed as len(targets) × 3 as a
        conservative lower bound (existence + consistency + completeness
        per annotated parameter).
        """
        plan = AnalysisPlan(
            plan_id               = str(uuid.uuid4()),
            targets               = tuple(targets),
            phases                = tuple(phases if phases is not None else _ANALYSIS_PHASES),
            estimated_obligations = len(targets) * _ANNOTATION_ESTIMATE_PER_ARG,
            trust_ceiling         = trust_ceiling or _DEFAULT_TRUST_CEILING,
            created_at            = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        )
        self._plans[plan.plan_id] = plan
        logger.info("Planner: created plan %s for %d targets", plan.plan_id[:8], len(targets))
        return plan

    def estimate_work(self, target: Any) -> int:
        """
        Estimate the total analysis work for a single target object.

        Counts annotations (via __annotations__) and methods (via
        inspect.getmembers) to produce a rough obligation count.
        """
        ann_count = 0
        method_count = 0

        try:
            ann_count = len(getattr(target, "__annotations__", {}))
        except Exception:
            pass

        if inspect.isclass(target):
            try:
                method_count = sum(
                    1 for _, m in inspect.getmembers(target, predicate=inspect.isfunction)
                )
                for _, m in inspect.getmembers(target, predicate=inspect.isfunction):
                    ann_count += len(getattr(m, "__annotations__", {}))
            except Exception:
                pass
        elif callable(target):
            try:
                ann_count += len(getattr(target, "__annotations__", {}))
            except Exception:
                pass

        # copilot: 3 obligations per annotation + base registry overhead
        return ann_count * _ANNOTATION_ESTIMATE_PER_ARG + _REGISTRY_ESTIMATE_BASE + method_count

    def prioritize_by_trust_deficit(self, targets: list[Any]) -> list[Any]:
        """
        Sort targets by their estimated trust deficit (descending).

        Trust deficit = unverified_annotation_fraction = (annotations
        without a known trusted checker) / total annotations.  Targets
        with the highest deficit (most unverified annotations) come first
        so they are analysed with highest priority.
        """
        def trust_deficit(t: Any) -> float:
            try:
                hints = typing.get_type_hints(t)
            except Exception:
                hints = getattr(t, "__annotations__", {})
            if not hints:
                return 0.0
            # copilot: heuristic: forward refs (string annotations) are unverified
            unverified = sum(1 for v in hints.values() if isinstance(v, str))
            return unverified / max(1, len(hints))

        return sorted(targets, key=trust_deficit, reverse=True)

    def list_plans(self) -> list[AnalysisPlan]:
        """Return all plans created by this planner, most recent first."""
        return sorted(
            self._plans.values(),
            key=lambda p: p.created_at,
            reverse=True,
        )

    def get_plan(self, plan_id: str) -> AnalysisPlan | None:
        """Return the plan with the given ID, or None if not found."""
        return self._plans.get(plan_id)


# ──────────────────────────────────────────────────────────────────────────────
# Executor
# ──────────────────────────────────────────────────────────────────────────────

class AnnotationsDecoratorsRegistriesGeneratedExecutor:
    """
    Executes an AnalysisPlan against a dict of concrete Python objects.

    theory2.tex Ch21 §21.6 defines the executor as the morphism application
    functor: it takes an AnalysisPlan and a dict of named objects and
    produces an AnalysisResult by running each analysis phase in sequence.

    Phases:
        annotations — build annotation graph; enumerate annotation records
        decorators  — detect decorator stacks; create DecoratorTransformers
        registries  — detect singledispatch/ABCMeta registries
        contracts   — find frozen dataclasses (contract records)
        burden      — compute theorem burden for all targets
    """

    def __init__(self) -> None:
        # copilot: one graph builder and one decorator analyzer per executor
        self._results: dict[str, AnalysisResult] = {}
        self._graph_builder = AnnotationGraphBuilder()
        self._decorator_analyzer = DecoratorStackAnalyzer()

    def execute(
        self, plan: AnalysisPlan, target_objects: dict[str, Any]
    ) -> AnalysisResult:
        """
        Execute all phases of the plan against the provided targets.

        For each (name, obj) in target_objects:
          - Runs each phase listed in plan.phases
          - Appends findings to the AnalysisResult
          - Handles per-phase errors gracefully

        Times the execution and stores elapsed_seconds in the result.
        """
        result = AnalysisResult(plan_id=plan.plan_id)
        start  = time.monotonic()

        logger.info(
            "Executor: starting plan %s with %d phases, %d targets",
            plan.plan_id[:8], len(plan.phases), len(target_objects)
        )

        for phase in plan.phases:
            logger.debug("Executor: phase=%s", phase)
            for name, obj in target_objects.items():
                try:
                    if phase == "annotations":
                        self._execute_annotations_phase(obj, result)
                    elif phase == "decorators":
                        self._execute_decorators_phase(obj, result)
                    elif phase == "registries":
                        self._execute_registries_phase(obj, result)
                    elif phase == "contracts":
                        self._execute_contracts_phase(obj, result)
                    elif phase == "burden":
                        self._execute_burden_phase(obj, result)
                    else:
                        logger.warning("Executor: unknown phase %s", phase)
                except Exception as exc:
                    err = f"phase={phase} target={name}: {type(exc).__name__}: {exc}"
                    result.add_error(err)
                    logger.warning("Executor error: %s", err)

        result.elapsed_seconds = time.monotonic() - start

        # copilot: compute burden_score from obligation ratios
        total      = len(result.obligations)
        discharged = sum(
            1 for ob in result.obligations
            if getattr(ob, "is_discharged", False)
        )
        result.burden_score = discharged / max(1, total)

        self._results[plan.plan_id] = result
        logger.info(
            "Executor: plan %s complete in %.3fs, findings=%d",
            plan.plan_id[:8], result.elapsed_seconds, result.total_findings()
        )
        return result

    def _execute_annotations_phase(self, target: Any, result: AnalysisResult) -> None:
        """
        Annotations phase: build annotation graph and enumerate annotations.

        For each annotation found:
          - Creates an AnnotationContract and appends to result.obligations
          - Emits a Judgment with BEHAVIORAL proposition
          - Increments annotations_found
        """
        graph = self._graph_builder.build(target)

        try:
            hints = typing.get_type_hints(target)
        except Exception:
            hints = getattr(target, "__annotations__", {})

        # copilot: also gather method annotations for classes
        if inspect.isclass(target):
            for _, member in inspect.getmembers(target, predicate=inspect.isfunction):
                try:
                    for k, v in typing.get_type_hints(member).items():
                        if k not in hints:
                            hints[k] = v
                except Exception:
                    pass

        for symbol_name, annotation in hints.items():
            ann_text = (
                annotation if isinstance(annotation, str)
                else getattr(annotation, "__name__", repr(annotation))
            )

            # copilot: create an AnnotationContract as the obligation record
            contract = AnnotationContract(
                symbol_name     = symbol_name,
                annotation_text = ann_text,
                trust_level     = TrustLevel.UNVERIFIED,
                is_discharged   = ann_text in {"int", "str", "float", "bool", "bytes"},
            )
            result.add_obligation(contract)

            # copilot: emit a Judgment for every annotation found
            prop = Proposition(
                kind           = PropositionKind.BEHAVIORAL,
                formula        = f"{symbol_name}: {ann_text}",
                free_variables = (symbol_name,),
            )
            j = Judgment(
                proposition = prop,
                carrier     = Carrier(name=symbol_name),
                evidence    = EvidenceBundle(),
                trust       = TrustAnnotation(level=TrustLevel.UNVERIFIED),
                provenance  = Provenance(sources=(ProvenanceSource.ORACLE,)),
            )
            result.add_judgment(j)
            result.annotations_found += 1

        logger.debug(
            "annotations phase: found %d annotations in %s (graph=%s)",
            result.annotations_found,
            getattr(target, "__qualname__", repr(target)),
            graph.summary(),
        )

    def _execute_decorators_phase(self, target: Any, result: AnalysisResult) -> None:
        """
        Decorators phase: detect decorator stacks on callables.

        For each method/function in the target:
          - Runs DecoratorStackAnalyzer.analyze()
          - Appends each DecoratorTransformer as an obligation
          - Emits a Judgment for each transformer
        """
        callables: list[tuple[str, Any]] = []

        if inspect.isclass(target):
            for name, member in inspect.getmembers(target, predicate=inspect.isfunction):
                callables.append((name, member))
            for name, member in inspect.getmembers(target, predicate=inspect.ismethod):
                callables.append((name, member))
        elif callable(target):
            callables.append((getattr(target, "__qualname__", repr(target)), target))

        for name, func in callables:
            transformers = self._decorator_analyzer.analyze(func)
            for t in transformers:
                result.add_obligation(t)
                prop = Proposition(
                    kind    = PropositionKind.STRUCTURAL,
                    formula = f"decorator_morphism({t.source_qualname} → {t.target_qualname})",
                )
                j = Judgment(
                    proposition = prop,
                    carrier     = Carrier(name=name),
                    trust       = TrustAnnotation(level=TrustLevel.ORACLE_PROPOSED),
                    provenance  = Provenance(sources=(ProvenanceSource.ORACLE,)),
                )
                result.add_judgment(j)

    def _execute_registries_phase(self, target: Any, result: AnalysisResult) -> None:
        """
        Registries phase: detect singledispatch and ABCMeta registries.

        Checks for __abstractmethods__, singledispatch registry attributes,
        and MRO length as a proxy for registry coverage.
        """
        registry_found = False

        # copilot: detect ABCMeta abstract method registries
        if inspect.isclass(target):
            abstract_methods = getattr(target, "__abstractmethods__", frozenset())
            if abstract_methods:
                registry_found = True
                section = RegistrySection(
                    registry_name = f"{getattr(target, '__qualname__', '?')}.ABCMeta",
                    entries       = tuple(abstract_methods),
                    is_covering   = False,
                )
                result.add_obligation(section)
                result.registries_found += 1

            # copilot: check for singledispatch implementations
            for _, member in inspect.getmembers(target):
                if hasattr(member, "registry") and hasattr(member, "dispatch"):
                    registry_found = True
                    dispatch_types = list(getattr(member, "registry", {}).keys())
                    section = RegistrySection(
                        registry_name = f"{getattr(member, '__qualname__', '?')}.singledispatch",
                        entries       = tuple(str(t) for t in dispatch_types),
                        is_covering   = len(dispatch_types) > 1,
                    )
                    result.add_obligation(section)
                    result.registries_found += 1

        # copilot: detect module-level singledispatch functions
        if inspect.ismodule(target):
            for name, member in inspect.getmembers(target):
                if hasattr(member, "registry") and hasattr(member, "dispatch"):
                    registry_found = True
                    dispatch_types = list(getattr(member, "registry", {}).keys())
                    section = RegistrySection(
                        registry_name = f"{name}.singledispatch",
                        entries       = tuple(str(t) for t in dispatch_types),
                        is_covering   = len(dispatch_types) > 1,
                    )
                    result.add_obligation(section)
                    result.registries_found += 1

        if not registry_found:
            logger.debug(
                "registries phase: no registries found in %s",
                getattr(target, "__qualname__", repr(target))
            )

    def _execute_contracts_phase(self, target: Any, result: AnalysisResult) -> None:
        """
        Contracts phase: detect frozen dataclasses and record them as ContractRecords.

        A frozen dataclass is a generated contract: its fields are the
        annotation obligations and the __init__ enforces the contract at
        construction time (§21.4).
        """
        targets_to_check: list[Any] = []

        if inspect.isclass(target):
            targets_to_check.append(target)
            # copilot: also check nested classes
            for _, member in inspect.getmembers(target, predicate=inspect.isclass):
                targets_to_check.append(member)
        elif inspect.ismodule(target):
            for _, member in inspect.getmembers(target, predicate=inspect.isclass):
                targets_to_check.append(member)
        else:
            targets_to_check.append(type(target))

        for cls in targets_to_check:
            params = getattr(cls, "__dataclass_params__", None)
            if params is None:
                continue

            frozen = getattr(params, "frozen", False)
            fields = getattr(cls, "__dataclass_fields__", {})
            qualname = getattr(cls, "__qualname__", repr(cls))
            key = f"{getattr(cls, '__module__', '')}.{qualname}"

            contracts: list[AnnotationContract] = []
            for field_name, field_info in fields.items():
                ann_text = repr(field_info.type) if field_info.type is not None else "Any"
                contracts.append(AnnotationContract(
                    symbol_name     = f"{qualname}.{field_name}",
                    annotation_text = ann_text,
                    trust_level     = TrustLevel.RUNTIME_WITNESSED if frozen else TrustLevel.UNVERIFIED,
                    is_discharged   = frozen,
                ))

            record = ContractRecord(
                coordinate_key = key,
                contracts      = tuple(contracts),
                is_complete    = frozen and len(contracts) > 0,
            )
            result.add_obligation(record)
            result.contracts_found += 1
            logger.debug("contracts phase: found dataclass %s (frozen=%s)", qualname, frozen)

    def _execute_burden_phase(self, target: Any, result: AnalysisResult) -> None:
        """
        Burden phase: compute theorem burden for the target.

        Creates ProofObligation-like records (using AnnotationContract as
        proxy) for each annotation and appends a summary Judgment.  Updates
        result.burden_score with the fraction of discharged obligations.
        """
        try:
            hints = typing.get_type_hints(target)
        except Exception:
            hints = getattr(target, "__annotations__", {})

        if inspect.isclass(target):
            for _, member in inspect.getmembers(target, predicate=inspect.isfunction):
                try:
                    for k, v in typing.get_type_hints(member).items():
                        if k not in hints:
                            hints[k] = v
                except Exception:
                    pass

        trivial_set = {"int", "str", "float", "bool", "bytes", "None"}
        discharged = 0

        for symbol_name, annotation in hints.items():
            ann_text = (
                annotation if isinstance(annotation, str)
                else getattr(annotation, "__name__", repr(annotation))
            )
            is_discharged = ann_text in trivial_set
            if is_discharged:
                discharged += 1

            ob = AnnotationContract(
                symbol_name     = f"burden.{symbol_name}",
                annotation_text = ann_text,
                trust_level     = TrustLevel.RUNTIME_WITNESSED if is_discharged else TrustLevel.UNVERIFIED,
                is_discharged   = is_discharged,
            )
            result.add_obligation(ob)

        if hints:
            new_score = discharged / len(hints)
            # copilot: merge scores using weighted average with existing
            result.burden_score = (result.burden_score + new_score) / 2

        prop = Proposition(
            kind    = PropositionKind.SEMANTIC,
            formula = f"burden_analysis({getattr(target, '__qualname__', repr(target))})",
        )
        j = Judgment(
            proposition = prop,
            carrier     = Carrier(name=getattr(target, "__qualname__", repr(target))),
            trust       = TrustAnnotation(level=TrustLevel.ORACLE_PROPOSED),
            provenance  = Provenance(sources=(ProvenanceSource.ORACLE,)),
        )
        result.add_judgment(j)

    def get_result(self, plan_id: str) -> AnalysisResult | None:
        """Return the AnalysisResult for the given plan_id, or None."""
        return self._results.get(plan_id)


# ──────────────────────────────────────────────────────────────────────────────
# Normalizer
# ──────────────────────────────────────────────────────────────────────────────

class AnnotationsDecoratorsRegistriesGeneratedNormalizer:
    """
    Post-processes an AnalysisResult to produce a canonical, deduplicated form.

    theory2.tex Ch21 §21.6 defines normalisation as the co-equalizer step:
    multiple analysis passes may produce overlapping judgments and obligations;
    the normalizer collapses these to a canonical set.

    Key normalisation operations:
      - Deduplicate judgments by proposition.formula
      - Merge obligations with the same description, keeping highest trust
      - Recompute burden_score from deduplicated obligations
      - Compute canonical (mode) trust level across all judgments
    """

    def normalize(self, result: AnalysisResult) -> AnalysisResult:
        """
        Return a new AnalysisResult with deduplicated, canonicalised content.

        Does not mutate the input; creates a fresh AnalysisResult with
        cleaned-up judgments, obligations, and recalculated burden_score.
        """
        clean = AnalysisResult(plan_id=result.plan_id)

        clean.judgments    = self._deduplicate_judgments(result.judgments)
        clean.obligations  = self._merge_obligations(result.obligations)
        clean.obstructions = list(result.obstructions)
        clean.errors       = list(result.errors)

        clean.annotations_found = result.annotations_found
        clean.contracts_found   = result.contracts_found
        clean.registries_found  = result.registries_found
        clean.elapsed_seconds   = result.elapsed_seconds
        clean.burden_score      = self._recompute_burden_score(clean)

        logger.debug(
            "Normalizer: reduced %d→%d judgments, %d→%d obligations",
            len(result.judgments), len(clean.judgments),
            len(result.obligations), len(clean.obligations),
        )
        return clean

    def _deduplicate_judgments(self, judgments: list) -> list:
        """
        Remove duplicate judgments by proposition.formula.

        When duplicates exist, the one with the highest trust level is kept.
        """
        seen: dict[str, Any] = {}
        for j in judgments:
            formula = ""
            try:
                formula = j.proposition.formula if j.proposition else repr(j)
            except AttributeError:
                formula = repr(j)

            if formula not in seen:
                seen[formula] = j
            else:
                # copilot: keep the higher-trust judgment
                existing_trust = getattr(getattr(seen[formula], "trust", None), "level", None)
                new_trust      = getattr(getattr(j, "trust", None), "level", None)
                if new_trust is not None and existing_trust is not None:
                    try:
                        if new_trust > existing_trust:
                            seen[formula] = j
                    except TypeError:
                        pass

        return list(seen.values())

    def _merge_obligations(self, obligations: list) -> list:
        """
        Merge obligations with identical descriptions/symbol names.

        For each group of obligations with the same key, keeps the one
        with the highest trust level (or the discharged one if any is discharged).
        """
        seen: dict[str, Any] = {}

        for ob in obligations:
            # copilot: compute merge key from available attributes
            if hasattr(ob, "symbol_name") and hasattr(ob, "annotation_text"):
                key = f"{ob.symbol_name}|{ob.annotation_text}"
            elif hasattr(ob, "description"):
                key = ob.description
            elif hasattr(ob, "coordinate_key"):
                key = ob.coordinate_key
            elif hasattr(ob, "registry_name"):
                key = ob.registry_name
            elif hasattr(ob, "source_qualname"):
                key = f"{ob.source_qualname}|{ob.target_qualname}"
            else:
                key = repr(ob)

            if key not in seen:
                seen[key] = ob
            else:
                existing = seen[key]
                # copilot: prefer discharged obligations; otherwise prefer higher trust
                ex_discharged  = getattr(existing, "is_discharged", False)
                new_discharged = getattr(ob, "is_discharged", False)
                if new_discharged and not ex_discharged:
                    seen[key] = ob
                elif not ex_discharged and not new_discharged:
                    ex_trust  = getattr(existing, "trust_level", None)
                    new_trust = getattr(ob, "trust_level", None)
                    try:
                        if new_trust is not None and ex_trust is not None and new_trust > ex_trust:
                            seen[key] = ob
                    except TypeError:
                        pass

        return list(seen.values())

    def _compute_canonical_trust(self, judgments: list) -> Any:
        """
        Return the mode trust level across all judgments.

        'Mode' is the most frequently occurring trust level.  If all
        judgments are at the same trust level, returns that level.
        If no judgments exist, returns TrustLevel.UNVERIFIED.
        """
        if not judgments:
            return TrustLevel.UNVERIFIED

        trust_counts: dict[Any, int] = {}
        for j in judgments:
            level = getattr(getattr(j, "trust", None), "level", None)
            if level is not None:
                trust_counts[level] = trust_counts.get(level, 0) + 1

        if not trust_counts:
            return TrustLevel.UNVERIFIED

        return max(trust_counts, key=trust_counts.__getitem__)

    def _recompute_burden_score(self, result: AnalysisResult) -> float:
        """
        Recompute burden_score as discharged / total obligations.

        Returns 0.0 if there are no obligations (conservative baseline).
        """
        total = len(result.obligations)
        if total == 0:
            return 0.0
        discharged = sum(
            1 for ob in result.obligations
            if getattr(ob, "is_discharged", False)
        )
        return discharged / total

    def generate_report(self, result: AnalysisResult) -> str:
        """
        Generate a clean multi-line report from a normalised AnalysisResult.

        Includes per-section counts, burden_score, and a summary of errors.
        """
        canonical_trust = self._compute_canonical_trust(result.judgments)
        lines = [
            "═" * 60,
            "  AnnotationsDecoratorsRegistriesGenerated Analysis Report",
            "═" * 60,
            f"  Plan ID:          {result.plan_id[:16]}…",
            f"  Elapsed:          {result.elapsed_seconds:.4f}s",
            f"  Annotations:      {result.annotations_found}",
            f"  Contracts:        {result.contracts_found}",
            f"  Registries:       {result.registries_found}",
            f"  Judgments:        {len(result.judgments)}",
            f"  Obligations:      {len(result.obligations)}",
            f"  Obstructions:     {len(result.obstructions)}",
            f"  Burden Score:     {result.burden_score:.4f}",
            f"  Canonical Trust:  {canonical_trust}",
            f"  Errors:           {len(result.errors)}",
        ]
        if result.errors:
            lines.append("")
            lines.append("  Errors:")
            for err in result.errors[:10]:  # copilot: cap error output at 10
                lines.append(f"    - {err}")
        lines.append("═" * 60)
        return "\n".join(lines)


# ──────────────────────────────────────────────────────────────────────────────
# Smoke test
# ──────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    print(f"[smoke] {__file__}")
    try:
        def sample(x: int, y: str = "hi") -> bool:
            """A simple annotated function used as a smoke-test target."""
            return True

        # copilot: test Planner → Executor → Normalizer pipeline
        planner = AnnotationsDecoratorsRegistriesGeneratedPlanner()
        plan    = planner.create_plan(["sample_func"])
        assert plan.plan_id != "", "plan_id should not be empty"
        assert plan.phase_count() == len(_ANALYSIS_PHASES)

        executor = AnnotationsDecoratorsRegistriesGeneratedExecutor()
        result   = executor.execute(plan, {"sample_func": sample})
        assert result.elapsed_seconds >= 0.0, "elapsed_seconds should be non-negative"
        assert result.plan_id == plan.plan_id

        normalizer = AnnotationsDecoratorsRegistriesGeneratedNormalizer()
        clean      = normalizer.normalize(result)
        report     = normalizer.generate_report(clean)

        assert isinstance(report, str) and len(report) > 0

        # copilot: test AnnotationGraph construction
        builder = AnnotationGraphBuilder()
        graph   = builder.build(sample)
        assert graph.node_count() >= 0
        order = graph.topological_sort()
        assert isinstance(order, list)

        # copilot: test cycle detection on a simple graph
        g = AnnotationGraph()
        from dataclasses import dataclass as _dc
        n1 = AnnotationGraphNode(symbol_name="a", annotation_text="int")
        n2 = AnnotationGraphNode(symbol_name="b", annotation_text="str")
        g.add_node(n1); g.add_node(n2)
        g.add_edge("a", "b")
        cycles = g.find_cycles()
        assert cycles == [], f"Unexpected cycle in simple graph: {cycles}"

        # copilot: test planner prioritisation
        prioritised = planner.prioritize_by_trust_deficit([sample, int, str])
        assert isinstance(prioritised, list)

        print(f"[smoke] findings={clean.total_findings()}")
        print("[smoke] PASS")
    except Exception as exc:
        import traceback
        traceback.print_exc()
        print(f"[smoke] FAIL: {exc}", file=sys.stderr)
        sys.exit(1)
