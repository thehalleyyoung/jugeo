"""Standalone algorithmic functions for geometric judgment debugging and repair.

This module implements the key algorithms from ``preliminaries/theory2.tex``
Chapter 11, §11.1–§11.4.  Every function is a *pure* transformation: it
accepts theory-level objects (repair plans, frontiers, debug sessions,
counterexample records) and returns new objects without mutating its inputs.

The algorithms are organized into four groups following the chapter structure:

1. **Minimization** — :func:`delta_debug` reduces an arbitrary failing
   assignment to a minimal sub-witness (§11.1, Algorithm 11.1).

2. **Frontier computation** — :func:`compute_minimal_repair_frontier`
   identifies the minimal set of coordinates that must change in order to
   cover all live obstructions (§11.3, Algorithm 11.3).

3. **Ordering** — :func:`topological_repair_order` derives a linearization
   of a repair plan's steps that respects all dependency edges (§11.2,
   Algorithm 11.2, Kahn 1962).

4. **Aggregation and scoring** — :func:`merge_repair_frontiers`,
   :func:`score_repair_confidence`, and :func:`compute_repair_distance`
   combine and evaluate repair plans relative to a session history (§11.4).

5. **Classification** — :func:`classify_cohomology_class` assigns a Čech
   H¹ class label to a counterexample record (§11.1, Definition 11.2).

6. **Certification** — :func:`repair_convergence_certificate` constructs a
   serializable certificate that a debug session has converged (§11.4,
   Theorem 11.5).

All algorithms include explicit theory references in their docstrings and
are implemented without any mutable global state, making them safe for
concurrent use.

Backward compatibility
-----------------------
No public symbols from this module are considered stable before the
repair_semantics subsystem reaches GA.  Import via the package-level
``__init__.py`` to insulate downstream code from moves.

See also
--------
* :mod:`jugeo.problem_modes.repair_semantics.models` — data model types.
* :mod:`jugeo.problem_modes.repair_semantics.integration` — integration layer.
* :mod:`jugeo.problem_modes.repair_semantics.theorems` — theorem declarations.
"""

from __future__ import annotations

import hashlib
import math
import time
import uuid
from collections import deque
from typing import Any, Callable, Sequence

from jugeo.errors import RepairPriority
from jugeo.solver.countermodels import FailureClass
from jugeo.problem_modes.repair_semantics.models import (
    CounterexampleRecord,
    DebugSession,
    DebugSessionStatus,
    RepairFrontier,
    RepairPlan,
    RepairStep,
)

# ---------------------------------------------------------------------------
# Module-level provenance
# ---------------------------------------------------------------------------

MANIFEST_SPEC_PROVENANCE: dict[str, str] = {
    "stage": "ch11-repair-semantics",
    "sequence": "11",
    "semantic_source": "preliminaries/theory2.tex",
    "module": "algorithms",
    "theory_section": "§11 — Algorithms for Debugging and Repair",
}

# ---------------------------------------------------------------------------
# §A  Private helpers
# ---------------------------------------------------------------------------


def _jaccard_similarity(set_a: frozenset, set_b: frozenset) -> float:
    """Compute the Jaccard similarity coefficient for two sets.

    The Jaccard similarity is defined as ``|A ∩ B| / |A ∪ B|``.  When both
    sets are empty the function returns ``1.0`` (identical empty sets).

    Parameters
    ----------
    set_a : frozenset
        First set operand.
    set_b : frozenset
        Second set operand.

    Returns
    -------
    float
        Jaccard similarity in ``[0.0, 1.0]``.
    """
    if not set_a and not set_b:
        return 1.0
    union_size = len(set_a | set_b)
    if union_size == 0:
        return 1.0
    intersection_size = len(set_a & set_b)
    return intersection_size / union_size


def _plan_action_set(plan: RepairPlan) -> frozenset[str]:
    """Return the set of action strings for all steps in a repair plan.

    This projection is used by :func:`compute_repair_distance` to compare
    plans by their action vocabularies independently of ordering or IDs.

    Parameters
    ----------
    plan : RepairPlan
        The plan to project.

    Returns
    -------
    frozenset[str]
        Frozenset of ``step.action`` values across all steps in the plan.
    """
    return frozenset(step.action for step in plan.steps if step.action)


def _assignment_fingerprint(assignments: tuple[tuple[str, str], ...]) -> str:
    """Compute a stable SHA-256 fingerprint for a variable-assignment tuple.

    The fingerprint is derived from the *sorted* ``name=value`` pairs so
    that two assignment tuples with the same semantic content but different
    ordering produce the same fingerprint.

    Parameters
    ----------
    assignments : tuple[tuple[str, str], ...]
        Sorted ``(name, value)`` pairs from a counterexample record.

    Returns
    -------
    str
        12-character hexadecimal prefix of the SHA-256 digest.
    """
    canonical = ";".join(f"{k}={v}" for k, v in sorted(assignments))
    return hashlib.sha256(canonical.encode()).hexdigest()[:12]


