"""Core data models for the repair_semantics subsystem (theory2.tex Ch11).

All models are frozen dataclasses with full JSON round-trip support.
See theory2.tex Ch11 for the theoretical foundations.

Theoretical overview
--------------------
The five models in this module correspond directly to the five principal
objects defined in Ch11 of theory2.tex:

1. **CounterexampleRecord** (Ch11 §11.2)
   A first-class semantic object representing a failed judgment.
   Counterexamples are classified as Cech 1-cohomology classes in H1(U, D)
   and carry full provenance metadata for audit trails.

2. **RepairPlan** (Ch11 §11.5)
   An ordered sequence of local-section-replacement steps with a
   topologically sorted dependency graph.  A plan is *admissible* iff its
   step graph is acyclic and every step's prerequisites are met.

3. **RepairFrontier** (Ch11 §11.4)
   The minimal set of coordinates whose sections must be replaced.
   Supports lattice operations (union, intersection, expand, contract)
   consistent with the sheaf-theoretic covering poset.

4. **DebugSession** (Ch11 §11.9)
   A monotone accumulator for counterexamples and repair attempts across
   iterations of the repair loop.  Sessions transition through the states
   OPEN -> CONVERGED | ABANDONED | BLOCKED.

5. **RepairValidator** (Ch11 §11.7)
   A rule-based checker for plan admissibility, step validity, and descent
   condition satisfaction.  Returns ValidationResult with structured
   failure/warning lists.

All models are @dataclass(frozen=True, slots=True) to guarantee
immutability and slot-based attribute access.  Immutable updates use
replace() from the dataclasses module.

# copilot: repair_semantics models -- theory2 ch11 core data structures
"""
from __future__ import annotations

import hashlib
import time
import uuid
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any, Mapping, Sequence

from jugeo.errors import (
    ObstructionRecord,
    RepairHint,
    RepairPriority,
    StructuredFailure,
    FailureScope,
    FailureClassification,
    EvidenceFamily,
)
from jugeo.judgments.judgment_terms import (
    EvidenceBundle,
    EvidenceItem,
    EvidenceItemKind,
    Provenance,
    ProvenanceSource,
    TrustLevel,
)
from jugeo.solver.countermodels import FailureClass, RepairType

# ---------------------------------------------------------------------------
# Type aliases
# ---------------------------------------------------------------------------

JsonScalar = None | bool | int | float | str
JsonValue = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]

# ---------------------------------------------------------------------------
# Module-level provenance
# ---------------------------------------------------------------------------

MANIFEST_SPEC_PROVENANCE: dict[str, str] = {
    "stage": "ch11-repair-semantics",
    "sequence": "11",
    "semantic_source": "preliminaries/theory2.tex",
    "module": "models",
}


