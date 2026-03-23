"""Implementation consequences — what the pipeline implies for code (S04-IC).

When the obstruction-to-kind pipeline proposes a new mathematical kind, it
does not do so in a vacuum.  Every new kind has *implementation consequences*:
new types to define, new functions to write, existing interfaces to refactor,
and new axioms or lemmas to verify.  This module formalises those consequences
and provides tools for prioritising, graphing, and explaining them.

# copilot: implementation_consequences — models architectural and runtime
# consequences of kind discovery, enforces kind-discovery policies, checks
# kind constraints, and validates system architecture.

Module layout::

    ┌─────────────────────────────────────────────────────────────────┐
    │  jugeo.ideation.kind_discovery.implementation_consequences  │
    ├─────────────────────────────────────────────────────────────────┤
    │  Helpers                                                        │
    │    _clamp              clamp float to [lo, hi]                 │
    │    _now_iso            UTC ISO-8601 timestamp                  │
    │    _consequence_id     fresh UUID for a consequence            │
    │    _effort_estimate    estimate implementation effort          │
    ├─────────────────────────────────────────────────────────────────┤
    │  Enums                                                          │
    │    ConsequenceType     taxonomy of implementation consequences  │
    ├─────────────────────────────────────────────────────────────────┤
    │  Value objects (frozen dataclasses)                             │
    │    ImplementationConsequenceConfig   hyper-parameters           │
    │    ImplementationConsequence         one concrete consequence   │
    ├─────────────────────────────────────────────────────────────────┤
    │  Mutable container                                              │
    │    ConsequenceGraph    dependency graph over consequences       │
    ├─────────────────────────────────────────────────────────────────┤
    │  Stateful services                                              │
    │    ImplementationConsequencesAnalyzer   derives consequences   │
    │    ImplementationConsequencesWitness    records consequences    │
    │    ImplementationConsequencesCoordinator orchestrator          │
    └─────────────────────────────────────────────────────────────────┘

Domain motivation
─────────────────
A new mathematical kind is not just a theoretical construct — it is a
*commitment* to a particular slice of the implementation roadmap.  Introducing
a kind ``K`` might require:

  1. A new type declaration ``data K a = ...`` (ConsequenceType.NEW_TYPE).
  2. New smart constructors ``mkK :: ... -> K a`` (ConsequenceType.NEW_FUNCTION).
  3. Refactoring existing code that previously encoded K's behaviour ad hoc
     (ConsequenceType.REFACTOR).
  4. A new algebraic axiom asserting K's laws (ConsequenceType.NEW_AXIOM).
  5. Lemmas proving that K's laws are consistent (ConsequenceType.NEW_LEMMA).
  6. Deprecation of the ad-hoc workarounds K supersedes
     (ConsequenceType.DEPRECATION).
  7. Changes to public interfaces that consume K (ConsequenceType.INTERFACE_CHANGE).
  8. Addition of a library dependency that provides K's primitives
     (ConsequenceType.DEPENDENCY_ADDITION).

These consequences form a *directed acyclic graph*: a new axiom cannot be
stated until the type it speaks about exists; a deprecation cannot happen
until the replacement function is written; and so on.  The
:class:`ConsequenceGraph` models these edges and provides a topological
ordering that respects the dependency constraints.

Priority and effort
───────────────────
Each :class:`ImplementationConsequence` carries a ``priority`` score in
[0.0, 1.0] and an ``estimated_effort`` value in abstract "story points".
The priority is computed from the composite score of the parent hypothesis
and the consequence type (axioms and types rank higher than deprecations).
The effort is estimated by :func:`_effort_estimate` as a function of
abstraction complexity and novelty.

Code sketches
─────────────
The analyzer can generate short code sketches for each consequence — Haskell-
ish pseudocode illustrating how the consequence might be implemented.  These
sketches are stored in the ``code_sketch`` field of
:class:`ImplementationConsequence` and are useful for documentation and
planning purposes.

Integration
───────────
The records produced here are the final output of the kind-discovery pipeline.
They are consumed by higher-level planning tools (outside this package) that
schedule implementation work on the jugeo roadmap.
"""

from __future__ import annotations

import datetime
import enum
import uuid
from dataclasses import dataclass
from typing import Any

# ---------------------------------------------------------------------------
# Cross-package imports (guarded)
# ---------------------------------------------------------------------------

try:
    from jugeo.ideation.kind_discovery.models import (
        KindCandidate,
        NewKind,
        KindStatus,
        KindBootstrapPlan,
    )
except ImportError:
    KindCandidate = None  # type: ignore[assignment,misc]
    NewKind = None  # type: ignore[assignment,misc]
    KindStatus = None  # type: ignore[assignment,misc]
    KindBootstrapPlan = None  # type: ignore[assignment,misc]

try:
    from jugeo.ideation.kind_discovery.candidate_new_mathematical_kinds_e import (
        KindHypothesis,
        TypeConstructorProposal,
        AbstractionLevel,
    )
except ImportError:
    KindHypothesis = None  # type: ignore[assignment,misc]
    TypeConstructorProposal = None  # type: ignore[assignment,misc]
    AbstractionLevel = None  # type: ignore[assignment,misc]

try:
    from jugeo.ideation.kind_discovery.the_obstruction_to_kind_pipeline_c import (
        PipelineRun,
        PipelineStage,
    )
except ImportError:
    PipelineRun = None  # type: ignore[assignment,misc]
    PipelineStage = None  # type: ignore[assignment,misc]

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

#: Base effort multiplier for ELEMENTARY abstraction level.
EFFORT_BASE_ELEMENTARY: float = 1.0

#: Base effort multiplier for INTERMEDIATE abstraction level.
EFFORT_BASE_INTERMEDIATE: float = 2.0

#: Base effort multiplier for ADVANCED abstraction level.
EFFORT_BASE_ADVANCED: float = 4.0