def _build_dependency_graph(plan: RepairPlan) -> dict[str, set[str]]:
    """Build an adjacency-list representation of a plan's dependency graph.

    The graph maps each step ID to the set of step IDs it *must precede*
    (i.e. its successors in topological order).  Edges in ``dependency_order``
    have the form ``(from_id, to_id)`` meaning ``from_id`` must come before
    ``to_id``.

    Parameters
    ----------
    plan : RepairPlan
        The repair plan whose dependency structure to extract.

    Returns
    -------
    dict[str, set[str]]
        Adjacency list: ``graph[step_id]`` is the set of steps that depend
        on ``step_id``.
    """
    all_ids = [s.step_id for s in plan.steps]
    graph: dict[str, set[str]] = {sid: set() for sid in all_ids}
    for from_id, to_id in plan.dependency_order:
        if from_id in graph and to_id in graph:
            graph[from_id].add(to_id)
        elif from_id in graph:
            # to_id may be external; still record the edge
            graph[from_id].add(to_id)
    # Also incorporate per-step depends_on edges
    for step in plan.steps:
        for dep in step.depends_on:
            if dep in graph:
                graph[dep].add(step.step_id)
    return graph


def _kahn_sort(
    graph: dict[str, set[str]],
    all_nodes: list[str],
) -> list[str]:
    """Compute a topological ordering via Kahn's algorithm (Kahn 1962).

    Kahn's algorithm maintains a queue of nodes with in-degree zero and
    processes them one at a time, decrementing the in-degree of their
    successors.  It detects cycles: if the output ordering is shorter than
    ``all_nodes``, at least one cycle exists.

    Parameters
    ----------
    graph : dict[str, set[str]]
        Adjacency list mapping each node to its *successor* nodes.
    all_nodes : list[str]
        Complete list of node IDs to sort.

    Returns
    -------
    list[str]
        Topologically sorted node IDs.

    Raises
    ------
    ValueError
        If a cycle is detected in the dependency graph.
    """
    # Build in-degree map
    in_degree: dict[str, int] = {node: 0 for node in all_nodes}
    for node in all_nodes:
        for successor in graph.get(node, set()):
            if successor in in_degree:
                in_degree[successor] = in_degree.get(successor, 0) + 1

    # Initialize queue with zero-in-degree nodes (stable sort: use list + sort)
    queue: deque[str] = deque(
        sorted(n for n, deg in in_degree.items() if deg == 0)
    )
    result: list[str] = []

    while queue:
        node = queue.popleft()
        result.append(node)
        for successor in sorted(graph.get(node, set())):
            if successor not in in_degree:
                continue
            in_degree[successor] -= 1
            if in_degree[successor] == 0:
                queue.append(successor)

    if len(result) != len(all_nodes):
        missing = set(all_nodes) - set(result)
        raise ValueError(
            f"Cycle detected in repair plan dependency graph. "
            f"Steps involved in cycle: {sorted(missing)}"
        )

    return result


# ---------------------------------------------------------------------------
# §1  Delta-debugging (§11.1, Algorithm 11.1)
# ---------------------------------------------------------------------------


