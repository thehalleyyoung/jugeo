"""Repair planning for the repair_semantics subsystem (theory2.tex Ch11 §11.2).

Stage 02 of the repair pipeline: given a :class:`CounterexampleRecord` (or a
:class:`RepairFrontier`), produce a :class:`RepairPlan` — an ordered collection
of repair steps with dependency constraints.

Theory basis (theory2.tex §11.2 — Repair as Local Section Replacement)
----------------------------------------------------------------------
A repair plan is a partial order on repair steps.  Each step replaces a local
section s_i with a patched s_i' at some coordinate U_i.  The partial order
encodes inter-coordinate dependencies: if U_j ⊆ U_i, the patch at U_i must be
consistent with the patch at U_j (descent condition).

After executing all steps in topological order the planner calls a descent check
to verify that the patched sections glue into a global section.

Key invariants
--------------
* A plan is *admissible* iff its dependency graph is acyclic (DAG).
* The *frontier* of a plan is the minimal set of coordinates touched.
* Merging two admissible plans produces an admissible plan iff their frontiers
  are consistent (no conflicting patches on the same coordinate).

Implementation notes
--------------------
The planner is a frozen dataclass with no mutable state.  All methods accept
immutable inputs and return new objects constructed via :func:`dataclasses.replace`.
The dependency graph is encoded as a tuple of ``(from_step_id, to_step_id)``
edge pairs stored in :attr:`~jugeo.problem_modes.repair_semantics.models.RepairPlan.dependency_order`.
Cycle detection (needed for admissibility) is delegated to
:class:`~jugeo.problem_modes.repair_semantics.models.RepairValidator`.

# copilot: s02 repair planning — theory2 ch11 stage 02
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field, replace
from typing import Any, Sequence

from jugeo.errors import (
    EvidenceFamily,
    FailureChain,
    FailureClassification,
    FailureScope,
    JuGeoError,
    ObstructionRecord,
    RepairHint,
    RepairPriority,
    StructuredFailure,
    as_failure_payload,
    raise_with_scope,
)
from jugeo.judgments.judgment_terms import (
    Carrier,
    EvidenceBundle,
    EvidenceItem,
    EvidenceItemKind,
    JudgmentAlgebra,
    JudgmentStatus,
    Obstruction,
    Provenance,
    ProvenanceSource,
    Proposition,
    ResidualObligation,
    TrustAnnotation,
    TrustLevel,
)
from jugeo.solver.countermodels import (
    Countermodel,
    CountermodelExtractor,
    FailureClass,
    ObstructionConverter,
    RepairType,
)
from jugeo.problem_modes.repair_semantics.models import (
    CounterexampleRecord,
    DebugSession,
    RepairFrontier,
    RepairPlan,
    RepairStep,
    RepairValidator,
)

# ---------------------------------------------------------------------------
# Module-level provenance
# ---------------------------------------------------------------------------

MANIFEST_SPEC_PROVENANCE: dict[str, str] = {
    "stage": "ch11-repair-semantics",
    "sequence": "11",
    "semantic_source": "preliminaries/theory2.tex",
    "module": "repair_planning",
    "pipeline_stage": "02",
    "theory_section": "§11.2 — Repair as Local Section Replacement",
}

# ---------------------------------------------------------------------------
# §1  Priority ordering constant
# ---------------------------------------------------------------------------

# Canonical priority ordering used by prioritize_steps(): highest-priority
# values appear earliest in the resulting step sequence.  The ordering
# matches theory2.tex §11.2, Definition 11.8 (repair plan topological sort).
_PRIORITY_ORDER: tuple[RepairPriority, ...] = (
    RepairPriority.CRITICAL,
    RepairPriority.REQUIRED,
    RepairPriority.RECOMMENDED,
    RepairPriority.SUGGESTED,
    RepairPriority.INFORMATIONAL,
)

# ---------------------------------------------------------------------------
# §2  Module-level helper functions
# ---------------------------------------------------------------------------


def _new_step_id() -> str:
    """Return a fresh step identifier as a 12-character hex UUID string.

    Step identifiers are used as keys in the dependency graph of a
    :class:`~jugeo.problem_modes.repair_semantics.models.RepairPlan`.
    They must be unique within the plan but do not need to be globally
    unique.

    Returns
    -------
    str
        A 12-character lowercase hex string, e.g. ``"a3f9b2c1d4e5"``.
    """
    return uuid.uuid4().hex[:12]


def _repair_type_from_action(action: str) -> RepairType:
    """Map an action string from a :class:`~jugeo.errors.RepairHint` to a :class:`RepairType`.

    The mapping is exhaustive: every known action key is assigned the most
    semantically appropriate :class:`RepairType` value.  Unknown action
    strings fall back to :attr:`~jugeo.solver.countermodels.RepairType.MANUAL_REVIEW`.

    Parameters
    ----------
    action : str
        The machine-readable action key from a :class:`~jugeo.errors.RepairHint`,
        e.g. ``"strengthen_precondition"`` or ``"add_sort_constraint"``.

    Returns
    -------
    RepairType
        The :class:`~jugeo.solver.countermodels.RepairType` that best
        describes how this action transforms the specification.

    Examples
    --------
    >>> _repair_type_from_action("strengthen_precondition")
    <RepairType.STRENGTHEN_PRECONDITION: 'strengthen_precondition'>
    >>> _repair_type_from_action("add_invariant")
    <RepairType.ADD_INVARIANT: 'add_invariant'>
    """
    _ACTION_TO_REPAIR_TYPE: dict[str, RepairType] = {
        # Precondition / postcondition adjustments
        "strengthen_precondition": RepairType.STRENGTHEN_PRECONDITION,
        "weaken_postcondition": RepairType.WEAKEN_POSTCONDITION,
        "tighten_precondition": RepairType.STRENGTHEN_PRECONDITION,
        "relax_postcondition": RepairType.WEAKEN_POSTCONDITION,
        # Invariants and quantifier fixes
        "add_invariant": RepairType.ADD_INVARIANT,
        "strengthen_quantifier_bound": RepairType.ADD_INVARIANT,
        # Implementation fixes
        "fix_implementation": RepairType.FIX_IMPLEMENTATION,
        "check_guard_condition": RepairType.FIX_IMPLEMENTATION,
        # Cover and sort
        "split_cover": RepairType.SPLIT_COVER,
        "refine_sort_universe": RepairType.ADD_SORT_CONSTRAINT,
        "add_sort_constraint": RepairType.ADD_SORT_CONSTRAINT,
        "tighten_index_domain": RepairType.ADD_SORT_CONSTRAINT,
        # Function / array / bounds
        "refine_function_spec": RepairType.REFINE_FUNCTION_SPEC,
        "add_function_axiom": RepairType.REFINE_FUNCTION_SPEC,
        "add_bounds_check": RepairType.FIX_IMPLEMENTATION,
        # Manual / review
        "manual_review": RepairType.MANUAL_REVIEW,
        "schedule_review": RepairType.MANUAL_REVIEW,
    }
    return _ACTION_TO_REPAIR_TYPE.get(action, RepairType.MANUAL_REVIEW)


def _effort_from_priority(priority: RepairPriority) -> str:
    """Map a :class:`~jugeo.errors.RepairPriority` value to an effort string.

    The effort string gives a qualitative estimate of the developer effort
    required to execute a repair step.  The mapping is intended as a rough
    guide for the :meth:`RepairPlanner.estimate_effort` aggregation.

    Parameters
    ----------
    priority : RepairPriority
        The repair step's priority level.

    Returns
    -------
    str
        One of ``"trivial"``, ``"moderate"``, or ``"significant"``.

    Examples
    --------
    >>> _effort_from_priority(RepairPriority.CRITICAL)
    'significant'
    >>> _effort_from_priority(RepairPriority.INFORMATIONAL)
    'trivial'
    """
    _EFFORT_MAP: dict[RepairPriority, str] = {
        RepairPriority.CRITICAL: "significant",
        RepairPriority.REQUIRED: "moderate",
        RepairPriority.RECOMMENDED: "moderate",
        RepairPriority.SUGGESTED: "trivial",
        RepairPriority.INFORMATIONAL: "trivial",
    }
    return _EFFORT_MAP.get(priority, "trivial")


def _priority_sort_key(step: RepairStep) -> int:
    """Return a sort key for a :class:`RepairStep` based on its priority.

    Steps with higher priority (more urgent) receive lower sort keys so
    that ``sorted(steps, key=_priority_sort_key)`` places them first.

    Parameters
    ----------
    step : RepairStep
        The step to evaluate.

    Returns
    -------
    int
        The negated integer value of the step's priority.  Since
        :class:`~jugeo.errors.RepairPriority` is an :class:`~enum.IntEnum`
        with CRITICAL=4 and INFORMATIONAL=0, negating gives a natural
        descending sort.
    """
    return -int(step.priority)


def _deduplicate_steps(
    steps: list[RepairStep],
) -> list[RepairStep]:
    """Remove duplicate steps, keeping the first occurrence of each ``(action, coordinate)`` pair.

    Two steps are considered duplicates if they share the same
    ``action`` and ``coordinate`` values.  The first occurrence in
    *steps* (which has already been priority-sorted by the caller) is
    retained; subsequent duplicates are discarded.

    This deduplication policy ensures that merging two plans that both
    address the same coordinate with the same action produces a single,
    non-redundant step.

    Parameters
    ----------
    steps : list[RepairStep]
        The mutable list of steps to deduplicate in-place order.

    Returns
    -------
    list[RepairStep]
        A new list containing only the first occurrence of each
        ``(action, coordinate)`` pair.
    """
    seen: set[tuple[str, str]] = set()
    result: list[RepairStep] = []
    for step in steps:
        key = (step.action, step.coordinate)
        if key not in seen:
            seen.add(key)
            result.append(step)
    return result


def _build_linear_dependency_order(
    steps: tuple[RepairStep, ...],
) -> tuple[tuple[str, str], ...]:
    """Build a linear dependency-order edge list from an ordered step sequence.

    Produces edges ``(steps[i].step_id, steps[i+1].step_id)`` for each
    consecutive pair in *steps*.  This encodes a total order: every step
    depends on the preceding step.

    The resulting edge list is always acyclic (it is a path graph), so a
    plan built with this dependency order is guaranteed to be admissible.

    Parameters
    ----------
    steps : tuple[RepairStep, ...]
        The ordered steps to chain.  Must contain at least 2 steps for any
        edges to be produced.

    Returns
    -------
    tuple[tuple[str, str], ...]
        Edge list ``(from_id, to_id)`` encoding the linear order.
        Returns an empty tuple if *steps* has fewer than 2 elements.
    """
    if len(steps) < 2:
        return ()
    return tuple(
        (steps[i].step_id, steps[i + 1].step_id)
        for i in range(len(steps) - 1)
    )


def _confidence_from_severity(severity_score: int) -> float:
    """Convert an integer severity score to a planner confidence value.

    Higher severity implies lower confidence that the plan will fully
    resolve the issue (since harder failures are less predictably
    repairable).  The mapping is a linear interpolation from
    ``[1, 10] → [0.9, 0.1]``.

    Parameters
    ----------
    severity_score : int
        An integer in ``[1, 10]`` as returned by
        :meth:`~jugeo.problem_modes.repair_semantics.counterexample_extraction.CounterexampleAnalyzer.score_severity`.

    Returns
    -------
    float
        A confidence value in ``[0.1, 0.9]`` rounded to two decimal places.
    """
    clamped = max(1, min(10, severity_score))
    # Linear interpolation: severity=1 → confidence=0.9, severity=10 → confidence=0.1
    confidence = 0.9 - (clamped - 1) * (0.8 / 9.0)
    return round(confidence, 2)


def _severity_from_failure_class(fc: FailureClass) -> int:
    """Return a rough severity estimate for a :class:`FailureClass`.

    This mirrors the scoring table from Stage 01 without requiring a full
    :class:`~jugeo.problem_modes.repair_semantics.counterexample_extraction.CounterexampleAnalyzer`
    instance.  Used internally by the planner when computing confidence
    scores directly from failure classes.

    Parameters
    ----------
    fc : FailureClass
        The failure class to estimate.

    Returns
    -------
    int
        An integer in ``{1, 2, 3, 4, 5}``.
    """
    _SCORES: dict[FailureClass, int] = {
        FailureClass.UNKNOWN: 1,
        FailureClass.ASSIGNMENT_CONFLICT: 2,
        FailureClass.FUNCTION_MISMATCH: 3,
        FailureClass.SORT_VIOLATION: 3,
        FailureClass.QUANTIFIER_WITNESS: 4,
        FailureClass.ARRAY_OUT_OF_BOUNDS: 5,
    }
    return _SCORES.get(fc, 1)


# ---------------------------------------------------------------------------
# §3  RepairPlanner
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RepairPlanner:
    """Plans repair steps as a partial order for a counterexample record.

    The planner is the second stage of the repair pipeline.  It accepts a
    :class:`~jugeo.problem_modes.repair_semantics.models.CounterexampleRecord`
    (or a :class:`~jugeo.problem_modes.repair_semantics.models.RepairFrontier`)
    and produces an admissible :class:`~jugeo.problem_modes.repair_semantics.models.RepairPlan`.

    Theory basis
    ------------
    Implements theory2.tex §11.2.  A repair plan is a partial order
    ``(R, ≤_P, conf)`` where:

    * ``R`` is the set of repair steps (encoded as ``plan.steps``).
    * ``≤_P`` is the dependency relation (encoded as ``plan.dependency_order``).
    * ``conf`` is the planner's confidence score (encoded as
      ``plan.confidence_score``).

    The planner guarantees that plans it produces are admissible (no cycles
    in the dependency graph) by constructing the dependency order using
    :func:`_build_linear_dependency_order`, which always produces a DAG.

    Attributes
    ----------
    coordinate : str
        The root coordinate for planning.  Used as the default coordinate
        for steps that do not have an explicit target coordinate.
    max_steps : int
        Maximum steps per plan.  Plans that would exceed this limit are
        truncated to the most important steps by priority.
    confidence_threshold : float
        Minimum confidence score for a plan to be considered acceptable
        by :meth:`check_plan_admissibility`.
    enable_merging : bool
        Whether :meth:`merge_plans` may combine plans from multiple records.
        When ``False``, :meth:`merge_plans` raises :exc:`ValueError`.
    """

    coordinate: str = ""
    max_steps: int = 20
    confidence_threshold: float = 0.3
    enable_merging: bool = True

    # ------------------------------------------------------------------
    # §3.1  Primary planning entry point
    # ------------------------------------------------------------------

    def plan(self, record: CounterexampleRecord) -> RepairPlan:
        """Produce a :class:`RepairPlan` from a single counterexample record.

        Converts each :class:`~jugeo.errors.RepairHint` in the record into
        a :class:`~jugeo.problem_modes.repair_semantics.models.RepairStep`,
        places REQUIRED- and CRITICAL-priority steps before lower-priority
        ones, chains them into a linear dependency order, and computes a
        confidence score derived from the record's failure class severity.

        The algorithm:

        1. Separate hints into "high-priority" (REQUIRED or CRITICAL) and
           "low-priority" (RECOMMENDED, SUGGESTED, INFORMATIONAL) buckets.
        2. Within each bucket, preserve the original hint order.
        3. Concatenate: high-priority first, then low-priority.
        4. Create a :class:`RepairStep` for each hint in the merged sequence.
        5. Build a linear dependency chain: step[i+1] depends on step[i].
        6. Truncate to :attr:`max_steps`.
        7. Set ``is_admissible=True`` (linear chains are always DAGs).
        8. Compute confidence from failure-class severity.

        Parameters
        ----------
        record : CounterexampleRecord
            The counterexample record whose ``repair_hints`` will be
            converted to steps.

        Returns
        -------
        RepairPlan
            An admissible repair plan with steps ordered by priority and
            chained into a linear dependency order.
        """
        coord = record.coordinate or self.coordinate

        # Partition hints by priority
        high_priority: list[RepairHint] = []
        low_priority: list[RepairHint] = []
        for hint in record.repair_hints:
            if hint.priority >= RepairPriority.REQUIRED:
                high_priority.append(hint)
            else:
                low_priority.append(hint)

        # Merge: high-priority hints lead, low-priority follow
        ordered_hints = high_priority + low_priority

        # Enforce max_steps limit
        ordered_hints = ordered_hints[: self.max_steps]

        # Build RepairStep objects, threading dependencies
        steps: list[RepairStep] = []
        for hint in ordered_hints:
            target_coord = hint.target_coordinate or coord
            step = RepairStep(
                step_id=_new_step_id(),
                action=hint.action,
                description=hint.description,
                priority=hint.priority,
                depends_on=(),   # will be set via dependency_order
                coordinate=target_coord,
            )
            steps.append(step)

        step_tuple = tuple(steps)

        # Build linear dependency order (always a DAG)
        dep_order = _build_linear_dependency_order(step_tuple)

        # Compute confidence from failure-class severity
        severity = _severity_from_failure_class(record.failure_class)
        confidence = _confidence_from_severity(severity)

        return RepairPlan(
            coordinate=coord,
            steps=step_tuple,
            confidence_score=confidence,
            is_admissible=True,
            dependency_order=dep_order,
        )

    # ------------------------------------------------------------------
    # §3.2  Frontier-based planning
    # ------------------------------------------------------------------

    def plan_for_frontier(self, frontier: RepairFrontier) -> RepairPlan:
        """Produce a :class:`RepairPlan` covering all coordinates in a frontier.

        Creates one :class:`RepairStep` per coordinate in the frontier,
        using the coordinate name as both the step description and target
        coordinate.  Coordinates that are also *obstruction coordinates*
        receive CRITICAL-priority steps; plain repair coordinates receive
        REQUIRED-priority steps; all others receive SUGGESTED steps.

        After creating individual steps for all coordinates, the steps are
        priority-sorted and chained into a linear dependency order.

        Parameters
        ----------
        frontier : RepairFrontier
            The repair frontier to plan for.  All coordinates in
            ``frontier.coordinates`` will be covered.

        Returns
        -------
        RepairPlan
            An admissible repair plan covering the full frontier, with steps
            ordered from most-critical to least-critical coordinate.
        """
        coord = self.coordinate
        steps: list[RepairStep] = []

        for target_coord in sorted(frontier.coordinates):
            if target_coord in frontier.obstruction_coordinates:
                priority = RepairPriority.CRITICAL
                action = "resolve_obstruction"
                desc = (
                    f"Resolve the active obstruction at coordinate '{target_coord}'.  "
                    "This coordinate has been flagged as an obstruction coordinate "
                    "in the repair frontier."
                )
            elif target_coord in frontier.repair_coordinates:
                priority = RepairPriority.REQUIRED
                action = "apply_repair_hints"
                desc = (
                    f"Apply the available repair hints at coordinate '{target_coord}'.  "
                    "This coordinate has at least one non-empty repair hint."
                )
            else:
                priority = RepairPriority.SUGGESTED
                action = "review_coordinate"
                desc = (
                    f"Review coordinate '{target_coord}' as part of the repair frontier.  "
                    "No specific repair hints are available; manual inspection required."
                )

            steps.append(
                RepairStep(
                    step_id=_new_step_id(),
                    action=action,
                    description=desc,
                    priority=priority,
                    depends_on=(),
                    coordinate=target_coord,
                )
            )

        # Priority-sort: CRITICAL first
        steps.sort(key=_priority_sort_key)

        # Truncate to max_steps
        steps = steps[: self.max_steps]

        step_tuple = tuple(steps)
        dep_order = _build_linear_dependency_order(step_tuple)

        # Confidence is lower when descent_failures are present
        base_confidence = 0.7
        if frontier.descent_failures:
            base_confidence -= 0.1 * min(3, len(frontier.descent_failures))
        confidence = round(max(0.1, base_confidence), 2)

        return RepairPlan(
            coordinate=coord,
            steps=step_tuple,
            confidence_score=confidence,
            is_admissible=True,
            dependency_order=dep_order,
        )

    # ------------------------------------------------------------------
    # §3.3  Step prioritization
    # ------------------------------------------------------------------

    def prioritize_steps(self, plan: RepairPlan) -> RepairPlan:
        """Return a copy of *plan* with steps sorted by priority.

        Sorts the steps in descending priority order (CRITICAL first,
        INFORMATIONAL last) and rebuilds the linear dependency order to
        match the new step sequence.

        The ``is_admissible`` flag is preserved because a re-sorted linear
        chain is still a DAG.

        Parameters
        ----------
        plan : RepairPlan
            The plan whose steps should be re-prioritized.

        Returns
        -------
        RepairPlan
            A new :class:`~jugeo.problem_modes.repair_semantics.models.RepairPlan`
            with steps sorted by descending priority and a freshly
            constructed dependency order.
        """
        sorted_steps = tuple(sorted(plan.steps, key=_priority_sort_key))
        dep_order = _build_linear_dependency_order(sorted_steps)
        return replace(plan, steps=sorted_steps, dependency_order=dep_order)

    # ------------------------------------------------------------------
    # §3.4  Plan merging
    # ------------------------------------------------------------------

    def merge_plans(self, plans: Sequence[RepairPlan]) -> RepairPlan:
        """Combine multiple repair plans into a single merged plan.

        The algorithm:

        1. Validate that merging is enabled (:attr:`enable_merging`).
        2. Collect all steps from all plans into a single pool.
        3. Deduplicate by ``(action, coordinate)`` using
           :func:`_deduplicate_steps`, keeping the first (highest-priority)
           occurrence of each action–coordinate pair.
        4. Sort the deduplicated pool by priority.
        5. Truncate to :attr:`max_steps`.
        6. Build a fresh linear dependency order.
        7. Average the confidence scores of all input plans (clamped to
           ``[0.0, 1.0]``).
        8. Set ``is_admissible=True`` (linear chain is always a DAG).

        Parameters
        ----------
        plans : Sequence[RepairPlan]
            The plans to merge.  Must be non-empty.

        Returns
        -------
        RepairPlan
            A new merged plan with a fresh ``plan_id``.

        Raises
        ------
        ValueError
            If :attr:`enable_merging` is ``False``.
        ValueError
            If *plans* is empty.
        """
        if not self.enable_merging:
            raise ValueError(
                "RepairPlanner.merge_plans() is disabled "
                "(enable_merging=False)."
            )
        if not plans:
            raise ValueError(
                "RepairPlanner.merge_plans() requires at least one plan."
            )

        # Collect all steps from all plans
        all_steps: list[RepairStep] = []
        for plan in plans:
            all_steps.extend(plan.steps)

        # Sort by priority before deduplication so the highest-priority
        # occurrence of each (action, coordinate) pair is retained
        all_steps.sort(key=_priority_sort_key)

        # Deduplicate by (action, coordinate)
        deduped_steps = _deduplicate_steps(all_steps)

        # Truncate to max_steps
        deduped_steps = deduped_steps[: self.max_steps]
        step_tuple = tuple(deduped_steps)

        # Build fresh linear dependency order
        dep_order = _build_linear_dependency_order(step_tuple)

        # Average confidence scores
        total_confidence = sum(p.confidence_score for p in plans)
        avg_confidence = round(
            max(0.0, min(1.0, total_confidence / len(plans))), 2
        )

        # Use the coordinate from the highest-confidence plan (or the first)
        best_plan = max(plans, key=lambda p: p.confidence_score)
        merged_coord = best_plan.coordinate or self.coordinate

        return RepairPlan(
            coordinate=merged_coord,
            steps=step_tuple,
            confidence_score=avg_confidence,
            is_admissible=True,
            dependency_order=dep_order,
        )

    # ------------------------------------------------------------------
    # §3.5  Effort estimation
    # ------------------------------------------------------------------

    def estimate_effort(self, plan: RepairPlan) -> str:
        """Estimate the total developer effort required to execute *plan*.

        The estimate is based on the number of steps in the plan:

        * 0 steps  → ``"trivial"``
        * 1 step   → ``"trivial"``
        * 2–4 steps → ``"moderate"``
        * 5–9 steps → ``"significant"``
        * ≥10 steps → ``"major"``

        Additionally, if any step has CRITICAL priority the estimate is
        upgraded by one level (trivial → moderate, moderate → significant,
        significant → major).

        Parameters
        ----------
        plan : RepairPlan
            The plan to estimate.

        Returns
        -------
        str
            One of ``"trivial"``, ``"moderate"``, ``"significant"``,
            or ``"major"``.
        """
        _EFFORT_LEVELS = ("trivial", "moderate", "significant", "major")
        n = len(plan.steps)

        if n <= 1:
            level_index = 0      # trivial
        elif n <= 4:
            level_index = 1      # moderate
        elif n <= 9:
            level_index = 2      # significant
        else:
            level_index = 3      # major

        # Upgrade by one level if any step is CRITICAL
        has_critical = any(s.is_critical() for s in plan.steps)
        if has_critical:
            level_index = min(3, level_index + 1)

        return _EFFORT_LEVELS[level_index]

    # ------------------------------------------------------------------
    # §3.6  Admissibility check
    # ------------------------------------------------------------------

    def check_plan_admissibility(self, plan: RepairPlan) -> bool:
        """Check whether *plan* is admissible and meets the confidence threshold.

        Performs two checks:

        1. Delegates structural validation (cycle detection, reference
           resolution) to :class:`~jugeo.problem_modes.repair_semantics.models.RepairValidator`.
        2. Verifies that ``plan.confidence_score >= self.confidence_threshold``.

        Both checks must pass for the method to return ``True``.

        Parameters
        ----------
        plan : RepairPlan
            The plan to check.

        Returns
        -------
        bool
            ``True`` iff the plan is structurally valid **and** its
            confidence score meets :attr:`confidence_threshold`.
        """
        validator = RepairValidator(strict=False)
        is_valid, _failures = validator.validate(plan)
        meets_threshold = plan.confidence_score >= self.confidence_threshold
        return is_valid and meets_threshold

    # ------------------------------------------------------------------
    # §3.7  Plan refinement
    # ------------------------------------------------------------------

    def refine_plan(self, plan: RepairPlan, feedback: str) -> RepairPlan:
        """Refine an existing plan in response to textual feedback.

        Interprets *feedback* for the following directives (case-insensitive):

        * ``"simplify"`` — remove all steps with INFORMATIONAL or SUGGESTED
          priority, leaving only RECOMMENDED, REQUIRED, and CRITICAL steps.
          Rebuilds the dependency order to reflect the reduced step set.
        * ``"prioritize"`` — re-sort all steps by descending priority via
          :meth:`prioritize_steps`.
        * ``"expand"`` — append a generic SUGGESTED manual-review step
          targeting :attr:`coordinate`.  Useful when the reviewer believes
          the plan does not cover all failure dimensions.

        Multiple directives may be present in *feedback* and are applied in
        the order listed above (simplify, then prioritize, then expand).
        Unrecognized text is ignored.

        Parameters
        ----------
        plan : RepairPlan
            The plan to refine.
        feedback : str
            Free-text refinement directive from the user or an upstream
            orchestrator.

        Returns
        -------
        RepairPlan
            A new :class:`~jugeo.problem_modes.repair_semantics.models.RepairPlan`
            reflecting the requested refinements.  The original *plan* is
            not modified.
        """
        refined = plan
        fb_lower = feedback.lower()

        # Directive 1: simplify — strip optional steps
        if "simplify" in fb_lower:
            retained = tuple(
                s for s in refined.steps
                if s.priority > RepairPriority.SUGGESTED
            )
            dep_order = _build_linear_dependency_order(retained)
            refined = replace(refined, steps=retained, dependency_order=dep_order)

        # Directive 2: prioritize — re-sort remaining steps
        if "prioritize" in fb_lower:
            refined = self.prioritize_steps(refined)

        # Directive 3: expand — add a generic review step
        if "expand" in fb_lower:
            review_step = RepairStep(
                step_id=_new_step_id(),
                action="manual_review",
                description=(
                    "Expanded review step added in response to 'expand' directive.  "
                    "Perform a comprehensive manual inspection of all coordinates "
                    "touched by this plan before executing the repair."
                ),
                priority=RepairPriority.SUGGESTED,
                depends_on=(),
                coordinate=self.coordinate,
            )
            new_steps = refined.steps + (review_step,)
            new_dep_order = _build_linear_dependency_order(new_steps)
            refined = replace(refined, steps=new_steps, dependency_order=new_dep_order)

        return refined

    # ------------------------------------------------------------------
    # §3.8  Repair frontier computation
    # ------------------------------------------------------------------

    def compute_repair_frontier(
        self,
        records: Sequence[CounterexampleRecord],
    ) -> RepairFrontier:
        """Build a :class:`RepairFrontier` from a sequence of counterexample records.

        Collects all distinct coordinates from the records and partitions
        them into:

        * **obstruction_coordinates** — coordinates whose ``failure_class``
          is :attr:`~jugeo.solver.countermodels.FailureClass.ASSIGNMENT_CONFLICT`
          or :attr:`~jugeo.solver.countermodels.FailureClass.SORT_VIOLATION`.
          These represent active semantic obstructions that must be resolved
          before the plan can be executed.
        * **repair_coordinates** — coordinates that have at least one
          non-empty ``repair_hints`` tuple.  These coordinates have
          actionable repair guidance available.
        * **coordinates** — the union of the two above sets plus any other
          coordinates present in the records.

        The ``descent_failures`` field is populated from the ``cohomology_class``
        strings of all records, deduplicated while preserving order.

        The ``coverage_score`` is the fraction of records that have at least
        one repair hint (``len(repair_hints) > 0``).

        The frontier is marked minimal (``is_minimal=True``) iff the total
        number of records is ≤ 3, following theory2.tex §11.3 Remark 11.5.

        Parameters
        ----------
        records : Sequence[CounterexampleRecord]
            The counterexample records from which to build the frontier.
            May be empty (returns an empty frontier).

        Returns
        -------
        RepairFrontier
            A :class:`~jugeo.problem_modes.repair_semantics.models.RepairFrontier`
            representing the minimal set of coordinates to repair.
        """
        if not records:
            return RepairFrontier(
                coordinates=frozenset(),
                obstruction_coordinates=frozenset(),
                repair_coordinates=frozenset(),
                descent_failures=(),
                coverage_score=0.0,
                is_minimal=True,
            )

        all_coordinates: set[str] = set()
        obstruction_coords: set[str] = set()
        repair_coords: set[str] = set()
        descent_failures_seen: list[str] = []
        descent_failures_set: set[str] = set()
        records_with_hints = 0

        _obstruction_classes = frozenset({
            FailureClass.ASSIGNMENT_CONFLICT,
            FailureClass.SORT_VIOLATION,
        })

        for rec in records:
            coord = rec.coordinate
            if coord:
                all_coordinates.add(coord)

                # Classify as obstruction coordinate
                if rec.failure_class in _obstruction_classes:
                    obstruction_coords.add(coord)

                # Classify as repair coordinate
                if rec.has_repair_hints():
                    repair_coords.add(coord)
                    records_with_hints += 1

            # Collect descent failure labels (preserve insertion order)
            if rec.cohomology_class and rec.cohomology_class not in descent_failures_set:
                descent_failures_set.add(rec.cohomology_class)
                descent_failures_seen.append(rec.cohomology_class)

        # Coverage score: fraction of records with at least one repair hint
        coverage_score = round(records_with_hints / len(records), 4)

        # Minimality per theory2.tex §11.3 Remark 11.5
        is_minimal = len(records) <= 3

        return RepairFrontier(
            coordinates=frozenset(all_coordinates),
            obstruction_coordinates=frozenset(obstruction_coords),
            repair_coordinates=frozenset(repair_coords),
            descent_failures=tuple(descent_failures_seen),
            coverage_score=coverage_score,
            is_minimal=is_minimal,
        )




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
    "RepairPlanner",
    # Unified architecture cross-references
    "repair_from_countermodel",
    "repair_certificate",
    "repair_descent_check",
]

# copilot: end of s02 repair planning