#: Base effort multiplier for FOUNDATIONAL abstraction level.
EFFORT_BASE_FOUNDATIONAL: float = 8.0

#: Default priority assigned when no parent hypothesis score is available.
DEFAULT_PRIORITY: float = 0.5

#: Priority bonus applied to axiom and type consequences.
HIGH_IMPORTANCE_BONUS: float = 0.15

#: Priority penalty applied to deprecation and dependency consequences.
LOW_IMPORTANCE_PENALTY: float = 0.1

#: Maximum number of lines in a generated code sketch.
MAX_SKETCH_LINES: int = 20

#: Header comment used at the top of every generated code sketch.
SKETCH_HEADER: str = "-- AUTO-GENERATED SKETCH (jugeo kind-discovery pipeline)"

#: Topological sort sentinel for cycle detection.
_TOPO_GREY: int = 1
_TOPO_BLACK: int = 2

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _clamp(v: float, lo: float, hi: float) -> float:
    """Return *v* clamped to the closed interval [*lo*, *hi*].

    >>> _clamp(2.0, 0.0, 1.0)
    1.0
    >>> _clamp(-0.5, 0.0, 1.0)
    0.0
    >>> _clamp(0.42, 0.0, 1.0)
    0.42
    """
    return max(lo, min(hi, v))


def _now_iso() -> str:
    """Return the current UTC instant in ISO-8601 format.

    Example: ``"2024-06-01T09:00:00Z"``
    """
    return datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z"


def _consequence_id() -> str:
    """Generate a unique implementation-consequence identifier.

    Format: ``"cons-<8 hex chars>"``

    Returns
    -------
    str
        A fresh identifier string.
    """
    return "cons-" + uuid.uuid4().hex[:8]


def _effort_estimate(complexity: float, novelty: float) -> float:
    """Estimate implementation effort in abstract story points.

    The estimate is based on a simple multiplicative model:

        effort = base * (1 + novelty) * complexity_factor

    where ``base = 2.0`` and ``complexity_factor = 1 + complexity``.

    Parameters
    ----------
    complexity:
        A float in [0.0, 1.0] representing how complex the implementation is.
        0.0 = trivial, 1.0 = maximally complex.
    novelty:
        A float in [0.0, 1.0] representing how novel the work is.
        Novel work takes longer because there are no existing patterns to
        copy from.

    Returns
    -------
    float
        Estimated effort in story points, rounded to one decimal place.

    Examples
    --------
    >>> _effort_estimate(0.0, 0.0)
    2.0
    >>> _effort_estimate(1.0, 1.0)
    8.0
    """
    base = 2.0
    return round(base * (1.0 + _clamp(novelty, 0.0, 1.0)) * (1.0 + _clamp(complexity, 0.0, 1.0)), 1)


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class ConsequenceType(str, enum.Enum):
    """Taxonomy of implementation consequences.

    Attributes
    ----------
    NEW_TYPE:
        A new algebraic data type or type alias must be introduced.
    NEW_FUNCTION:
        A new function or smart constructor must be written.
    REFACTOR:
        Existing code must be restructured to use the new kind.
    NEW_AXIOM:
        A new algebraic axiom must be stated (in a proof assistant or
        as a QuickCheck property).
    NEW_LEMMA:
        A derived lemma must be proved.
    DEPRECATION:
        An existing entity becomes obsolete and should be removed.
    INTERFACE_CHANGE:
        A public module interface (type class, API surface) must change.
    DEPENDENCY_ADDITION:
        A new external library or package dependency is required.
    """

    NEW_TYPE = "NEW_TYPE"
    NEW_FUNCTION = "NEW_FUNCTION"
    REFACTOR = "REFACTOR"
    NEW_AXIOM = "NEW_AXIOM"
    NEW_LEMMA = "NEW_LEMMA"
    DEPRECATION = "DEPRECATION"
    INTERFACE_CHANGE = "INTERFACE_CHANGE"
    DEPENDENCY_ADDITION = "DEPENDENCY_ADDITION"

    def priority_modifier(self) -> float:
        """Return the priority modifier for this consequence type.

        Types that form the foundation of implementation (NEW_TYPE, NEW_AXIOM)
        receive a positive bonus; low-stakes bookkeeping (DEPRECATION,
        DEPENDENCY_ADDITION) receives a small penalty.

        Returns
        -------
        float
            A signed offset to apply to the base priority.
        """
        high = {ConsequenceType.NEW_TYPE, ConsequenceType.NEW_AXIOM, ConsequenceType.NEW_LEMMA}
        low = {ConsequenceType.DEPRECATION, ConsequenceType.DEPENDENCY_ADDITION}
        if self in high:
            return HIGH_IMPORTANCE_BONUS
        if self in low:
            return -LOW_IMPORTANCE_PENALTY
        return 0.0

    def typical_effort_multiplier(self) -> float:
        """Return the typical effort multiplier for this consequence type.

        This is used by :func:`_effort_estimate` to scale the base estimate.

        Returns
        -------
        float
            A positive multiplier (1.0 = same as base effort).
        """
        multipliers = {
            ConsequenceType.NEW_TYPE: 1.0,
            ConsequenceType.NEW_FUNCTION: 0.8,
            ConsequenceType.REFACTOR: 1.5,
            ConsequenceType.NEW_AXIOM: 1.2,
            ConsequenceType.NEW_LEMMA: 1.8,
            ConsequenceType.DEPRECATION: 0.5,
            ConsequenceType.INTERFACE_CHANGE: 2.0,
            ConsequenceType.DEPENDENCY_ADDITION: 0.3,
        }
        return multipliers.get(self, 1.0)

    def short_label(self) -> str:
        """Return a two-to-four letter abbreviation for use in compact displays."""
        labels = {
            ConsequenceType.NEW_TYPE: "TYPE",
            ConsequenceType.NEW_FUNCTION: "FUNC",
            ConsequenceType.REFACTOR: "RFCT",
            ConsequenceType.NEW_AXIOM: "AXIM",
            ConsequenceType.NEW_LEMMA: "LEMM",
            ConsequenceType.DEPRECATION: "DEPR",
            ConsequenceType.INTERFACE_CHANGE: "INTC",
            ConsequenceType.DEPENDENCY_ADDITION: "DEPS",
        }
        return labels.get(self, self.value[:4])