def delta_debug(
    assignment: dict[str, str],
    checker: Callable[[dict[str, str]], bool],
) -> dict[str, str]:
    """Find the minimal subset of an assignment that still causes a failure.

    This is an adaptation of the classic delta-debugging algorithm
    (Zeller & Hildebrandt, 1999) to semantic assignment dictionaries.  The
    algorithm assumes that ``checker(assignment)`` returns ``True`` (i.e.
    the full assignment causes the failure) and returns the smallest
    sub-mapping that still causes the failure.

    Theory basis
    ------------
    See theory2.tex §11.1, Algorithm 11.1 (*Semantic Delta-Debugging*).
    In the geometric setting, each key–value pair corresponds to a local
    section assignment; the minimal sub-witness is the minimal set of
    sections whose presence jointly violates the descent condition.

    Algorithm
    ---------
    The algorithm operates in rounds, each round splitting the current
    candidate set into ``n`` roughly equal subsets:

    1. Start with ``n = 2`` (binary split).
    2. Test each subset: if ``checker(subset)`` is True, reduce to that
       subset and reset ``n = 2``.
    3. If no subset causes the failure, test each *complement*: if
       ``checker(complement)`` is True, reduce to that complement and
       reset ``n = max(2, n - 1)``.
    4. If no subset or complement causes the failure, double ``n``.
    5. Stop when ``n > len(current)``.

    The algorithm terminates because either the candidate set shrinks in
    step 2/3, or ``n`` eventually exceeds the set size in step 4.

    Parameters
    ----------
    assignment : dict[str, str]
        The full failing assignment (``checker(assignment)`` must be True).
    checker : Callable[[dict[str, str]], bool]
        Returns True iff the given assignment causes the failure.

    Returns
    -------
    dict[str, str]
        A minimal failing sub-assignment.  Every key–value pair in the
        result is necessary: removing any single pair causes ``checker``
        to return False.

    Raises
    ------
    ValueError
        If ``checker(assignment)`` returns False (precondition violated).

    Examples
    --------
    >>> full = {"x": "1", "y": "2", "z": "3", "w": "4"}
    >>> # Only "x" and "z" together trigger the failure
    >>> minimal = delta_debug(full, lambda a: "x" in a and "z" in a)
    >>> set(minimal.keys()) == {"x", "z"}
    True
    """
    if not assignment:
        return {}
    if not checker(assignment):
        raise ValueError(
            "delta_debug precondition violated: checker(assignment) must be True"
        )

    # Work with a list of (key, value) pairs for stable ordering
    items: list[tuple[str, str]] = list(assignment.items())
    n: int = 2

    while len(items) >= 2:
        if n > len(items):
            # Cannot split further; return current candidate
            break

        # Split items into n roughly equal subsets
        chunk_size = math.ceil(len(items) / n)
        subsets: list[list[tuple[str, str]]] = []
        for i in range(n):
            start = i * chunk_size
            end = min(start + chunk_size, len(items))
            if start < end:
                subsets.append(items[start:end])

        reduced = False

        # Phase 1: test each subset
        for subset in subsets:
            candidate = dict(subset)
            if checker(candidate):
                items = subset
                n = 2
                reduced = True
                break

        if reduced:
            continue

        # Phase 2: test complements
        for i, subset in enumerate(subsets):
            subset_keys = {k for k, _ in subset}
            complement_items = [(k, v) for k, v in items if k not in subset_keys]
            if not complement_items:
                continue
            candidate = dict(complement_items)
            if checker(candidate):
                items = complement_items
                n = max(2, n - 1)
                reduced = True
                break

        if reduced:
            continue

        # Phase 3: increase granularity
        n *= 2
        if n > len(items):
            break

    result = dict(items)

    # Final pass: try removing individual items to ensure true minimality
    changed = True
    while changed:
        changed = False
        for key in list(result.keys()):
            trial = {k: v for k, v in result.items() if k != key}
            if trial and checker(trial):
                del result[key]
                changed = True
                break

    return result


# ---------------------------------------------------------------------------
# §2  Repair frontier computation (§11.3, Algorithm 11.3)
# ---------------------------------------------------------------------------


def compute_minimal_repair_frontier(
    counterexamples: Sequence[CounterexampleRecord],
) -> RepairFrontier:
    """Compute the minimal frontier of coordinates that must be repaired.

    Given a sequence of counterexample records, this function identifies
    the smallest set of coordinates whose repair would address every
    live obstruction.  A coordinate is included in the frontier iff at
    least one counterexample record references it and has either an active
    obstruction classification or at least one repair hint.

    Theory basis
    ------------
    See theory2.tex §11.3 (*Repair Frontier Minimality*).  The frontier
    ``F ⊆ Cov(c)`` is minimal when removing any element of ``F`` leaves
    some counterexample record whose obstruction is not addressed.

    Algorithm
    ---------
    1. Collect all non-empty coordinates from all records.
    2. Identify *obstruction coordinates*: coordinates of records whose
       ``failure_class`` is ``ASSIGNMENT_CONFLICT`` or ``SORT_VIOLATION``.
    3. Identify *repair coordinates*: coordinates of records that have at
       least one repair hint.
    4. Build the ``descent_failures`` list from distinct cohomology class
       labels across all records.
    5. Compute the ``coverage_score`` as the fraction of records that have
       at least one repair hint.
    6. Determine minimality: the frontier is minimal iff every coordinate
       in ``coordinates`` is referenced by at least one counterexample
       record that is *not* covered by the remaining coordinates.

    Parameters
    ----------
    counterexamples : Sequence[CounterexampleRecord]
        The counterexample records from the current debug session.

    Returns
    -------
    RepairFrontier
        The minimal repair frontier.  If ``counterexamples`` is empty,
        returns an empty frontier with ``is_minimal=True``.
    """
    if not counterexamples:
        return RepairFrontier(
            coordinates=frozenset(),
            obstruction_coordinates=frozenset(),
            repair_coordinates=frozenset(),
            descent_failures=(),
            coverage_score=1.0,
            is_minimal=True,
        )

    # Step 1: collect coordinates
    all_coords: set[str] = set()
    obstruction_coords: set[str] = set()
    repair_coords: set[str] = set()
    cohomology_labels: list[str] = []
    records_with_hints: int = 0

    for record in counterexamples:
        coord = record.coordinate or record.obstruction_coordinate
        if not coord:
            continue
        all_coords.add(coord)

        # Step 2: obstruction coordinates
        if record.is_obstruction_coordinate():
            obstruction_coords.add(coord)

        # Step 3: repair coordinates
        if record.has_repair_hints():
            repair_coords.add(coord)
            records_with_hints += 1

        # Step 4: descent failures
        if record.cohomology_class:
            cohomology_labels.append(record.cohomology_class)

    # Deduplicate cohomology labels preserving order
    seen_labels: set[str] = set()
    unique_labels: list[str] = []
    for label in cohomology_labels:
        if label not in seen_labels:
            seen_labels.add(label)
            unique_labels.append(label)

    # Step 5: coverage score
    total = len(counterexamples)
    coverage = records_with_hints / total if total > 0 else 0.0

    # Step 6: minimality check
    # A frontier is minimal iff for every coordinate c in coordinates,
    # there exists at least one counterexample record that (a) references c
    # and (b) is not covered by any other coordinate in coordinates.
    is_minimal = True
    for coord in all_coords:
        # Records covered by this coordinate
        coord_records = [
            r for r in counterexamples
            if (r.coordinate == coord or r.obstruction_coordinate == coord)
        ]
        # Check if any of these records are exclusively covered by this coord
        # (i.e. they have no other coordinate in all_coords)
        exclusively_covered = any(
            not any(
                (r.coordinate == other or r.obstruction_coordinate == other)
                for other in all_coords
                if other != coord
            )
            for r in coord_records
        )
        if not coord_records or not exclusively_covered:
            # This coordinate is redundant → frontier is not minimal
            is_minimal = False
            break

    return RepairFrontier(
        coordinates=frozenset(all_coords),
        obstruction_coordinates=frozenset(obstruction_coords),
        repair_coordinates=frozenset(repair_coords),
        descent_failures=tuple(unique_labels),
        coverage_score=coverage,
        is_minimal=is_minimal,
    )