# ---------------------------------------------------------------------------
# CounterexampleRecord
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class CounterexampleRecord:
    """A first-class semantic object representing a failed judgment.

    In the sheaf-theoretic framework of theory2.tex Ch11, a counterexample
    is not merely an error string but a *cohomology class* -- an element of
    the Cech cohomology group H1(U, D) computed over the covering U of the
    coordinate space associated with the failing judgment.

    Formal characterisation (Ch11 §11.2)
    --------------------------------------
    Given:
      * A coordinate c in the sheaf Gamma,
      * A judgment psi that fails at c,
      * A SAT/SMT model M witnessing the failure,

    the counterexample record stores:
      * The coordinate c (``coordinate``),
      * The failing proposition psi (``proposition``),
      * The model M as variable/sort/function interpretations,
      * The cohomological classification [delta(psi)] in H1 (``cohomology_class``),
      * Repair hints derived from the model,
      * Whether the model has been minimised (``is_minimal``).

    Minimisation
    ------------
    A counterexample is *minimal* iff removing any single variable assignment
    produces a model that no longer witnesses the failure.  Minimisation is
    performed by the ``with_minimization`` method, which replaces the
    ``variable_assignments`` field with the reduced set.

    Severity
    --------
    ``severity_score()`` returns an integer in [1, 5] based on the
    ``failure_class``:

      UNKNOWN                                    -> 1
      TYPE_MISMATCH, SORT_ERROR                  -> 2
      FUNCTION_ARITY, PREDICATE_ARITY            -> 3
      CONSTRAINT_VIOLATION, DESCENT_FAILURE      -> 4
      COHERENCE_FAILURE, GLOBAL_OBSTRUCTION      -> 5

    JSON round-trip
    ---------------
    ``to_dict`` / ``from_dict`` provide lossless serialisation.  All enum
    values are serialised as their string names; tuples are serialised as
    lists.

    Parameters
    ----------
    record_id:
        Unique identifier (hex UUID prefix).
    coordinate:
        The sheaf coordinate at which the failure was detected.
    proposition:
        The failing proposition as a string.
    failure_class:
        The FailureClass classification of the failure.
    variable_assignments:
        Tuple of (name, value) pairs from the SAT/SMT model.
    sort_interpretations:
        Tuple of (sort_name, (element, ...)) pairs.
    function_interpretations:
        Tuple of (func_name, ((arg_str, result_str), ...)) pairs.
    repair_hints:
        Tuple of RepairHint objects derived from the model.
    cohomology_class:
        String representation of the cohomology class.
    is_minimal:
        Whether the model has been delta-minimised.
    extraction_timestamp:
        ISO-8601 timestamp of extraction.
    provenance_source:
        The ProvenanceSource that produced this record.
    metadata:
        Additional key-value metadata pairs.
    """

    record_id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])
    coordinate: str = ""
    proposition: str = ""
    failure_class: FailureClass = FailureClass.UNKNOWN
    variable_assignments: tuple[tuple[str, str], ...] = ()
    sort_interpretations: tuple[tuple[str, tuple[str, ...]], ...] = ()
    function_interpretations: tuple[tuple[str, tuple[tuple[str, str], ...]], ...] = ()
    repair_hints: tuple[RepairHint, ...] = ()
    cohomology_class: str = ""
    is_minimal: bool = False
    extraction_timestamp: str = ""
    provenance_source: ProvenanceSource = ProvenanceSource.SOLVER
    metadata: tuple[tuple[str, str], ...] = ()

    def to_obstruction_record(self) -> ObstructionRecord:
        """Build an ObstructionRecord from this counterexample.

        The ObstructionRecord is the interface type consumed by the repair
        planner.  This conversion captures the coordinate, proposition,
        failure class, repair hints, cohomology class, and metadata.

        Returns
        -------
        ObstructionRecord
            A new ObstructionRecord corresponding to this counterexample.
        """
        return ObstructionRecord(
            coordinate=self.coordinate,
            proposition=self.proposition,
            failure_class=self.failure_class,
            repair_hints=list(self.repair_hints),
            cohomology_class=self.cohomology_class,
            metadata=dict(self.metadata),
        )

    def to_repair_hints(self) -> tuple[RepairHint, ...]:
        """Return the repair hints associated with this counterexample.

        Returns
        -------
        tuple[RepairHint, ...]
            The repair_hints tuple stored in this record.
        """
        return self.repair_hints

    def is_genuine(self) -> bool:
        """Return True iff this record represents a genuine counterexample.

        A counterexample is *genuine* if it has a non-UNKNOWN failure class
        and a non-empty coordinate.

        Returns
        -------
        bool
            True if failure_class != UNKNOWN and coordinate != "".
        """
        return self.failure_class != FailureClass.UNKNOWN and self.coordinate != ""

    def classify_failure(self) -> FailureClass:
        """Return the failure class of this counterexample.

        Returns
        -------
        FailureClass
            The failure_class stored in this record.
        """
        return self.failure_class

    def with_minimization(
        self, minimal_assignments: tuple[tuple[str, str], ...]
    ) -> "CounterexampleRecord":
        """Return a new record with variable assignments replaced by a minimised set.

        Parameters
        ----------
        minimal_assignments:
            The minimised tuple of (name, value) pairs.

        Returns
        -------
        CounterexampleRecord
            A new record with variable_assignments = minimal_assignments
            and is_minimal = True.
        """
        return replace(
            self,
            variable_assignments=minimal_assignments,
            is_minimal=True,
        )

    def severity_score(self) -> int:
        """Return an integer severity score in [1, 5] based on failure_class.

        Returns
        -------
        int
            An integer in [1, 5].
        """
        score_map: dict[str, int] = {
            "UNKNOWN": 1,
            "TYPE_MISMATCH": 2,
            "SORT_ERROR": 2,
            "FUNCTION_ARITY": 3,
            "PREDICATE_ARITY": 3,
            "CONSTRAINT_VIOLATION": 4,
            "DESCENT_FAILURE": 4,
            "COHERENCE_FAILURE": 5,
            "GLOBAL_OBSTRUCTION": 5,
        }
        return score_map.get(self.failure_class.name, 1)

    def to_dict(self) -> dict[str, "JsonValue"]:
        """Serialise this record to a plain-Python dict.

        Returns
        -------
        dict[str, JsonValue]
            A JSON-serialisable dict with all fields.
        """
        return {
            "record_id": self.record_id,
            "coordinate": self.coordinate,
            "proposition": self.proposition,
            "failure_class": self.failure_class.name,
            "variable_assignments": [list(pair) for pair in self.variable_assignments],
            "sort_interpretations": [
                [name, list(elems)]
                for name, elems in self.sort_interpretations
            ],
            "function_interpretations": [
                [name, [list(row) for row in rows]]
                for name, rows in self.function_interpretations
            ],
            "repair_hints": [
                h.name if isinstance(h, Enum) else str(h)
                for h in self.repair_hints
            ],
            "cohomology_class": self.cohomology_class,
            "is_minimal": self.is_minimal,
            "extraction_timestamp": self.extraction_timestamp,
            "provenance_source": self.provenance_source.name,
            "metadata": [list(pair) for pair in self.metadata],
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "CounterexampleRecord":
        """Deserialise a CounterexampleRecord from a plain-Python dict.

        Parameters
        ----------
        payload:
            A dict as produced by to_dict.

        Returns
        -------
        CounterexampleRecord
            The reconstructed record.

        Raises
        ------
        KeyError
            If a required key is absent.
        ValueError
            If an enum value string cannot be mapped to a known enum member.
        """
        return cls(
            record_id=payload.get("record_id", uuid.uuid4().hex[:16]),
            coordinate=payload.get("coordinate", ""),
            proposition=payload.get("proposition", ""),
            failure_class=FailureClass[payload["failure_class"]],
            variable_assignments=tuple(
                (row[0], row[1]) for row in payload.get("variable_assignments", [])
            ),
            sort_interpretations=tuple(
                (row[0], tuple(row[1]))
                for row in payload.get("sort_interpretations", [])
            ),
            function_interpretations=tuple(
                (row[0], tuple((r[0], r[1]) for r in row[1]))
                for row in payload.get("function_interpretations", [])
            ),
            repair_hints=tuple(
                RepairHint[h] if isinstance(h, str) else h
                for h in payload.get("repair_hints", [])
            ),
            cohomology_class=payload.get("cohomology_class", ""),
            is_minimal=payload.get("is_minimal", False),
            extraction_timestamp=payload.get("extraction_timestamp", ""),
            provenance_source=ProvenanceSource[
                payload.get("provenance_source", "SOLVER")
            ],
            metadata=tuple(
                (pair[0], pair[1]) for pair in payload.get("metadata", [])
            ),
        )


# ---------------------------------------------------------------------------
# RepairPlan
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class RepairPlan:
    """An ordered sequence of section-replacement steps with dependency ordering.

    A RepairPlan is the output of the repair-planning stage (Ch11 §11.5).
    It enumerates the concrete steps required to eliminate a cohomological
    obstruction, together with a dependency graph that constrains the order
    in which steps may be applied.

    Admissibility (Ch11 Thm 11.2)
    ------------------------------
    A plan is *admissible* iff its step dependency graph is a DAG (acyclic).
    The is_admissible method checks this condition.  The topological_sort
    method returns the unique linear order consistent with the DAG (ties
    broken by step_id lexicographic order).

    Inner type: RepairStep
    ----------------------
    Each step is represented by the nested frozen dataclass RepairStep.
    Steps carry: repair action, target coordinate, description,
    prerequisites, repair_type, priority, and estimated_effort.

    Parameters
    ----------
    plan_id:
        Unique identifier (hex UUID prefix).
    coordinate:
        The sheaf coordinate that this plan targets.
    steps:
        Ordered tuple of RepairStep objects.
    dependency_order:
        Tuple of (before_step_id, after_step_id) pairs.
    estimated_effort:
        Aggregate effort estimate.
    confidence_score:
        Float in [0.0, 1.0].
    provenance:
        The ProvenanceSource that produced this plan.
    metadata:
        Additional key-value metadata pairs.
    """

    @dataclass(frozen=True, slots=True)
    class RepairStep:
        """A single section-replacement step within a RepairPlan.

        Parameters
        ----------
        step_id:
            Unique identifier for this step.
        action:
            A short action descriptor.
        target_coordinate:
            The coordinate whose local section is to be replaced.
        description:
            Human-readable description.
        prerequisites:
            Tuple of step_id values that must complete before this step.
        repair_type:
            The RepairType algebraic classification.
        priority:
            The RepairPriority scheduling priority.
        estimated_effort:
            Effort estimate string.
        """

        step_id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
        action: str = ""
        target_coordinate: str = ""
        description: str = ""
        prerequisites: tuple[str, ...] = ()
        repair_type: RepairType = RepairType.MANUAL_REVIEW
        priority: RepairPriority = RepairPriority.SUGGESTED
        estimated_effort: str = "unknown"

        def to_dict(self) -> dict[str, "JsonValue"]:
            """Serialise this step to a plain-Python dict.

            Returns
            -------
            dict[str, JsonValue]
                A JSON-serialisable dict.
            """
            return {
                "step_id": self.step_id,
                "action": self.action,
                "target_coordinate": self.target_coordinate,
                "description": self.description,
                "prerequisites": list(self.prerequisites),
                "repair_type": self.repair_type.name,
                "priority": self.priority.name,
                "estimated_effort": self.estimated_effort,
            }

        @classmethod
        def from_dict(cls, payload: dict[str, Any]) -> "RepairPlan.RepairStep":
            """Deserialise a RepairStep from a plain-Python dict.

            Parameters
            ----------
            payload:
                A dict as produced by to_dict.

            Returns
            -------
            RepairPlan.RepairStep
                The reconstructed step.
            """
            return cls(
                step_id=payload.get("step_id", uuid.uuid4().hex[:8]),
                action=payload.get("action", ""),
                target_coordinate=payload.get("target_coordinate", ""),
                description=payload.get("description", ""),
                prerequisites=tuple(payload.get("prerequisites", [])),
                repair_type=RepairType[payload.get("repair_type", "MANUAL_REVIEW")],
                priority=RepairPriority[payload.get("priority", "SUGGESTED")],
                estimated_effort=payload.get("estimated_effort", "unknown"),
            )

    plan_id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])
    coordinate: str = ""
    steps: tuple[RepairStep, ...] = ()
    dependency_order: tuple[tuple[str, str], ...] = ()
    estimated_effort: str = "unknown"
    confidence_score: float = 0.0
    provenance: ProvenanceSource = ProvenanceSource.SOLVER
    metadata: tuple[tuple[str, str], ...] = ()

    def topological_sort(self) -> tuple["RepairPlan.RepairStep", ...]:
        """Return steps in a valid topological order using Kahn's algorithm.

        Kahn's algorithm repeatedly extracts nodes with in-degree zero.
        Ties are broken by step_id lexicographic order for determinism.

        Returns
        -------
        tuple[RepairStep, ...]
            Steps in a valid topological order.  If a cycle exists, the
            returned tuple may be shorter than self.steps.
        """
        step_map: dict[str, RepairPlan.RepairStep] = {s.step_id: s for s in self.steps}
        in_degree: dict[str, int] = {sid: 0 for sid in step_map}
        adjacency: dict[str, list[str]] = {sid: [] for sid in step_map}

        for before, after in self.dependency_order:
            if before in adjacency and after in in_degree:
                adjacency[before].append(after)
                in_degree[after] += 1

        queue = sorted([sid for sid, deg in in_degree.items() if deg == 0])
        result: list[RepairPlan.RepairStep] = []

        while queue:
            current = queue.pop(0)
            if current in step_map:
                result.append(step_map[current])
            for neighbour in sorted(adjacency.get(current, [])):
                in_degree[neighbour] -= 1
                if in_degree[neighbour] == 0:
                    queue.append(neighbour)
                    queue.sort()

        return tuple(result)

    def is_admissible(self) -> bool:
        """Return True iff the step dependency graph is a DAG.

        Returns
        -------
        bool
            True if len(topological_sort()) == len(self.steps).
        """
        return len(self.topological_sort()) == len(self.steps)

    def next_steps(self) -> tuple["RepairPlan.RepairStep", ...]:
        """Return steps whose prerequisites are all satisfied (in-degree zero).

        Returns
        -------
        tuple[RepairStep, ...]
            Steps with no dependency_order entries pointing to them.
        """
        has_predecessor = {after for _, after in self.dependency_order}
        return tuple(s for s in self.steps if s.step_id not in has_predecessor)

    def with_step(self, step: "RepairPlan.RepairStep") -> "RepairPlan":
        """Return a new plan with step appended.

        Parameters
        ----------
        step:
            The RepairStep to add.

        Returns
        -------
        RepairPlan
            A new plan with steps extended by step.
        """
        return replace(self, steps=self.steps + (step,))

    def without_step(self, step_id: str) -> "RepairPlan":
        """Return a new plan with the step identified by step_id removed.

        Parameters
        ----------
        step_id:
            The step_id of the step to remove.

        Returns
        -------
        RepairPlan
            A new plan with the named step and its edges removed.
        """
        new_steps = tuple(s for s in self.steps if s.step_id != step_id)
        new_deps = tuple(
            (b, a) for b, a in self.dependency_order
            if b != step_id and a != step_id
        )
        return replace(self, steps=new_steps, dependency_order=new_deps)

    def total_effort(self) -> str:
        """Summarise the aggregate effort across all steps.

        Returns
        -------
        str
            A string of the form "N steps: <effort distribution>".
        """
        if not self.steps:
            return "0 steps: no effort"
        effort_counts: dict[str, int] = {}
        for step in self.steps:
            effort_counts[step.estimated_effort] = (
                effort_counts.get(step.estimated_effort, 0) + 1
            )
        distribution = ", ".join(
            f"{count}x{effort}" for effort, count in sorted(effort_counts.items())
        )
        return f"{len(self.steps)} steps: {distribution}"

    def to_dict(self) -> dict[str, "JsonValue"]:
        """Serialise this plan to a plain-Python dict.

        Returns
        -------
        dict[str, JsonValue]
            A JSON-serialisable dict.
        """
        return {
            "plan_id": self.plan_id,
            "coordinate": self.coordinate,
            "steps": [s.to_dict() for s in self.steps],
            "dependency_order": [list(pair) for pair in self.dependency_order],
            "estimated_effort": self.estimated_effort,
            "confidence_score": self.confidence_score,
            "provenance": self.provenance.name,
            "metadata": [list(pair) for pair in self.metadata],
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "RepairPlan":
        """Deserialise a RepairPlan from a plain-Python dict.

        Parameters
        ----------
        payload:
            A dict as produced by to_dict.

        Returns
        -------
        RepairPlan
            The reconstructed plan.

        Raises
        ------
        KeyError
            If a required key is absent.
        """
        return cls(
            plan_id=payload.get("plan_id", uuid.uuid4().hex[:16]),
            coordinate=payload.get("coordinate", ""),
            steps=tuple(
                RepairPlan.RepairStep.from_dict(s)
                for s in payload.get("steps", [])
            ),
            dependency_order=tuple(
                (pair[0], pair[1]) for pair in payload.get("dependency_order", [])
            ),
            estimated_effort=payload.get("estimated_effort", "unknown"),
            confidence_score=float(payload.get("confidence_score", 0.0)),
            provenance=ProvenanceSource[payload.get("provenance", "SOLVER")],
            metadata=tuple(
                (pair[0], pair[1]) for pair in payload.get("metadata", [])
            ),
        )


# ---------------------------------------------------------------------------
# RepairFrontier
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class RepairFrontier:
    """The minimal set of coordinates whose sections must be replaced.

    In the sheaf-theoretic framework (Ch11 §11.4), the repair frontier is
    the minimal sub-covering of the coordinate space that:

    1. Contains every coordinate with a detected obstruction
       (obstruction_coordinates).
    2. Contains every coordinate targeted by at least one repair step
       (repair_coordinates).
    3. Is closed under the descent morphisms.

    Lattice operations
    ------------------
    * expand(new_coords)       -- upward closure: add coordinates.
    * contract(remove_coords)  -- downward pruning: remove coordinates.
    * union(other)             -- join with another frontier.
    * intersection(other)      -- meet with another frontier.
    * is_covered_by(other)     -- subset test.

    Minimality (Ch11 Thm 11.4)
    ---------------------------
    A frontier is *minimal* iff no proper sub-covering satisfies the closure
    conditions above.  The is_minimal field records whether minimality has
    been certified.

    Parameters
    ----------
    frontier_id:
        Unique identifier (hex UUID prefix).
    coordinates:
        The full set of coordinates in the frontier.
    obstruction_coordinates:
        Subset of coordinates where obstructions were detected.
    repair_coordinates:
        Subset of coordinates targeted by repair steps.
    descent_failures:
        Tuple of coordinate strings where descent conditions failed.
    is_minimal:
        Whether the frontier has been certified as minimal.
    coverage_score:
        Float in [0.0, 1.0] indicating coverage of the affected region.
    """

    frontier_id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])
    coordinates: frozenset[str] = frozenset()
    obstruction_coordinates: frozenset[str] = frozenset()
    repair_coordinates: frozenset[str] = frozenset()
    descent_failures: tuple[str, ...] = ()
    is_minimal: bool = False
    coverage_score: float = 0.0

    def expand(self, new_coords: frozenset[str]) -> "RepairFrontier":
        """Return a new frontier with new_coords added to coordinates.

        Parameters
        ----------
        new_coords:
            The frozenset of coordinate strings to add.

        Returns
        -------
        RepairFrontier
            A new frontier with coordinates = self.coordinates | new_coords.
        """
        return replace(self, coordinates=self.coordinates | new_coords)

    def contract(self, remove_coords: frozenset[str]) -> "RepairFrontier":
        """Return a new frontier with remove_coords removed from all sets.

        Parameters
        ----------
        remove_coords:
            The frozenset of coordinate strings to remove.

        Returns
        -------
        RepairFrontier
            A new frontier with the given coordinates removed from all sets.
        """
        return replace(
            self,
            coordinates=self.coordinates - remove_coords,
            obstruction_coordinates=self.obstruction_coordinates - remove_coords,
            repair_coordinates=self.repair_coordinates - remove_coords,
            descent_failures=tuple(
                c for c in self.descent_failures if c not in remove_coords
            ),
        )

    def is_covered_by(self, other: "RepairFrontier") -> bool:
        """Return True iff this frontier's coordinates are a subset of other's.

        Parameters
        ----------
        other:
            The frontier to compare against.

        Returns
        -------
        bool
            True if self.coordinates <= other.coordinates.
        """
        return self.coordinates <= other.coordinates

    def union(self, other: "RepairFrontier") -> "RepairFrontier":
        """Return the union of this frontier and other.

        The is_minimal flag is cleared because the union may not be minimal.

        Parameters
        ----------
        other:
            The frontier to union with.

        Returns
        -------
        RepairFrontier
            A new frontier with all coordinates from both frontiers.
        """
        return replace(
            self,
            frontier_id=uuid.uuid4().hex[:16],
            coordinates=self.coordinates | other.coordinates,
            obstruction_coordinates=(
                self.obstruction_coordinates | other.obstruction_coordinates
            ),
            repair_coordinates=self.repair_coordinates | other.repair_coordinates,
            descent_failures=tuple(
                sorted(set(self.descent_failures) | set(other.descent_failures))
            ),
            is_minimal=False,
        )

    def intersection(self, other: "RepairFrontier") -> "RepairFrontier":
        """Return the intersection of this frontier and other.

        Parameters
        ----------
        other:
            The frontier to intersect with.

        Returns
        -------
        RepairFrontier
            A new frontier with only coordinates present in both.
        """
        return replace(
            self,
            frontier_id=uuid.uuid4().hex[:16],
            coordinates=self.coordinates & other.coordinates,
            obstruction_coordinates=(
                self.obstruction_coordinates & other.obstruction_coordinates
            ),
            repair_coordinates=self.repair_coordinates & other.repair_coordinates,
            descent_failures=tuple(
                sorted(set(self.descent_failures) & set(other.descent_failures))
            ),
            is_minimal=False,
        )

    def contains_coordinate(self, coord: str) -> bool:
        """Return True iff coord is in the frontier's coordinate set.

        Parameters
        ----------
        coord:
            The coordinate string to check.

        Returns
        -------
        bool
            True if coord in self.coordinates.
        """
        return coord in self.coordinates

    def to_dict(self) -> dict[str, "JsonValue"]:
        """Serialise this frontier to a plain-Python dict.

        Returns
        -------
        dict[str, JsonValue]
            A JSON-serialisable dict.  frozensets serialised as sorted lists.
        """
        return {
            "frontier_id": self.frontier_id,
            "coordinates": sorted(self.coordinates),
            "obstruction_coordinates": sorted(self.obstruction_coordinates),
            "repair_coordinates": sorted(self.repair_coordinates),
            "descent_failures": list(self.descent_failures),
            "is_minimal": self.is_minimal,
            "coverage_score": self.coverage_score,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "RepairFrontier":
        """Deserialise a RepairFrontier from a plain-Python dict.

        Parameters
        ----------
        payload:
            A dict as produced by to_dict.

        Returns
        -------
        RepairFrontier
            The reconstructed frontier.
        """
        return cls(
            frontier_id=payload.get("frontier_id", uuid.uuid4().hex[:16]),
            coordinates=frozenset(payload.get("coordinates", [])),
            obstruction_coordinates=frozenset(
                payload.get("obstruction_coordinates", [])
            ),
            repair_coordinates=frozenset(payload.get("repair_coordinates", [])),
            descent_failures=tuple(payload.get("descent_failures", [])),
            is_minimal=payload.get("is_minimal", False),
            coverage_score=float(payload.get("coverage_score", 0.0)),
        )


# ---------------------------------------------------------------------------
# DebugSession
# ---------------------------------------------------------------------------

class DebugStatus(str, Enum):
    """Status values for a DebugSession.

    OPEN:
        The session is active and accepting new counterexamples and repair
        attempts.
    CONVERGED:
        The repair loop reached a fixpoint; all descent conditions pass.
    ABANDONED:
        The session was explicitly abandoned, e.g. due to a timeout.
    BLOCKED:
        The repair loop cannot make further progress without external help.
    """

    OPEN = "open"
    CONVERGED = "converged"
    ABANDONED = "abandoned"
    BLOCKED = "blocked"


@dataclass(frozen=True, slots=True)
class DebugSession:
    """A monotone accumulator for counterexamples and repair attempts.

    A DebugSession records the complete history of a single repair loop
    execution for one coordinate.  It accumulates:

    * Counterexamples (counterexamples), which grow monotonically -- no
      previously witnessed obstruction is ever retracted (Ch11 Thm 11.7:
      session_monotonicity).
    * Repair attempts (repair_attempts), one per iteration.
    * The current repair frontier (current_frontier).
    * An iteration counter (iteration_count).

    Lifecycle
    ---------
    Sessions begin in DebugStatus.OPEN and transition to:
      * CONVERGED when mark_converged() is called.
      * BLOCKED   when mark_blocked() is called.
      * ABANDONED via replace() externally.

    Parameters
    ----------
    session_id:
        Unique identifier (hex UUID prefix).
    coordinate:
        The sheaf coordinate this session targets.
    counterexamples:
        Monotonically growing tuple of CounterexampleRecord objects.
    repair_attempts:
        Tuple of RepairPlan objects, one per repair iteration.
    current_frontier:
        The most recently computed RepairFrontier, or None.
    iteration_count:
        Number of completed repair-loop iterations.
    status:
        Current DebugStatus.
    created_at:
        ISO-8601 timestamp of session creation.
    updated_at:
        ISO-8601 timestamp of the last update.
    """

    session_id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])
    coordinate: str = ""
    counterexamples: tuple[CounterexampleRecord, ...] = ()
    repair_attempts: tuple[RepairPlan, ...] = ()
    current_frontier: RepairFrontier | None = None
    iteration_count: int = 0
    status: DebugStatus = DebugStatus.OPEN
    created_at: str = ""
    updated_at: str = ""

    def add_counterexample(self, record: CounterexampleRecord) -> "DebugSession":
        """Return a new session with record appended to counterexamples.

        Parameters
        ----------
        record:
            The CounterexampleRecord to add.

        Returns
        -------
        DebugSession
            A new session with counterexamples extended by record and
            updated_at refreshed.
        """
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        return replace(
            self,
            counterexamples=self.counterexamples + (record,),
            updated_at=now,
        )

    def add_repair_attempt(self, plan: RepairPlan) -> "DebugSession":
        """Return a new session with plan appended to repair_attempts.

        Parameters
        ----------
        plan:
            The RepairPlan to record.

        Returns
        -------
        DebugSession
            A new session with repair_attempts extended by plan and
            updated_at refreshed.
        """
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        return replace(
            self,
            repair_attempts=self.repair_attempts + (plan,),
            updated_at=now,
        )

    def advance_iteration(self) -> "DebugSession":
        """Return a new session with iteration_count incremented by 1.

        Returns
        -------
        DebugSession
            A new session with iteration_count = self.iteration_count + 1
            and updated_at refreshed.
        """
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        return replace(
            self,
            iteration_count=self.iteration_count + 1,
            updated_at=now,
        )

    def mark_converged(self) -> "DebugSession":
        """Return a new session with status set to CONVERGED.

        Returns
        -------
        DebugSession
            A new session with status = DebugStatus.CONVERGED.
        """
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        return replace(self, status=DebugStatus.CONVERGED, updated_at=now)

    def mark_blocked(self, reason: str) -> "DebugSession":
        """Return a new session with status set to BLOCKED.

        Parameters
        ----------
        reason:
            A human-readable explanation of why the session is blocked.

        Returns
        -------
        DebugSession
            A new session with status = DebugStatus.BLOCKED.
        """
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        return replace(
            self,
            status=DebugStatus.BLOCKED,
            updated_at=now,
        )

    def is_active(self) -> bool:
        """Return True iff the session is still open and accepting work.

        Returns
        -------
        bool
            True if status == DebugStatus.OPEN.
        """
        return self.status == DebugStatus.OPEN

    def latest_counterexample(self) -> CounterexampleRecord | None:
        """Return the most recently added counterexample, or None.

        Returns
        -------
        CounterexampleRecord | None
            The last element of self.counterexamples, or None if empty.
        """
        if not self.counterexamples:
            return None
        return self.counterexamples[-1]

    def to_dict(self) -> dict[str, "JsonValue"]:
        """Serialise this session to a plain-Python dict.

        Returns
        -------
        dict[str, JsonValue]
            A JSON-serialisable dict with all fields.
        """
        return {
            "session_id": self.session_id,
            "coordinate": self.coordinate,
            "counterexamples": [c.to_dict() for c in self.counterexamples],
            "repair_attempts": [p.to_dict() for p in self.repair_attempts],
            "current_frontier": (
                self.current_frontier.to_dict()
                if self.current_frontier is not None
                else None
            ),
            "iteration_count": self.iteration_count,
            "status": self.status.value,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "DebugSession":
        """Deserialise a DebugSession from a plain-Python dict.

        Parameters
        ----------
        payload:
            A dict as produced by to_dict.

        Returns
        -------
        DebugSession
            The reconstructed session.

        Raises
        ------
        KeyError
            If a required key is absent.
        """
        raw_frontier = payload.get("current_frontier")
        frontier = (
            RepairFrontier.from_dict(raw_frontier)
            if raw_frontier is not None
            else None
        )
        return cls(
            session_id=payload.get("session_id", uuid.uuid4().hex[:16]),
            coordinate=payload.get("coordinate", ""),
            counterexamples=tuple(
                CounterexampleRecord.from_dict(c)
                for c in payload.get("counterexamples", [])
            ),
            repair_attempts=tuple(
                RepairPlan.from_dict(p) for p in payload.get("repair_attempts", [])
            ),
            current_frontier=frontier,
            iteration_count=int(payload.get("iteration_count", 0)),
            status=DebugStatus(payload.get("status", DebugStatus.OPEN.value)),
            created_at=payload.get("created_at", ""),
            updated_at=payload.get("updated_at", ""),
        )


# ---------------------------------------------------------------------------
# RepairValidator
# ---------------------------------------------------------------------------

class ValidationResult(str, Enum):
    """Outcome of a repair-plan or repair-step validation check.

    VALID:    All rules pass.
    INVALID:  At least one required rule fails.
    PARTIAL:  Some rules pass, some warnings (no hard failures).
    UNKNOWN:  Validation not performed.
    """

    VALID = "valid"
    INVALID = "invalid"
    PARTIAL = "partial"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class RepairValidator:
    """A rule-based checker for plan admissibility and descent satisfaction.

    The RepairValidator encapsulates the validation logic of Ch11 §11.7
    (descent_preservation, Thm 11.3) and §11.5 (repair_admissibility,
    Thm 11.2).  It holds:

    * A tuple of rule strings (validation_rules) that define the checks.
    * The result of the most recent validation run (result).
    * Lists of failures and warnings from the last run.

    Validation workflow
    -------------------
    1. Create a RepairValidator with coordinate and rules.
    2. Call validate_plan(plan) -> new validator with result/failures/warnings.
    3. Optionally call validate_step(step) for per-step checks.
    4. Call check_descent(session) to verify convergence.
    5. Call check_admissibility(plan) as a quick boolean check.

    Parameters
    ----------
    validator_id:
        Unique identifier (hex UUID prefix).
    coordinate:
        The sheaf coordinate being validated.
    validation_rules:
        Tuple of rule name strings.
    checked_at:
        ISO-8601 timestamp of the most recent run.
    result:
        The ValidationResult from the most recent run.
    failures:
        Tuple of failure description strings.
    warnings:
        Tuple of warning description strings.
    """

    validator_id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])
    coordinate: str = ""
    validation_rules: tuple[str, ...] = ()
    checked_at: str = ""
    result: ValidationResult = ValidationResult.UNKNOWN
    failures: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    def validate_plan(self, plan: RepairPlan) -> "RepairValidator":
        """Validate an entire RepairPlan and return an updated validator.

        Checks:
        1. Plan has at least one step.
        2. Plan is admissible (acyclic dependency graph).
        3. Every step has a non-empty target_coordinate.
        4. Every step's prerequisites reference existing step IDs.
        5. confidence_score is in [0.0, 1.0].

        Parameters
        ----------
        plan:
            The RepairPlan to validate.

        Returns
        -------
        RepairValidator
            A new validator with result, failures, and warnings populated.
        """
        failures: list[str] = []
        warnings: list[str] = []
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

        if not plan.steps:
            failures.append("Plan has no steps.")

        if not plan.is_admissible():
            failures.append(
                f"Plan {plan.plan_id!r} has a cyclic dependency graph."
            )

        step_ids = {s.step_id for s in plan.steps}
        for step in plan.steps:
            if not step.target_coordinate:
                failures.append(
                    f"Step {step.step_id!r} has an empty target_coordinate."
                )
            for prereq in step.prerequisites:
                if prereq not in step_ids:
                    failures.append(
                        f"Step {step.step_id!r} references unknown prerequisite"
                        f" {prereq!r}."
                    )

        if not (0.0 <= plan.confidence_score <= 1.0):
            warnings.append(
                f"Plan confidence_score {plan.confidence_score} is outside [0, 1]."
            )

        if failures:
            result = ValidationResult.INVALID
        elif warnings:
            result = ValidationResult.PARTIAL
        else:
            result = ValidationResult.VALID

        return replace(
            self,
            checked_at=now,
            result=result,
            failures=tuple(failures),
            warnings=tuple(warnings),
        )

    def validate_step(
        self, step: "RepairPlan.RepairStep"
    ) -> tuple[bool, str]:
        """Validate a single RepairStep and return a (passed, message) pair.

        Checks: non-empty step_id, action, and target_coordinate.

        Parameters
        ----------
        step:
            The RepairStep to validate.

        Returns
        -------
        tuple[bool, str]
            (True, "ok") if valid, or (False, reason) if not.
        """
        if not step.step_id:
            return (False, "Step has empty step_id.")
        if not step.action:
            return (False, f"Step {step.step_id!r} has empty action.")
        if not step.target_coordinate:
            return (False, f"Step {step.step_id!r} has empty target_coordinate.")
        return (True, "ok")

    def check_descent(self, session: DebugSession) -> bool:
        """Return True iff the debug session has converged.

        A session has *converged* iff its status is DebugStatus.CONVERGED.

        Parameters
        ----------
        session:
            The DebugSession to check.

        Returns
        -------
        bool
            True if session.status == DebugStatus.CONVERGED.
        """
        return session.status == DebugStatus.CONVERGED

    def check_admissibility(self, plan: RepairPlan) -> bool:
        """Return True iff plan is admissible.

        Delegates to plan.is_admissible().

        Parameters
        ----------
        plan:
            The RepairPlan to check.

        Returns
        -------
        bool
            True iff the plan's dependency graph is acyclic.
        """
        return plan.is_admissible()

    def add_rule(self, rule: str) -> "RepairValidator":
        """Return a new validator with rule appended to validation_rules.

        Parameters
        ----------
        rule:
            A rule name string to add.

        Returns
        -------
        RepairValidator
            New validator with validation_rules extended and result reset.
        """
        return replace(
            self,
            validation_rules=self.validation_rules + (rule,),
            result=ValidationResult.UNKNOWN,
        )

    def to_dict(self) -> dict[str, "JsonValue"]:
        """Serialise this validator to a plain-Python dict.

        Returns
        -------
        dict[str, JsonValue]
            A JSON-serialisable dict with all fields.
        """
        return {
            "validator_id": self.validator_id,
            "coordinate": self.coordinate,
            "validation_rules": list(self.validation_rules),
            "checked_at": self.checked_at,
            "result": self.result.value,
            "failures": list(self.failures),
            "warnings": list(self.warnings),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "RepairValidator":
        """Deserialise a RepairValidator from a plain-Python dict.

        Parameters
        ----------
        payload:
            A dict as produced by to_dict.

        Returns
        -------
        RepairValidator
            The reconstructed validator.
        """
        return cls(
            validator_id=payload.get("validator_id", uuid.uuid4().hex[:16]),
            coordinate=payload.get("coordinate", ""),
            validation_rules=tuple(payload.get("validation_rules", [])),
            checked_at=payload.get("checked_at", ""),
            result=ValidationResult(
                payload.get("result", ValidationResult.UNKNOWN.value)
            ),
            failures=tuple(payload.get("failures", [])),
            warnings=tuple(payload.get("warnings", [])),
        )


# ---------------------------------------------------------------------------
# Module exports
# ---------------------------------------------------------------------------

__all__ = [
    "CounterexampleRecord",
    "RepairPlan",
    "RepairFrontier",
    "DebugSession",
    "DebugStatus",
    "RepairValidator",
    "ValidationResult",
]
# copilot: end of repair_semantics models