# ---------------------------------------------------------------------------
# Value objects
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ImplementationConsequenceConfig:
    """Hyper-parameters for the implementation-consequence derivation stage.

    Attributes
    ----------
    auto_generate:
        If ``True`` the coordinator will attempt to auto-generate code
        sketches for every consequence.  Defaults to ``False`` because
        sketch generation is heuristic and may produce incorrect code.
    confidence_threshold:
        Only consequences whose parent hypothesis has a composite score
        above this value are included in the output.
    max_consequences:
        Hard cap on the number of consequences generated per coordinator
        run.  Excess consequences are dropped (lowest-priority first).
    include_examples:
        Whether to include example strings in
        :class:`TypeConstructorProposal` fields when deriving consequences.
    verbosity_level:
        Controls how much detail is included in generated explanations.
        0 = one line; 1 = short paragraph; 2 = full detail.
    """

    auto_generate: bool = False
    confidence_threshold: float = 0.6
    max_consequences: int = 20
    include_examples: bool = True
    verbosity_level: int = 2


@dataclass(frozen=True, slots=True)
class ImplementationConsequence:
    """A single concrete implementation consequence of a kind hypothesis.

    Instances are immutable; use :func:`dataclasses.replace` to create
    modified copies.

    Attributes
    ----------
    consequence_id:
        Unique identifier for this consequence.
    kind_hypothesis_id:
        The :class:`KindHypothesis` from which this consequence was derived.
    consequence_type:
        What category of implementation work this represents.
    title:
        A short title, suitable for a ticket or task description.
    description:
        A paragraph-length description of what needs to be done and why.
    code_sketch:
        A rough code sketch (Haskell-ish pseudocode or Python stub) showing
        what the implementation might look like.  May be empty.
    priority:
        A float in [0.0, 1.0] indicating how urgently this should be
        implemented.  Higher is more urgent.
    estimated_effort:
        Estimated implementation effort in abstract story points.
    dependencies:
        Identifiers of other :class:`ImplementationConsequence` objects that
        must be completed before this one can start.
    timestamp:
        UTC timestamp at which this consequence was created.
    """

    consequence_id: str
    kind_hypothesis_id: str
    consequence_type: ConsequenceType
    title: str
    description: str
    code_sketch: str
    priority: float
    estimated_effort: float
    dependencies: tuple[str, ...]
    timestamp: str

    # ------------------------------------------------------------------
    # Predicates and helpers
    # ------------------------------------------------------------------

    def is_high_priority(self, threshold: float = 0.7) -> bool:
        """Return True if priority exceeds *threshold*."""
        return self.priority >= threshold

    def has_dependencies(self) -> bool:
        """Return True if this consequence depends on other consequences."""
        return bool(self.dependencies)

    def effort_band(self) -> str:
        """Return a human-readable effort band label.

        Bands:
          XS (≤ 1 pt), S (≤ 2 pt), M (≤ 4 pt), L (≤ 8 pt), XL (> 8 pt).
        """
        e = self.estimated_effort
        if e <= 1.0:
            return "XS"
        if e <= 2.0:
            return "S"
        if e <= 4.0:
            return "M"
        if e <= 8.0:
            return "L"
        return "XL"

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain Python dict."""
        return {
            "consequence_id": self.consequence_id,
            "kind_hypothesis_id": self.kind_hypothesis_id,
            "consequence_type": self.consequence_type.value,
            "title": self.title,
            "description": self.description,
            "code_sketch": self.code_sketch,
            "priority": self.priority,
            "estimated_effort": self.estimated_effort,
            "effort_band": self.effort_band(),
            "dependencies": list(self.dependencies),
            "timestamp": self.timestamp,
            "is_high_priority": self.is_high_priority(),
        }


# ---------------------------------------------------------------------------
# Mutable container
# ---------------------------------------------------------------------------


class ConsequenceGraph:
    """A directed acyclic graph of implementation consequences.

    Nodes are :class:`ImplementationConsequence` objects; edges represent
    "must complete before" relationships (i.e., dependency edges point from
    the dependency *to* the dependent).

    This class is intentionally *not* frozen because it is built
    incrementally as consequences are derived.

    Usage::

        graph = ConsequenceGraph()
        graph.add(consequence_a)
        graph.add(consequence_b)  # depends on a
        ordered = graph.topological_order()
        path = graph.critical_path()

    Notes
    -----
    Cycle detection is performed during topological sort.  If a cycle is
    found, the sort returns the partial order up to the cycle and logs a
    warning (it does not raise).
    """

    def __init__(self) -> None:
        self._nodes: dict[str, ImplementationConsequence] = {}

    # ------------------------------------------------------------------
    # Mutation
    # ------------------------------------------------------------------

    def add(self, c: ImplementationConsequence) -> None:
        """Add *c* to the graph.

        If a consequence with the same ID already exists it is silently
        overwritten (last write wins).

        Parameters
        ----------
        c:
            The consequence to add.
        """
        self._nodes[c.consequence_id] = c

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def dependencies_of(self, cid: str) -> list[ImplementationConsequence]:
        """Return the direct dependencies of consequence *cid*.

        Parameters
        ----------
        cid:
            The consequence identifier to look up.

        Returns
        -------
        list[ImplementationConsequence]
            All consequences that *cid* directly depends on and that are
            present in this graph.  Returns an empty list if *cid* is
            unknown.
        """
        node = self._nodes.get(cid)
        if node is None:
            return []
        result = []
        for dep_id in node.dependencies:
            dep = self._nodes.get(dep_id)
            if dep is not None:
                result.append(dep)
        return result

    def topological_order(self) -> list[ImplementationConsequence]:
        """Return all consequences in a topological order.

        Consequences with no dependencies come first; those that depend on
        others come later.  If the graph contains a cycle the partial order
        is returned (cycle edges are ignored with a best-effort approach).

        Returns
        -------
        list[ImplementationConsequence]
            All nodes in a valid topological order.
        """
        colour: dict[str, int] = {}
        result: list[ImplementationConsequence] = []

        def _visit(cid: str) -> None:
            if colour.get(cid) == _TOPO_BLACK:
                return
            if colour.get(cid) == _TOPO_GREY:
                # Cycle detected — skip to avoid infinite recursion
                return
            colour[cid] = _TOPO_GREY
            node = self._nodes.get(cid)
            if node is not None:
                for dep_id in node.dependencies:
                    _visit(dep_id)
                colour[cid] = _TOPO_BLACK
                result.append(node)

        for node_id in self._nodes:
            _visit(node_id)

        return result

    def critical_path(self) -> list[str]:
        """Return the sequence of consequence IDs on the critical (longest) path.

        The critical path is the longest dependency chain in the graph,
        weighted by :attr:`ImplementationConsequence.estimated_effort`.
        A longer path means more serial work before the final consequence
        can begin.

        Returns
        -------
        list[str]
            IDs of consequences on the critical path, in dependency order
            (earliest dependency first).
        """
        # Dynamic programming on the DAG
        longest: dict[str, float] = {}
        predecessor: dict[str, str | None] = {}

        topo = self.topological_order()
        for node in topo:
            best_dep_effort = 0.0
            best_dep_id: str | None = None
            for dep in self.dependencies_of(node.consequence_id):
                dep_effort = longest.get(dep.consequence_id, 0.0)
                if dep_effort > best_dep_effort:
                    best_dep_effort = dep_effort
                    best_dep_id = dep.consequence_id
            longest[node.consequence_id] = node.estimated_effort + best_dep_effort
            predecessor[node.consequence_id] = best_dep_id

        if not longest:
            return []

        # Find the end node with the highest total effort
        end_id = max(longest, key=lambda k: longest[k])

        # Trace back the path
        path: list[str] = []
        current: str | None = end_id
        while current is not None:
            path.append(current)
            current = predecessor.get(current)
        path.reverse()
        return path

    def node_count(self) -> int:
        """Return the number of consequences in the graph."""
        return len(self._nodes)

    def edge_count(self) -> int:
        """Return the total number of dependency edges in the graph."""
        return sum(len(n.dependencies) for n in self._nodes.values())

    def to_dict(self) -> dict[str, Any]:
        """Serialise the graph to a plain Python dict.

        Returns
        -------
        dict[str, Any]
            Contains ``nodes`` (list of consequence dicts), ``edge_count``,
            and ``critical_path``.
        """
        return {
            "nodes": [n.to_dict() for n in self.topological_order()],
            "edge_count": self.edge_count(),
            "critical_path": self.critical_path(),
        }


# ---------------------------------------------------------------------------
# Analysis engine
# ---------------------------------------------------------------------------


class ImplementationConsequencesAnalyzer:
    """Derives, prioritises, and explains implementation consequences.

    This class is the core of the S04 stage.  It takes a kind hypothesis
    and its associated type-constructor proposal and produces a list of
    :class:`ImplementationConsequence` objects that describe what needs to
    be built.

    Parameters
    ----------
    config:
        Hyper-parameters.  Defaults to
        :class:`ImplementationConsequenceConfig`.

    Examples
    --------
    ::

        analyzer = ImplementationConsequencesAnalyzer()
        hyp = {"hypothesis_id": "hyp-001", "name": "SemiringKind",
               "composite_score": 0.8, "abstraction_level": "INTERMEDIATE"}
        proposal = {"proposal_id": "prop-001", "constructor_name": "MkSemiring",
                    "laws": ["zero-annihilation", "distributivity"]}
        consequences = analyzer.derive_consequences(hyp, proposal)
        graph = analyzer.build_graph(consequences)
    """

    def __init__(self, config: ImplementationConsequenceConfig | None = None) -> None:
        self._config = config or ImplementationConsequenceConfig()

    # ------------------------------------------------------------------
    # Core methods
    # ------------------------------------------------------------------

    def derive_consequences(
        self,
        hyp: dict,
        proposal: dict,
    ) -> list[ImplementationConsequence]:
        """Derive a list of implementation consequences from a hypothesis and proposal.

        The derived consequences follow a fixed template ordered by their
        natural dependency chain:

        1. ``NEW_TYPE`` — the kind's data type declaration.
        2. ``NEW_FUNCTION`` — the smart constructor.
        3. ``NEW_AXIOM`` — the algebraic laws.
        4. ``NEW_LEMMA`` — consistency lemmas.
        5. ``REFACTOR`` — ad-hoc workarounds to replace.
        6. ``INTERFACE_CHANGE`` — public API surface changes.
        7. ``DEPRECATION`` — obsolete entities to remove.
        8. ``DEPENDENCY_ADDITION`` — any new library dependencies.

        Parameters
        ----------
        hyp:
            The hypothesis dict (from :class:`KindHypothesis` or a plain
            dict with ``hypothesis_id``, ``name``, ``composite_score``,
            ``abstraction_level`` keys).
        proposal:
            The type-constructor proposal dict (from
            :class:`TypeConstructorProposal` or a plain dict with
            ``proposal_id``, ``constructor_name``, ``laws`` keys).

        Returns
        -------
        list[ImplementationConsequence]
            The derived consequences in dependency order.
        """
        hyp_id = str(hyp.get("hypothesis_id", "hyp-unknown"))
        hyp_name = str(hyp.get("name", "UnknownKind"))
        base_score = float(hyp.get("composite_score", DEFAULT_PRIORITY))
        abstraction = str(hyp.get("abstraction_level", "INTERMEDIATE"))
        constructor_name = str(proposal.get("constructor_name", f"Mk{hyp_name}"))
        laws: list[str] = list(proposal.get("laws", []))

        # Map abstraction to complexity
        complexity_map = {
            "ELEMENTARY": 0.2,
            "INTERMEDIATE": 0.5,
            "ADVANCED": 0.75,
            "FOUNDATIONAL": 0.95,
        }
        complexity = complexity_map.get(abstraction, 0.5)
        novelty = _clamp(base_score, 0.0, 1.0)

        # Build the type-declaration consequence
        type_id = _consequence_id()
        type_cons = ImplementationConsequence(
            consequence_id=type_id,
            kind_hypothesis_id=hyp_id,
            consequence_type=ConsequenceType.NEW_TYPE,
            title=f"Declare data type for {hyp_name}",
            description=(
                f"Introduce a new algebraic data type '{hyp_name}' to the codebase.  "
                f"This type is the carrier of the new kind proposed by hypothesis {hyp_id}.  "
                f"It should be parameterised as needed and derive standard instances "
                f"(Show, Eq, Ord, etc.)."
            ),
            code_sketch=self.generate_code_sketch(
                ImplementationConsequence(
                    consequence_id=type_id,
                    kind_hypothesis_id=hyp_id,
                    consequence_type=ConsequenceType.NEW_TYPE,
                    title="",
                    description="",
                    code_sketch="",
                    priority=0.0,
                    estimated_effort=0.0,
                    dependencies=(),
                    timestamp=_now_iso(),
                )
            ) if self._config.auto_generate else
            f"{SKETCH_HEADER}\nnewtype {hyp_name} f a = {constructor_name} {{ un{hyp_name} :: f a }}\n"
            f"  deriving (Show, Eq, Functor, Foldable, Traversable)",
            priority=_clamp(base_score + ConsequenceType.NEW_TYPE.priority_modifier(), 0.0, 1.0),
            estimated_effort=_effort_estimate(complexity, novelty) * ConsequenceType.NEW_TYPE.typical_effort_multiplier(),
            dependencies=(),
            timestamp=_now_iso(),
        )

        # Smart constructor
        func_id = _consequence_id()
        func_cons = ImplementationConsequence(
            consequence_id=func_id,
            kind_hypothesis_id=hyp_id,
            consequence_type=ConsequenceType.NEW_FUNCTION,
            title=f"Implement smart constructor for {hyp_name}",
            description=(
                f"Write a smart constructor function 'mk{hyp_name}' that validates "
                f"the constructor's preconditions before wrapping a value in '{hyp_name}'.  "
                f"This function is the primary public API for creating instances of the new kind."
            ),
            code_sketch=(
                f"{SKETCH_HEADER}\n"
                f"mk{hyp_name} :: (Validate f) => f a -> Either ValidationError ({hyp_name} f a)\n"
                f"mk{hyp_name} fa\n"
                f"  | validate fa = Right ({constructor_name} fa)\n"
                f"  | otherwise   = Left  (ValidationError \"{hyp_name} precondition violated\")"
            ),
            priority=_clamp(base_score + ConsequenceType.NEW_FUNCTION.priority_modifier(), 0.0, 1.0),
            estimated_effort=_effort_estimate(complexity * 0.8, novelty * 0.9) * ConsequenceType.NEW_FUNCTION.typical_effort_multiplier(),
            dependencies=(type_id,),
            timestamp=_now_iso(),
        )

        # Axiom consequences (one per law)
        axiom_ids: list[str] = []
        axiom_consequences: list[ImplementationConsequence] = []
        for law in laws:
            ax_id = _consequence_id()
            axiom_ids.append(ax_id)
            axiom_consequences.append(
                ImplementationConsequence(
                    consequence_id=ax_id,
                    kind_hypothesis_id=hyp_id,
                    consequence_type=ConsequenceType.NEW_AXIOM,
                    title=f"State axiom: {law} for {hyp_name}",
                    description=(
                        f"Formalise the '{law}' axiom for the '{hyp_name}' kind.  "
                        f"This should be expressed either as a QuickCheck property "
                        f"(for runtime testing) or as a type-level proof obligation "
                        f"(for static verification)."
                    ),
                    code_sketch=(
                        f"{SKETCH_HEADER}\n"
                        f"-- Axiom: {law}\n"
                        f"prop_{law.replace('-', '_')} :: {hyp_name} f a -> Bool\n"
                        f"prop_{law.replace('-', '_')} x = ... -- TODO: formalise"
                    ),
                    priority=_clamp(base_score + ConsequenceType.NEW_AXIOM.priority_modifier(), 0.0, 1.0),
                    estimated_effort=_effort_estimate(complexity, novelty * 0.8) * ConsequenceType.NEW_AXIOM.typical_effort_multiplier(),
                    dependencies=(type_id, func_id),
                    timestamp=_now_iso(),
                )
            )

        # Lemma: consistency
        lemma_id = _consequence_id()
        lemma_cons = ImplementationConsequence(
            consequence_id=lemma_id,
            kind_hypothesis_id=hyp_id,
            consequence_type=ConsequenceType.NEW_LEMMA,
            title=f"Prove consistency of {hyp_name} laws",
            description=(
                f"Verify that the axioms stated for '{hyp_name}' are mutually consistent "
                f"and do not admit a proof of False.  In the QuickCheck setting this means "
                f"running all property tests with a large random sample; in a proof assistant "
                f"it means constructing a model."
            ),
            code_sketch=(
                f"{SKETCH_HEADER}\n"
                f"-- Consistency check for {hyp_name}\n"
                f"checkConsistency{hyp_name} :: IO ()\n"
                f"checkConsistency{hyp_name} = do\n"
                + "\n".join(
                    f"  quickCheck prop_{l.replace('-', '_')}"
                    for l in laws
                )
                + "\n  putStrLn \"All {hyp_name} laws hold.\""
            ) if laws else f"{SKETCH_HEADER}\n-- No laws to check.",
            priority=_clamp(base_score + ConsequenceType.NEW_LEMMA.priority_modifier() - 0.05, 0.0, 1.0),
            estimated_effort=_effort_estimate(complexity, novelty) * ConsequenceType.NEW_LEMMA.typical_effort_multiplier(),
            dependencies=tuple(axiom_ids) + (func_id,),
            timestamp=_now_iso(),
        )

        # Refactor: replace ad-hoc workarounds
        refactor_id = _consequence_id()
        refactor_cons = ImplementationConsequence(
            consequence_id=refactor_id,
            kind_hypothesis_id=hyp_id,
            consequence_type=ConsequenceType.REFACTOR,
            title=f"Refactor ad-hoc workarounds to use {hyp_name}",
            description=(
                f"Search the codebase for patterns that are currently encoding the "
                f"'{hyp_name}' behaviour without using the new kind.  Replace each "
                f"occurrence with the appropriate '{constructor_name}' constructor call.  "
                f"This refactor improves type-safety and removes duplicated logic."
            ),
            code_sketch=(
                f"{SKETCH_HEADER}\n"
                f"-- Before (ad-hoc):\n"
                f"--   let result = unsafeCompose f g\n"
                f"-- After (using {hyp_name}):\n"
                f"--   let result = un{hyp_name} (mk{hyp_name} (f `composedWith` g))"
            ),
            priority=_clamp(base_score + ConsequenceType.REFACTOR.priority_modifier() - 0.1, 0.0, 1.0),
            estimated_effort=_effort_estimate(complexity * 1.2, novelty * 0.7) * ConsequenceType.REFACTOR.typical_effort_multiplier(),
            dependencies=(type_id, func_id, lemma_id),
            timestamp=_now_iso(),
        )

        # Interface change
        iface_id = _consequence_id()
        iface_cons = ImplementationConsequence(
            consequence_id=iface_id,
            kind_hypothesis_id=hyp_id,
            consequence_type=ConsequenceType.INTERFACE_CHANGE,
            title=f"Update public interfaces to expose {hyp_name}",
            description=(
                f"Modify the public module exports and type-class hierarchies to "
                f"include '{hyp_name}'.  This may involve adding a new type-class "
                f"instance, re-exporting the type from the library façade, and "
                f"updating any module-level documentation."
            ),
            code_sketch=(
                f"{SKETCH_HEADER}\n"
                f"-- Add to module exports:\n"
                f"module Jugeo.Kinds (\n"
                f"    module Jugeo.Kinds.{hyp_name},\n"
                f"    ...\n"
                f") where\n"
                f"import Jugeo.Kinds.{hyp_name}"
            ),
            priority=_clamp(base_score + ConsequenceType.INTERFACE_CHANGE.priority_modifier() - 0.05, 0.0, 1.0),
            estimated_effort=_effort_estimate(complexity * 0.6, novelty * 0.5) * ConsequenceType.INTERFACE_CHANGE.typical_effort_multiplier(),
            dependencies=(type_id, func_id, refactor_id),
            timestamp=_now_iso(),
        )

        # Deprecation
        depr_id = _consequence_id()
        depr_cons = ImplementationConsequence(
            consequence_id=depr_id,
            kind_hypothesis_id=hyp_id,
            consequence_type=ConsequenceType.DEPRECATION,
            title=f"Deprecate ad-hoc {hyp_name}-like patterns",
            description=(
                f"Mark any functions or types that the '{hyp_name}' kind supersedes "
                f"as deprecated in the API documentation.  Schedule their removal for "
                f"the next major version bump."
            ),
            code_sketch=(
                f"{SKETCH_HEADER}\n"
                f"{{-# DEPRECATED unsafeCompose \"Use {hyp_name} instead\" #-}}"
            ),
            priority=_clamp(base_score + ConsequenceType.DEPRECATION.priority_modifier(), 0.0, 1.0),
            estimated_effort=_effort_estimate(complexity * 0.3, novelty * 0.2) * ConsequenceType.DEPRECATION.typical_effort_multiplier(),
            dependencies=(refactor_id, iface_id),
            timestamp=_now_iso(),
        )

        consequences = (
            [type_cons, func_cons]
            + axiom_consequences
            + [lemma_cons, refactor_cons, iface_cons, depr_cons]
        )
        return consequences

    def prioritize(
        self,
        consequences: list[ImplementationConsequence],
    ) -> list[ImplementationConsequence]:
        """Sort *consequences* in descending order of priority.

        Ties are broken by consequence type (NEW_TYPE before REFACTOR before
        DEPRECATION) and then by estimated effort (smaller first, so quick
        wins appear early).

        Parameters
        ----------
        consequences:
            Unsorted list of consequences.

        Returns
        -------
        list[ImplementationConsequence]
            The same consequences sorted best-first.
        """
        _type_order = [
            ConsequenceType.NEW_TYPE,
            ConsequenceType.NEW_AXIOM,
            ConsequenceType.NEW_FUNCTION,
            ConsequenceType.NEW_LEMMA,
            ConsequenceType.INTERFACE_CHANGE,
            ConsequenceType.REFACTOR,
            ConsequenceType.DEPRECATION,
            ConsequenceType.DEPENDENCY_ADDITION,
        ]

        def _sort_key(c: ImplementationConsequence) -> tuple:
            type_rank = _type_order.index(c.consequence_type) if c.consequence_type in _type_order else 99
            return (-c.priority, type_rank, c.estimated_effort)

        return sorted(consequences, key=_sort_key)

    def build_graph(
        self,
        consequences: list[ImplementationConsequence],
    ) -> ConsequenceGraph:
        """Construct a :class:`ConsequenceGraph` from *consequences*.

        Parameters
        ----------
        consequences:
            The consequences to add.

        Returns
        -------
        ConsequenceGraph
            A graph containing all consequences with their dependency edges.
        """
        graph = ConsequenceGraph()
        for c in consequences:
            graph.add(c)
        return graph

    def generate_code_sketch(
        self, consequence: ImplementationConsequence
    ) -> str:
        """Generate a heuristic code sketch for *consequence*.

        The sketch is a very rough template, not production-ready code.
        Its purpose is to give implementors a starting point.

        Parameters
        ----------
        consequence:
            The consequence to sketch.

        Returns
        -------
        str
            A short multi-line pseudocode string.
        """
        ctype = consequence.consequence_type
        hyp_id = consequence.kind_hypothesis_id
        if ctype == ConsequenceType.NEW_TYPE:
            return (
                f"{SKETCH_HEADER}\n"
                f"-- New type for hypothesis {hyp_id}\n"
                f"newtype KindCarrier f a = MkKindCarrier {{ runKindCarrier :: f a }}\n"
                f"  deriving (Show, Eq)"
            )
        if ctype == ConsequenceType.NEW_FUNCTION:
            return (
                f"{SKETCH_HEADER}\n"
                f"-- Smart constructor for hypothesis {hyp_id}\n"
                f"mkKind :: f a -> KindCarrier f a\n"
                f"mkKind = MkKindCarrier"
            )
        if ctype == ConsequenceType.NEW_AXIOM:
            return (
                f"{SKETCH_HEADER}\n"
                f"-- Axiom for hypothesis {hyp_id}\n"
                f"propAxiom :: KindCarrier f a -> Bool\n"
                f"propAxiom _ = True  -- TODO: formalise"
            )
        if ctype == ConsequenceType.REFACTOR:
            return (
                f"{SKETCH_HEADER}\n"
                f"-- Refactor: replace ad-hoc usages with KindCarrier\n"
                f"-- Before: unsafeOp x\n"
                f"-- After:  runKindCarrier (mkKind x)"
            )
        return f"{SKETCH_HEADER}\n-- Sketch for {ctype.value} consequence\n-- TODO: implement"

    def estimate_effort(self, consequence: ImplementationConsequence) -> float:
        """Re-estimate the effort for *consequence* using current config.

        This method applies the consequence type's typical effort multiplier
        on top of the base :func:`_effort_estimate`.

        Parameters
        ----------
        consequence:
            The consequence to re-estimate.

        Returns
        -------
        float
            Effort in story points.
        """
        complexity = _clamp(1.0 - consequence.priority, 0.0, 1.0)
        novelty = _clamp(consequence.priority, 0.0, 1.0)
        base = _effort_estimate(complexity, novelty)
        return round(base * consequence.consequence_type.typical_effort_multiplier(), 1)

    def explain_consequence(self, consequence: ImplementationConsequence) -> str:
        """Produce a human-readable explanation of *consequence*.

        The verbosity of the explanation is controlled by
        :attr:`ImplementationConsequenceConfig.verbosity_level`.

        Parameters
        ----------
        consequence:
            The consequence to explain.

        Returns
        -------
        str
            A formatted string.
        """
        vl = self._config.verbosity_level
        lines = [
            f"Consequence {consequence.consequence_id}  [{consequence.consequence_type.short_label()}]",
        ]
        if vl >= 1:
            lines += [
                "=" * 60,
                f"Title:       {consequence.title}",
                f"Type:        {consequence.consequence_type.value}",
                f"Priority:    {consequence.priority:.3f}  "
                f"({'HIGH' if consequence.is_high_priority() else 'NORMAL'})",
                f"Effort:      {consequence.estimated_effort} pts ({consequence.effort_band()})",
                f"Depends on:  {', '.join(consequence.dependencies) or 'none'}",
            ]
        if vl >= 2:
            lines += [
                "",
                "Description:",
                f"  {consequence.description}",
                "",
                "Code sketch:",
            ]
            for sketch_line in consequence.code_sketch.splitlines()[:MAX_SKETCH_LINES]:
                lines.append(f"  {sketch_line}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Witness
# ---------------------------------------------------------------------------


class ImplementationConsequencesWitness:
    """Records and queries :class:`ImplementationConsequence` objects.

    Acts as an append-only log for the entire coordinator run.

    Usage::

        witness = ImplementationConsequencesWitness()
        witness.record(cons_a)
        witness.record(cons_b)
        print(witness.total_effort())
        print(witness.by_type(ConsequenceType.NEW_TYPE))
        data = witness.export()
    """

    def __init__(self) -> None:
        self._consequences: list[ImplementationConsequence] = []

    def record(self, c: ImplementationConsequence) -> None:
        """Append *c* to the internal log.

        Parameters
        ----------
        c:
            The consequence to record.
        """
        self._consequences.append(c)

    def by_type(self, t: ConsequenceType) -> list[ImplementationConsequence]:
        """Return all consequences of type *t*.

        Parameters
        ----------
        t:
            The :class:`ConsequenceType` to filter on.

        Returns
        -------
        list[ImplementationConsequence]
            Matching consequences in insertion order.
        """
        return [c for c in self._consequences if c.consequence_type == t]

    def total_effort(self) -> float:
        """Return the sum of estimated effort across all recorded consequences.

        Returns
        -------
        float
            Total story points.
        """
        return sum(c.estimated_effort for c in self._consequences)

    def high_priority(self, threshold: float = 0.7) -> list[ImplementationConsequence]:
        """Return all consequences with priority above *threshold*.

        Parameters
        ----------
        threshold:
            Minimum priority value.

        Returns
        -------
        list[ImplementationConsequence]
            Matching consequences in insertion order.
        """
        return [c for c in self._consequences if c.priority >= threshold]

    def count(self) -> int:
        """Return the total number of recorded consequences."""
        return len(self._consequences)

    def summary(self) -> dict[str, Any]:
        """Return aggregate statistics as a plain dict.

        Keys include ``total``, ``total_effort``, ``high_priority``,
        ``by_type``, and ``avg_priority``.

        Returns
        -------
        dict[str, Any]
            Statistics dict.
        """
        if not self._consequences:
            return {"total": 0, "total_effort": 0.0, "high_priority": 0, "by_type": {}, "avg_priority": 0.0}
        type_counts = {}
        for ct in ConsequenceType:
            cnt = len(self.by_type(ct))
            if cnt:
                type_counts[ct.value] = cnt
        avg_p = sum(c.priority for c in self._consequences) / len(self._consequences)
        return {
            "total": len(self._consequences),
            "total_effort": round(self.total_effort(), 1),
            "high_priority": len(self.high_priority()),
            "by_type": type_counts,
            "avg_priority": round(avg_p, 4),
        }

    def export(self) -> list[dict[str, Any]]:
        """Serialise all consequences to a list of dicts.

        Returns
        -------
        list[dict[str, Any]]
            One dict per consequence.
        """
        return [c.to_dict() for c in self._consequences]


# ---------------------------------------------------------------------------
# Coordinator
# ---------------------------------------------------------------------------


class ImplementationConsequencesCoordinator:
    """End-to-end orchestrator for the implementation-consequence stage.

    Given a list of kind hypotheses and their associated type-constructor
    proposals, derives all implementation consequences, prioritises them,
    and records them in the witness.

    Parameters
    ----------
    config:
        Pipeline hyper-parameters.

    Example
    -------
    ::

        coord = ImplementationConsequencesCoordinator()
        hypotheses = [
            {"hypothesis_id": "hyp-001", "name": "SemiringKind",
             "composite_score": 0.8, "abstraction_level": "INTERMEDIATE"},
        ]
        proposals = [
            {"proposal_id": "prop-001", "kind_hypothesis_id": "hyp-001",
             "constructor_name": "MkSemiring", "laws": ["zero-annihilation"]},
        ]
        consequences = coord.run(hypotheses, proposals)
        print(coord.report())
    """

    def __init__(self, config: ImplementationConsequenceConfig | None = None) -> None:
        self._config = config or ImplementationConsequenceConfig()
        self.analyzer = ImplementationConsequencesAnalyzer(self._config)
        self.witness = ImplementationConsequencesWitness()

    def run(
        self,
        hypotheses: list[dict],
        proposals: list[dict],
    ) -> list[ImplementationConsequence]:
        """Derive consequences for all hypothesis+proposal pairs.

        Hypotheses and proposals are matched by ``kind_hypothesis_id``.
        Hypotheses without a matching proposal are paired with an empty
        stub proposal so that at least the basic type and function
        consequences are generated.

        Parameters
        ----------
        hypotheses:
            List of kind hypothesis dicts.
        proposals:
            List of type-constructor proposal dicts.

        Returns
        -------
        list[ImplementationConsequence]
            All derived, prioritised consequences (up to
            ``config.max_consequences``).
        """
        # Index proposals by hypothesis id
        proposal_index: dict[str, dict] = {}
        for p in proposals:
            hid = str(p.get("kind_hypothesis_id", ""))
            proposal_index[hid] = p

        all_consequences: list[ImplementationConsequence] = []
        for hyp in hypotheses:
            # Skip low-confidence hypotheses
            score = float(hyp.get("composite_score", 0.0))
            if score < self._config.confidence_threshold:
                continue
            hyp_id = str(hyp.get("hypothesis_id", ""))
            proposal = proposal_index.get(hyp_id, {"proposal_id": "stub", "constructor_name": "MkStub", "laws": []})
            consequences = self.analyzer.derive_consequences(hyp, proposal)
            all_consequences.extend(consequences)

        # Prioritise and cap
        prioritised = self.analyzer.prioritize(all_consequences)
        accepted = prioritised[: self._config.max_consequences]

        for c in accepted:
            self.witness.record(c)

        return accepted

    def report(self) -> dict[str, Any]:
        """Return a coordinator snapshot report.

        Returns
        -------
        dict[str, Any]
            Contains witness summary and optionally the full consequence
            list.
        """
        graph = self.analyzer.build_graph(
            [c for c in self.witness._consequences]
        )
        return {
            "summary": self.witness.summary(),
            "graph": {
                "node_count": graph.node_count(),
                "edge_count": graph.edge_count(),
                "critical_path_length": len(graph.critical_path()),
            },
        }


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import json

    _hypotheses = [
        {
            "hypothesis_id": "hyp-abc001",
            "name": "SemiringKind",
            "composite_score": 0.82,
            "abstraction_level": "INTERMEDIATE",
        },
        {
            "hypothesis_id": "hyp-abc002",
            "name": "ComonoidKind",
            "composite_score": 0.71,
            "abstraction_level": "ADVANCED",
        },
    ]
    _proposals = [
        {
            "proposal_id": "prop-x001",
            "kind_hypothesis_id": "hyp-abc001",
            "constructor_name": "MkSemiring",
            "laws": ["zero-annihilation", "distributivity", "additive-commutativity"],
        },
        {
            "proposal_id": "prop-x002",
            "kind_hypothesis_id": "hyp-abc002",
            "constructor_name": "MkComonoid",
            "laws": ["counit-law", "coassociativity"],
        },
    ]

    _coord = ImplementationConsequencesCoordinator()
    _consequences = _coord.run(_hypotheses, _proposals)

    print(f"Derived {len(_consequences)} consequence(s).\n")
    for _c in _consequences[:4]:
        print(_coord.analyzer.explain_consequence(_c))
        print()

    print(json.dumps(_coord.report(), indent=2))

    # Build and inspect the dependency graph
    _graph = _coord.analyzer.build_graph(_consequences)
    print(f"\nGraph: {_graph.node_count()} nodes, {_graph.edge_count()} edges")
    print(f"Critical path: {_graph.critical_path()}")