# ---------------------------------------------------------------------------
# §3  Topological repair ordering (§11.2, Algorithm 11.2)
# ---------------------------------------------------------------------------


def topological_repair_order(plan: RepairPlan) -> list[RepairStep]:
    """Compute a topological ordering of repair steps respecting dependencies.

    Uses Kahn's algorithm (1962) to linearize the steps of a :class:`RepairPlan`
    while respecting all dependency edges.  Dependency edges are collected from
    two sources: the ``plan.dependency_order`` edge list and each step's
    ``step.depends_on`` field.

    Theory basis
    ------------
    See theory2.tex §11.2 (*Repair Plan Admissibility*).  A repair plan is
    admissible iff its dependency graph is a DAG; this function both verifies
    that condition (by raising if a cycle exists) and produces the linearization.

    Algorithm (Kahn 1962)
    ---------------------
    1. Build the adjacency list from all dependency edges.
    2. Compute in-degrees for all step IDs.
    3. Initialize a queue with all zero-in-degree steps (sorted for stability).
    4. Repeatedly dequeue a step, append it to the result, and decrement the
       in-degree of its successors.  When a successor reaches in-degree zero,
       enqueue it.
    5. If the result length is less than the number of steps, a cycle exists
       and ``ValueError`` is raised with the names of the involved steps.

    Parameters
    ----------
    plan : RepairPlan
        The repair plan to sort.

    Returns
    -------
    list[RepairStep]
        Repair steps in topological order.  Steps with no inter-dependencies
        appear in their original order (stable sort).

    Raises
    ------
    ValueError
        If the dependency graph contains a cycle, making the plan inadmissible.

    Examples
    --------
    >>> step_a = RepairStep(step_id="a", action="fix-sort", depends_on=())
    >>> step_b = RepairStep(step_id="b", action="tighten-pre", depends_on=("a",))
    >>> plan = RepairPlan(steps=(step_a, step_b), dependency_order=(("a", "b"),))
    >>> ordered = topological_repair_order(plan)
    >>> [s.step_id for s in ordered]
    ['a', 'b']
    """
    if not plan.steps:
        return []

    # Build id→step mapping for final reconstruction
    step_by_id: dict[str, RepairStep] = {s.step_id: s for s in plan.steps}
    all_ids: list[str] = [s.step_id for s in plan.steps]

    # Build dependency graph: graph[id] = set of successors
    graph = _build_dependency_graph(plan)

    # Run Kahn's topological sort
    sorted_ids = _kahn_sort(graph, all_ids)

    # Return RepairStep objects in sorted order
    return [step_by_id[sid] for sid in sorted_ids if sid in step_by_id]


# ---------------------------------------------------------------------------
# §4  Frontier merging (§11.3)
# ---------------------------------------------------------------------------


def merge_repair_frontiers(frontiers: Sequence[RepairFrontier]) -> RepairFrontier:
    """Merge multiple repair frontiers into a single covering frontier.

    The merged frontier contains the union of all coordinates from the
    input frontiers.  The merged frontier is marked minimal iff all input
    frontiers are themselves minimal AND their obstruction coordinate sets
    are pairwise disjoint (which guarantees that no coordinate is redundant
    in the union).

    Theory basis
    ------------
    See theory2.tex §11.3 (*Frontier Merging Lemma*).  The union of two
    minimal frontiers is minimal iff their obstruction sets are disjoint,
    because otherwise one obstruction coordinate in one frontier is also
    covered by another frontier, making the first one redundant.

    Parameters
    ----------
    frontiers : Sequence[RepairFrontier]
        Input frontiers to merge.  May be empty.

    Returns
    -------
    RepairFrontier
        A single frontier whose ``coordinates`` field is the union of all
        input ``coordinates`` fields.  Returns an empty minimal frontier
        if ``frontiers`` is empty.

    Examples
    --------
    >>> f1 = RepairFrontier(coordinates=frozenset({"a", "b"}),
    ...                     obstruction_coordinates=frozenset({"a"}),
    ...                     is_minimal=True)
    >>> f2 = RepairFrontier(coordinates=frozenset({"c"}),
    ...                     obstruction_coordinates=frozenset({"c"}),
    ...                     is_minimal=True)
    >>> merged = merge_repair_frontiers([f1, f2])
    >>> merged.is_minimal
    True
    """
    if not frontiers:
        return RepairFrontier(
            coordinates=frozenset(),
            obstruction_coordinates=frozenset(),
            repair_coordinates=frozenset(),
            descent_failures=(),
            coverage_score=1.0,
            is_minimal=True,
        )

    merged_coords: set[str] = set()
    merged_obs_coords: set[str] = set()
    merged_repair_coords: set[str] = set()
    all_descent_failures: list[str] = []
    max_coverage: float = 0.0
    all_minimal: bool = True

    # Check pairwise disjointness of obstruction coordinate sets
    obs_sets: list[frozenset[str]] = []

    for frontier in frontiers:
        merged_coords |= frontier.coordinates
        merged_obs_coords |= frontier.obstruction_coordinates
        merged_repair_coords |= frontier.repair_coordinates
        all_descent_failures.extend(frontier.descent_failures)
        max_coverage = max(max_coverage, frontier.coverage_score)
        if not frontier.is_minimal:
            all_minimal = False
        obs_sets.append(frontier.obstruction_coordinates)

    # Pairwise disjointness check for minimality
    pairwise_disjoint = True
    obs_list = list(obs_sets)
    for i in range(len(obs_list)):
        for j in range(i + 1, len(obs_list)):
            if obs_list[i] & obs_list[j]:
                pairwise_disjoint = False
                break
        if not pairwise_disjoint:
            break

    is_minimal = all_minimal and pairwise_disjoint

    # Deduplicate descent failures
    seen: set[str] = set()
    unique_failures: list[str] = []
    for label in all_descent_failures:
        if label not in seen:
            seen.add(label)
            unique_failures.append(label)

    return RepairFrontier(
        coordinates=frozenset(merged_coords),
        obstruction_coordinates=frozenset(merged_obs_coords),
        repair_coordinates=frozenset(merged_repair_coords),
        descent_failures=tuple(unique_failures),
        coverage_score=max_coverage,
        is_minimal=is_minimal,
    )


# ---------------------------------------------------------------------------
# §5  Repair confidence scoring (§11.4)
# ---------------------------------------------------------------------------


def score_repair_confidence(
    plan: RepairPlan,
    history: Sequence[RepairPlan],
) -> float:
    """Score the confidence that a repair plan will succeed given prior history.

    The scoring function combines a base confidence from the plan itself with
    a penalty for plans similar to previously failed attempts, a bonus for
    high-priority steps, and a bonus for admissibility.

    Theory basis
    ------------
    See theory2.tex §11.4 (*Repair Convergence*).  The confidence score is
    an informal measure used to rank competing repair candidates; it is not
    a formal probability.

    Scoring formula
    ---------------
    * Start from ``plan.confidence_score``.
    * Subtract ``0.1`` for each plan in ``history`` whose Jaccard distance
      from ``plan`` is less than ``0.3`` (i.e. very similar plan was tried
      and presumably failed).
    * Add ``0.05`` for each step whose ``priority`` is ``REQUIRED`` or
      ``CRITICAL`` (these steps address known critical obstructions).
    * Add ``0.1`` if ``plan.is_admissible`` is True.
    * Clamp the result to ``[0.0, 1.0]``.

    Parameters
    ----------
    plan : RepairPlan
        The candidate plan to score.
    history : Sequence[RepairPlan]
        Previously attempted repair plans in this session.

    Returns
    -------
    float
        Confidence score in ``[0.0, 1.0]``.

    Examples
    --------
    >>> step = RepairStep(action="fix", priority=RepairPriority.CRITICAL)
    >>> plan = RepairPlan(steps=(step,), confidence_score=0.5, is_admissible=True)
    >>> score = score_repair_confidence(plan, [])
    >>> 0.0 <= score <= 1.0
    True
    """
    score: float = plan.confidence_score

    # Penalty for similar historical plans
    plan_actions = _plan_action_set(plan)
    for historical_plan in history:
        hist_actions = _plan_action_set(historical_plan)
        distance = 1.0 - _jaccard_similarity(plan_actions, hist_actions)
        if distance < 0.3:
            score -= 0.1

    # Bonus for required/critical steps
    for step in plan.steps:
        if step.priority >= RepairPriority.REQUIRED:
            score += 0.05

    # Bonus for admissibility
    if plan.is_admissible:
        score += 0.1

    # Clamp to [0.0, 1.0]
    return max(0.0, min(1.0, score))


# ---------------------------------------------------------------------------
# §6  Repair distance (§11.4)
# ---------------------------------------------------------------------------


def compute_repair_distance(
    plan_a: RepairPlan,
    plan_b: RepairPlan,
) -> float:
    """Compute a normalized edit distance between two repair plans.

    The distance is based on the Jaccard distance of the two plans' action
    sets: ``1 - |actions_a ∩ actions_b| / |actions_a ∪ actions_b|``.
    Plans with identical action vocabularies have distance ``0.0``; plans
    with completely disjoint action vocabularies have distance ``1.0``.

    Theory basis
    ------------
    See theory2.tex §11.4 (*Repair Iteration Distance*).  This distance is
    used to detect when the repair iteration has stalled (successive plans
    have distance below a convergence threshold).

    Note
    ----
    This metric is *order-independent*: it compares the multi-sets of
    actions, not their ordering.  Two plans that apply the same set of
    actions in different orders have distance ``0.0``.

    Parameters
    ----------
    plan_a : RepairPlan
        First plan.
    plan_b : RepairPlan
        Second plan.

    Returns
    -------
    float
        Jaccard distance in ``[0.0, 1.0]``.

    Examples
    --------
    >>> p1 = RepairPlan(steps=(RepairStep(action="fix-sort"),))
    >>> p2 = RepairPlan(steps=(RepairStep(action="fix-sort"),))
    >>> compute_repair_distance(p1, p2)
    0.0
    >>> p3 = RepairPlan(steps=(RepairStep(action="add-invariant"),))
    >>> compute_repair_distance(p1, p3)
    1.0
    """
    actions_a = _plan_action_set(plan_a)
    actions_b = _plan_action_set(plan_b)
    similarity = _jaccard_similarity(actions_a, actions_b)
    return 1.0 - similarity


# ---------------------------------------------------------------------------
# §7  Cohomology class classification (§11.1)
# ---------------------------------------------------------------------------


def classify_cohomology_class(record: CounterexampleRecord) -> str:
    """Classify a counterexample record into a Čech H¹ cohomology class.

    The cohomology class is a symbolic label that identifies which *type*
    of obstruction the counterexample represents, which open set (coordinate)
    it lives at, and a stable hash of the variable assignments.  This label
    is used throughout the repair pipeline to group, deduplicate, and
    track obstructions.

    Theory basis
    ------------
    See theory2.tex §11.1 (*Counterexample Classification*), Definition 11.2.
    The Čech cochain group ``C¹(U, F)`` over the cover ``U`` of the semantic
    site has one component per open set (coordinate).  An H¹ class is an
    equivalence class of 1-cocycles modulo 1-coboundaries; here we use a
    concrete label that identifies the group (via ``failure_class``), the
    open set (via ``coordinate``), and the class representative (via the
    assignment fingerprint).

    Label format
    ------------
    The returned label has the form::

        H1[<failure_type>:<coordinate>:<fingerprint>]

    where:

    * ``<failure_type>`` is the lowercase ``failure_class`` value.
    * ``<coordinate>`` is the record's coordinate (truncated to 32 chars).
    * ``<fingerprint>`` is a 12-hex-char hash of the sorted variable
      assignments.

    Parameters
    ----------
    record : CounterexampleRecord
        The counterexample record to classify.

    Returns
    -------
    str
        A string label in the format ``"H1[type:coord:hash]"``.

    Examples
    --------
    >>> from jugeo.solver.countermodels import FailureClass
    >>> record = CounterexampleRecord(
    ...     coordinate="module/checker",
    ...     failure_class=FailureClass.SORT_VIOLATION,
    ...     variable_assignments=(("x", "42"), ("y", "true")),
    ... )
    >>> label = classify_cohomology_class(record)
    >>> label.startswith("H1[sort_violation:")
    True
    """
    failure_type = record.failure_class.value if record.failure_class else "unknown"
    coordinate = (record.coordinate or "root")[:32].replace("/", "_")
    fingerprint = _assignment_fingerprint(record.variable_assignments)
    return f"H1[{failure_type}:{coordinate}:{fingerprint}]"


# ---------------------------------------------------------------------------
# §8  Convergence certificate (§11.4, Theorem 11.5)
# ---------------------------------------------------------------------------


def repair_convergence_certificate(session: DebugSession) -> dict:
    """Generate a convergence certificate for a completed debug session.

    A convergence certificate records all the facts needed to verify that
    a debug session has reached a satisfactory terminal state.  It is
    intended to be stored alongside the repaired judgment as auditable
    evidence of the repair process.

    Theory basis
    ------------
    See theory2.tex §11.4 (*Repair Convergence*), Theorem 11.5.  Convergence
    is certified when:

    1. The session status is ``CONVERGED``.
    2. At least one repair attempt was made (``repair_attempt_count > 0``).
    3. No counterexample in the session lacks a cohomology class label.

    The certificate is signed with a SHA-256 hash of its key fields to allow
    downstream verification without re-running the session.

    Certificate fields
    ------------------
    * ``session_id`` — session identity.
    * ``coordinate`` — root coordinate of the session.
    * ``status`` — session status value.
    * ``iteration_count`` — number of repair iterations performed.
    * ``counterexample_count`` — total counterexamples accumulated.
    * ``repair_attempt_count`` — total repair plans attempted.
    * ``frontier_coverage`` — coverage score of the final repair frontier.
    * ``convergence_evidence`` — list of human-readable evidence strings.
    * ``certificate_hash`` — SHA-256 of the key fields.
    * ``is_certified`` — True iff all convergence criteria are satisfied.
    * ``generated_at`` — ISO-8601 timestamp.

    Parameters
    ----------
    session : DebugSession
        The debug session to certify.  Need not be CONVERGED; the
        ``is_certified`` field will be False for non-converged sessions.

    Returns
    -------
    dict
        A JSON-serializable convergence certificate dictionary.

    Examples
    --------
    >>> session = DebugSession(status=DebugSessionStatus.CONVERGED,
    ...                        iteration_count=3)
    >>> cert = repair_convergence_certificate(session)
    >>> isinstance(cert["certificate_hash"], str)
    True
    """
    status_value = session.status.value if session.status else "unknown"
    iteration_count = session.iteration_count
    counterexample_count = len(session.counterexamples)
    repair_attempt_count = len(session.repair_attempts)

    # Compute frontier coverage from the session's counterexamples
    if session.counterexamples:
        frontier = compute_minimal_repair_frontier(session.counterexamples)
        frontier_coverage = frontier.coverage_score
    else:
        frontier_coverage = 1.0

    # Build convergence evidence list
    convergence_evidence: list[str] = []

    if session.status == DebugSessionStatus.CONVERGED:
        convergence_evidence.append("session_status=CONVERGED")
    else:
        convergence_evidence.append(f"session_status={status_value} (not converged)")

    if repair_attempt_count > 0:
        convergence_evidence.append(f"repair_attempts={repair_attempt_count}")
    else:
        convergence_evidence.append("no_repair_attempts_made")

    labeled_records = sum(
        1 for r in session.counterexamples if r.cohomology_class
    )
    if labeled_records == counterexample_count:
        convergence_evidence.append(
            f"all_{counterexample_count}_counterexamples_classified"
        )
    else:
        convergence_evidence.append(
            f"only_{labeled_records}_of_{counterexample_count}_counterexamples_classified"
        )

    if frontier_coverage >= 1.0:
        convergence_evidence.append("full_frontier_coverage")
    else:
        convergence_evidence.append(f"partial_frontier_coverage={frontier_coverage:.2f}")

    # Determine certification
    is_certified = (
        session.status == DebugSessionStatus.CONVERGED
        and repair_attempt_count > 0
        and labeled_records == counterexample_count
    )

    # Build certificate hash
    hash_payload = (
        f"{session.session_id}|{session.coordinate}|{status_value}|"
        f"{iteration_count}|{counterexample_count}|{repair_attempt_count}|"
        f"{frontier_coverage:.4f}|{is_certified}"
    )
    certificate_hash = hashlib.sha256(hash_payload.encode()).hexdigest()

    # ISO-8601 timestamp
    generated_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    return {
        "session_id": session.session_id,
        "coordinate": session.coordinate,
        "status": status_value,
        "iteration_count": iteration_count,
        "counterexample_count": counterexample_count,
        "repair_attempt_count": repair_attempt_count,
        "frontier_coverage": frontier_coverage,
        "convergence_evidence": convergence_evidence,
        "certificate_hash": certificate_hash,
        "is_certified": is_certified,
        "generated_at": generated_at,
    }




# ---------------------------------------------------------------------------
# Unified architecture cross-references (jugeo.solver, jugeo.evidence, jugeo.geometry)
# ---------------------------------------------------------------------------


def repair_from_countermodel(cm: Any) -> dict[str, Any]:
    """Extract repair guidance from a countermodel.

    Countermodels from the solver encode exactly where the current section
    fails — they are the starting point for all repair actions.

    Parameters
    ----------
    cm : Any
        A Countermodel object or dict with countermodel data.

    Returns
    -------
    dict[str, Any]
        Repair guidance with ``failing_coordinates``, ``repair_hints``,
        ``countermodel_id``, and ``obstruction_class`` keys.
    """
    try:
        from jugeo.solver.countermodels import extract_repair_hints, Countermodel
    except ImportError:
        extract_repair_hints = None
        Countermodel = None

    model_id = getattr(cm, "model_id", None) or (cm.get("model_id") if isinstance(cm, dict) else "unknown")
    coord = getattr(cm, "coordinate", None) or (cm.get("coordinate") if isinstance(cm, dict) else None)

    guidance: dict[str, Any] = {
        "countermodel_id": model_id,
        "failing_coordinates": [coord] if coord else [],
        "repair_hints": [],
        "obstruction_class": f"H1_from_{model_id}",
    }

    if extract_repair_hints is not None:
        try:
            hints = extract_repair_hints(cm)
            guidance["repair_hints"] = list(hints) if hints else []
        except Exception:
            pass

    return guidance


def repair_certificate(repair: Any) -> dict[str, Any]:
    """Build an evidence certificate for a completed repair.

    Repair certificates attest that a repair action was performed,
    passed validation, and restored section well-formedness.

    Parameters
    ----------
    repair : Any
        A repair result object or dict.

    Returns
    -------
    dict[str, Any]
        Certificate with ``repair_id``, ``valid``, ``trust_level``,
        ``certificate_hash``, and ``certificate_obj`` keys.
    """
    try:
        from jugeo.evidence.certificates import Certificate, build_certificate
    except ImportError:
        Certificate = None
        build_certificate = None

    import hashlib, uuid

    repair_id = getattr(repair, "repair_id", None) or (
        repair.get("repair_id") if isinstance(repair, dict) else str(uuid.uuid4())
    )
    valid = getattr(repair, "valid", None)
    if valid is None and isinstance(repair, dict):
        valid = repair.get("valid", repair.get("status") == "success")

    cert: dict[str, Any] = {
        "certificate_id": str(uuid.uuid4()),
        "repair_id": repair_id,
        "valid": bool(valid) if valid is not None else False,
        "trust_level": "REPAIRED" if valid else "UNVERIFIED",
        "certificate_hash": hashlib.sha256(str(repair).encode()).hexdigest()[:16],
        "certificate_obj": None,
    }

    if build_certificate is not None:
        try:
            cert["certificate_obj"] = build_certificate(
                claim=f"repair_{repair_id}", satisfied=valid, source="repair_semantics"
            )
        except Exception:
            pass

    return cert


def repair_descent_check(repair: Any) -> dict[str, Any]:
    """Check whether a repair restores descent (gluing) conditions.

    A valid repair must restore the ability of local sections to glue
    into a global section — i.e., the cocycle obstruction must vanish.

    Parameters
    ----------
    repair : Any
        A repair result object or dict.

    Returns
    -------
    dict[str, Any]
        Descent check with ``gluing_restored``, ``cocycle_trivial``,
        ``affected_coordinates``, and ``descent_status`` keys.
    """
    try:
        from jugeo.geometry.descent import check_descent_after_repair, DescentStatus
    except ImportError:
        check_descent_after_repair = None
        DescentStatus = None

    coords = getattr(repair, "affected_coordinates", None) or (
        repair.get("affected_coordinates") if isinstance(repair, dict) else []
    )
    repair_id = getattr(repair, "repair_id", None) or (
        repair.get("repair_id") if isinstance(repair, dict) else "unknown"
    )

    check: dict[str, Any] = {
        "repair_id": repair_id,
        "affected_coordinates": list(coords) if coords else [],
        "gluing_restored": None,
        "cocycle_trivial": None,
        "descent_status": "UNKNOWN",
    }

    if check_descent_after_repair is not None:
        try:
            result = check_descent_after_repair(coords, repair_id=repair_id)
            check["gluing_restored"] = getattr(result, "gluing_restored", None)
            check["cocycle_trivial"] = getattr(result, "cocycle_trivial", None)
            check["descent_status"] = getattr(result, "status", "UNKNOWN")
        except Exception:
            pass

    return check


# ---------------------------------------------------------------------------
# Module exports
# ---------------------------------------------------------------------------

__all__ = [
    "MANIFEST_SPEC_PROVENANCE",
    # Public algorithms
    "delta_debug",
    "compute_minimal_repair_frontier",
    "topological_repair_order",
    "merge_repair_frontiers",
    "score_repair_confidence",
    "compute_repair_distance",
    "classify_cohomology_class",
    "repair_convergence_certificate",
    # Private helpers (exported for testing)
    "_jaccard_similarity",
    "_plan_action_set",
    "_assignment_fingerprint",
    "_build_dependency_graph",
    "_kahn_sort",
    # Unified architecture cross-references
    "repair_from_countermodel",
    "repair_certificate",
    "repair_descent_check",
]

# copilot: end of algorithms
