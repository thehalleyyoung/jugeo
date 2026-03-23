"""CountermodelToRepair pipeline for the JuGeo structural_frontier package.

This module implements the CountermodelToRepair pipeline — the pathway from a
countermodel (a concrete witness that a claim fails) through obstruction
classification, repair candidate generation, repair frontier navigation, and
verification.  A countermodel is not just a failure; it is a map to the repair.

The pipeline has four stages:

1.  **Extraction** — A raw :class:`~jugeo.solver.countermodels.Countermodel` is
    converted into a :class:`~jugeo.encodings.structural_frontier.models.CountermodelObstruction`
    by the :class:`ObstructionClassifier`, which inspects the countermodel's
    assignments, sorts, and function symbols to determine the
    :class:`~jugeo.solver.countermodels.FailureClass` and the violated
    invariant string.

2.  **Generation** — The :class:`RepairCandidateGenerator` produces a ranked
    list of :class:`~jugeo.encodings.structural_frontier.models.RepairAction`
    values by consulting the obstruction's frontier boundary, its failure class,
    and a set of heuristic strategies (strengthen precondition, weaken
    postcondition, add type invariant, split case, abstract away).

3.  **Navigation** — The :class:`RepairFrontierNavigator` uses the
    :class:`~jugeo.encodings.structural_frontier.models.DecidabilityMap` to
    find the cheapest sequence of frontier boundary crossings that moves the
    formula from its current (undecidable) region into a decidable one.

4.  **Verification** — After applying a repair action the pipeline checks
    whether the resulting obstruction ``is_resolvable()``, logging the outcome
    and updating the repair history.

The structural frontier determines which repairs are feasible, and the repair
frontier navigator charts a course from the failed claim to a successful one.
Copilot integration is available for obstruction hints, repair suggestions, and
navigation guidance throughout the pipeline.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Optional imports — the solver stack is guarded so the module can be
# imported in environments without the full jugeo solver installed.
# ---------------------------------------------------------------------------

try:
    from jugeo.solver.z3_session import (
        Z3Session,
        Z3Formula,
        SolveOutcome,
        SolverResult,
        Z3QueryBuilder,
        Z3Result,
    )
except Exception:  # pragma: no cover
    Z3Session = Any  # type: ignore[assignment,misc]
    Z3Formula = Any  # type: ignore[assignment,misc]
    SolveOutcome = Any  # type: ignore[assignment,misc]
    SolverResult = Any  # type: ignore[assignment,misc]
    Z3QueryBuilder = Any  # type: ignore[assignment,misc]
    Z3Result = Any  # type: ignore[assignment,misc]

try:
    from jugeo.solver.fragments import (
        Fragment,
        LogicalFragment,
        SolverFragment,
        classify_fragment,
    )
except Exception:  # pragma: no cover
    Fragment = Any  # type: ignore[assignment,misc]
    LogicalFragment = Any  # type: ignore[assignment,misc]
    SolverFragment = Any  # type: ignore[assignment,misc]
    classify_fragment = None  # type: ignore[assignment]

try:
    from jugeo.solver.countermodels import (
        Countermodel,
        CountermodelExtractor,
        ObstructionConverter,
        FailureClass,
        RepairType,
    )
except Exception:  # pragma: no cover
    Countermodel = Any  # type: ignore[assignment,misc]
    CountermodelExtractor = Any  # type: ignore[assignment,misc]
    ObstructionConverter = Any  # type: ignore[assignment,misc]

    class FailureClass(str, Enum):  # type: ignore[no-redef]
        ASSIGNMENT_CONFLICT = "assignment_conflict"
        SORT_VIOLATION = "sort_violation"
        FUNCTION_MISMATCH = "function_mismatch"
        ARRAY_OUT_OF_BOUNDS = "array_out_of_bounds"
        QUANTIFIER_WITNESS = "quantifier_witness"
        UNKNOWN = "unknown"

    class RepairType(str, Enum):  # type: ignore[no-redef]
        STRENGTHEN_PRECONDITION = "strengthen_precondition"
        WEAKEN_POSTCONDITION = "weaken_postcondition"
        ADD_INVARIANT = "add_invariant"
        FIX_IMPLEMENTATION = "fix_implementation"
        SPLIT_COVER = "split_cover"
        ADD_SORT_CONSTRAINT = "add_sort_constraint"
        REFINE_FUNCTION_SPEC = "refine_function_spec"
        MANUAL_REVIEW = "manual_review"

try:
    from jugeo.geometry.supports import SupportRegion
except Exception:  # pragma: no cover
    SupportRegion = Any  # type: ignore[assignment,misc]

try:
    from jugeo.encodings.structural_frontier.models import (
        DecidabilityClass,
        FrontierSide,
        RepairAction,
        StructuralFrontier,
        SolverLiftedType,
        FrontierBoundary,
        DecidabilityMap,
        CountermodelObstruction,
        KNOWN_DECIDABLE_FRAGMENTS,
        KNOWN_UNDECIDABLE_REGIONS,
        make_default_frontier,
        make_default_boundary,
        make_default_map,
    )
except Exception as _models_exc:  # pragma: no cover
    raise ImportError(
        f"structural_frontier.models could not be imported: {_models_exc}"
    ) from _models_exc

# ============================================================================
# Section 1: ObstructionClassifier
# ============================================================================

# --- Keyword sets for heuristic failure classification ----------------------

_ARRAY_KEYWORDS: frozenset[str] = frozenset(
    {"array", "select", "store", "index", "arr", "buf", "vec"}
)
_SORT_KEYWORDS: frozenset[str] = frozenset(
    {"sort", "type", "cast", "coerce", "int2real", "bv2int", "real2int"}
)
_FUNCTION_KEYWORDS: frozenset[str] = frozenset(
    {"apply", "lambda", "fun", "func", "map", "compose", "partial"}
)
_QUANTIFIER_KEYWORDS: frozenset[str] = frozenset(
    {"forall", "exists", "quantifier", "witness", "skolem"}
)


class ObstructionClassifier:
    """Classifies countermodels into failure classes and frontier locations.

    ObstructionClassifier is the first stage of the CountermodelToRepair
    pipeline.  It inspects the assignments, sorts, and function symbols
    inside a :class:`~jugeo.solver.countermodels.Countermodel` to determine
    the :class:`~jugeo.solver.countermodels.FailureClass` and the
    :class:`~jugeo.encodings.structural_frontier.models.FrontierBoundary`
    where the obstruction lives.  Results are cached to avoid re-classifying
    the same countermodel multiple times across batch runs.

    Copilot integration is available via :meth:`copilot_obstruction_hint`,
    which produces a structured hint string suitable for display in IDE
    extensions.
    """

    def __init__(self) -> None:
        """Initialise the classifier with an empty cache and an ObstructionConverter."""
        self.classification_cache: dict[str, FailureClass] = {}
        try:
            self.converter: ObstructionConverter | None = ObstructionConverter()
        except Exception:  # pragma: no cover
            self.converter = None
        logger.debug("ObstructionClassifier initialised")

    # --- classify -----------------------------------------------------------

    def classify(self, countermodel: Any) -> FailureClass:
        """Classify a countermodel into a high-level FailureClass.

        First attempts to delegate to
        :meth:`jugeo.solver.countermodels.ObstructionConverter.classify_failure`
        if available.  Falls back to a keyword-based heuristic that inspects
        the countermodel's assignment keys for patterns associated with arrays,
        sorts, functions, and quantifiers.  Results are cached by the
        countermodel's formula hash to avoid redundant work in batch pipelines.

        Parameters
        ----------
        countermodel:
            A :class:`~jugeo.solver.countermodels.Countermodel` instance
            returned by the solver.

        Returns
        -------
        FailureClass
            The inferred failure category for the countermodel.
        """
        cache_key = _fingerprint_countermodel(countermodel)
        if cache_key in self.classification_cache:
            logger.debug("ObstructionClassifier.classify: cache hit %s", cache_key[:8])
            return self.classification_cache[cache_key]

        result: FailureClass = FailureClass.UNKNOWN

        # Delegate to ObstructionConverter if available
        if self.converter is not None:
            try:
                result = self.converter.classify_failure(countermodel)
                self.classification_cache[cache_key] = result
                logger.debug(
                    "classify: converter returned %s for %s", result.value, cache_key[:8]
                )
                return result
            except Exception as exc:
                logger.debug("classify: converter failed (%s), falling back", exc)

        # Keyword heuristic over assignment keys
        assignment: dict[str, Any] = {}
        try:
            assignment = dict(countermodel.assignment)
        except Exception:
            pass

        key_str = " ".join(str(k).lower() for k in assignment.keys())

        if _ARRAY_KEYWORDS & set(key_str.split()):
            result = FailureClass.ARRAY_OUT_OF_BOUNDS
        elif _SORT_KEYWORDS & set(key_str.split()):
            result = FailureClass.SORT_VIOLATION
        elif _FUNCTION_KEYWORDS & set(key_str.split()):
            result = FailureClass.FUNCTION_MISMATCH
        elif _QUANTIFIER_KEYWORDS & set(key_str.split()):
            result = FailureClass.QUANTIFIER_WITNESS
        elif assignment:
            result = FailureClass.ASSIGNMENT_CONFLICT
        else:
            result = FailureClass.UNKNOWN

        self.classification_cache[cache_key] = result
        logger.debug(
            "classify: heuristic result %s for countermodel %s",
            result.value,
            cache_key[:8],
        )
        return result

    # --- extract_violated_invariants ----------------------------------------

    def extract_violated_invariants(self, countermodel: Any) -> list[str]:
        """Extract a list of violated invariant strings from a countermodel.

        Iterates over the countermodel's assignment dict and builds human-
        readable strings of the form ``"variable = value"`` that are likely
        to violate expected constraints.  For each assignment pair the method
        applies a simple value-range check: integer values outside [-1e6, 1e6]
        and boolean values of False are flagged as potential violations.

        Used by :meth:`extract_obstruction` to populate the
        ``violated_invariant`` field of the resulting
        :class:`~jugeo.encodings.structural_frontier.models.CountermodelObstruction`.

        Parameters
        ----------
        countermodel:
            A :class:`~jugeo.solver.countermodels.Countermodel` instance.

        Returns
        -------
        list[str]
            A list of ``"variable = value"`` strings that may violate
            expected invariants.  Empty list if no assignments are found.
        """
        violated: list[str] = []
        try:
            assignment = dict(countermodel.assignment)
        except Exception:
            logger.debug("extract_violated_invariants: no assignment attribute")
            return violated

        for var, val in assignment.items():
            var_str = str(var)
            val_str = str(val)
            # Flag out-of-range numeric values
            try:
                numeric = float(val_str)
                if abs(numeric) > 1_000_000:
                    violated.append(f"{var_str} = {val_str}  [out-of-range]")
                    continue
            except (ValueError, TypeError):
                pass
            # Flag boolean False assignments (likely a postcondition failure)
            if val_str.lower() in ("false", "0", "unsat"):
                violated.append(f"{var_str} = {val_str}  [false-assertion]")
                continue
            # Flag names that look like constraint identifiers
            if any(kw in var_str.lower() for kw in ("inv", "post", "pre", "assert", "req")):
                violated.append(f"{var_str} = {val_str}  [constraint-variable]")

        if not violated and assignment:
            # Fall back: report the first assignment as the violated invariant
            first_var, first_val = next(iter(assignment.items()))
            violated.append(f"{first_var} = {first_val}  [inferred]")

        logger.debug(
            "extract_violated_invariants: found %d violations", len(violated)
        )
        return violated

    # --- locate_in_frontier -------------------------------------------------

    def locate_in_frontier(
        self, countermodel: Any, definer: Any = None
    ) -> FrontierBoundary:
        """Locate the countermodel's formula within the decidability map.

        Attempts to retrieve a :class:`~jugeo.encodings.structural_frontier.models.FrontierBoundary`
        from the provided ``definer`` object.  If no definer is available or
        the definer does not expose a ``get_boundary`` method, falls back to
        ``make_default_boundary("linear_arithmetic", "nonlinear")``.

        The returned boundary is stored on the
        :class:`~jugeo.encodings.structural_frontier.models.CountermodelObstruction`
        and used by the copilot navigator to plan repair paths.

        Parameters
        ----------
        countermodel:
            The countermodel to locate.
        definer:
            An optional object with a ``get_boundary(countermodel)`` method.
            Typically a :class:`FrontierExplorer` or a user-supplied class.

        Returns
        -------
        FrontierBoundary
            The frontier boundary where the countermodel's formula lives.
        """
        if definer is not None:
            try:
                boundary = definer.get_boundary(countermodel)
                if isinstance(boundary, FrontierBoundary):
                    logger.debug("locate_in_frontier: definer returned boundary")
                    return boundary
            except Exception as exc:
                logger.debug(
                    "locate_in_frontier: definer.get_boundary failed (%s)", exc
                )

        # Inspect the countermodel formula to make an informed choice
        formula_str = ""
        try:
            formula_str = str(countermodel.formula).lower()
        except Exception:
            pass

        if any(kw in formula_str for kw in ("nonlinear", "nra", "nia", "*", "^", "pow")):
            return make_default_boundary("qf_lra", "qf_nra", cost=2)
        if any(kw in formula_str for kw in ("array", "select", "store")):
            return make_default_boundary("qf_uf", "qf_nia", cost=4)
        if any(kw in formula_str for kw in ("forall", "exists")):
            return make_default_boundary("qf_lia", "fo_la", cost=5)

        return make_default_boundary("linear_arithmetic", "nonlinear")

    # --- rank_by_severity ---------------------------------------------------

    def rank_by_severity(
        self, obstructions: list[CountermodelObstruction]
    ) -> list[CountermodelObstruction]:
        """Sort obstructions by confidence descending (highest confidence first).

        Higher confidence means the classifier is more certain about the
        failure class and its repair path.  The copilot repair scheduler uses
        this ordering to present the most actionable obstructions first.

        Parameters
        ----------
        obstructions:
            A list of :class:`~jugeo.encodings.structural_frontier.models.CountermodelObstruction`
            values to rank.

        Returns
        -------
        list[CountermodelObstruction]
            A new list sorted by ``confidence`` descending.
        """
        ranked = sorted(obstructions, key=lambda o: o.confidence, reverse=True)
        logger.debug("rank_by_severity: ranked %d obstructions", len(ranked))
        return ranked

    # --- detect_recurring ---------------------------------------------------

    def detect_recurring(
        self, obstructions: list[CountermodelObstruction]
    ) -> list[CountermodelObstruction]:
        """Return obstructions whose violated invariant appears more than once.

        Groups obstructions by their ``violated_invariant`` string and returns
        those that appear in more than one distinct obstruction record.
        Recurring obstructions indicate a systematic issue rather than an
        isolated counterexample, and the copilot repair pipeline should
        prioritise them accordingly.

        Parameters
        ----------
        obstructions:
            A list of :class:`~jugeo.encodings.structural_frontier.models.CountermodelObstruction`
            values to inspect.

        Returns
        -------
        list[CountermodelObstruction]
            Obstructions with a violated_invariant that appears more than once.
        """
        groups: dict[str, list[CountermodelObstruction]] = {}
        for obs in obstructions:
            key = obs.violated_invariant.strip()
            groups.setdefault(key, []).append(obs)

        recurring: list[CountermodelObstruction] = []
        for key, group in groups.items():
            if len(group) > 1:
                recurring.extend(group)
                logger.debug(
                    "detect_recurring: invariant %r appears %d times",
                    key[:60],
                    len(group),
                )

        return recurring

    # --- copilot_obstruction_hint -------------------------------------------

    def copilot_obstruction_hint(self, countermodel: Any) -> str:
        """Return a structured copilot hint for the given countermodel.

        The hint summarises the inferred failure class, the violated invariant,
        and the frontier location.  It is formatted for display in IDE copilot
        extensions and for inclusion in repair tickets.  The hint always
        includes a recommendation for next steps.

        Parameters
        ----------
        countermodel:
            A :class:`~jugeo.solver.countermodels.Countermodel` instance.

        Returns
        -------
        str
            A multi-line copilot hint string.
        """
        failure = self.classify(countermodel)
        invariants = self.extract_violated_invariants(countermodel)
        boundary = self.locate_in_frontier(countermodel)

        lines = [
            "=== Copilot Obstruction Hint ===",
            f"Failure class    : {failure.value}",
            f"Frontier         : {boundary.outside_fragment!r} → {boundary.inside_fragment!r}",
            f"Crossing cost    : {boundary.crossing_cost}",
            "",
            "Violated invariants:",
        ]
        if invariants:
            for inv in invariants[:5]:
                lines.append(f"  • {inv}")
        else:
            lines.append("  (none detected)")
        lines += [
            "",
            "Recommended action:",
            f"  Move formula into {boundary.inside_fragment!r} via "
            f"{boundary.crossing_label!r} (cost={boundary.crossing_cost}).",
            "  Use RepairCandidateGenerator.generate() for concrete repair steps.",
            "================================",
        ]
        return "\n".join(lines)


# ============================================================================
# Section 2: RepairCandidateGenerator
# ============================================================================

class RepairCandidateGenerator:
    """Generates ranked repair action candidates for a given obstruction.

    RepairCandidateGenerator is the second stage of the CountermodelToRepair
    pipeline.  It consults the obstruction's frontier boundary, its failure
    class, and a collection of heuristic strategies to produce an ordered
    list of :class:`~jugeo.encodings.structural_frontier.models.RepairAction`
    values.  The copilot repair pipeline presents these to the user as
    actionable steps, with the cheapest (lowest cost) listed first.

    Results are cached by obstruction fingerprint to avoid redundant
    computation in batch processing scenarios.  Copilot integration is
    available via :meth:`copilot_repair_suggestions`.
    """

    def __init__(self) -> None:
        """Initialise the generator with an empty suggestion cache."""
        self.suggestion_cache: dict[str, list[RepairAction]] = {}
        logger.debug("RepairCandidateGenerator initialised")

    # --- generate -----------------------------------------------------------

    def generate(self, obstruction: CountermodelObstruction) -> list[RepairAction]:
        """Generate a ranked list of repair actions for the given obstruction.

        Dispatches to heuristic strategies based on the obstruction's
        ``failure_class`` and ``repair_frontier``.  The returned list is
        sorted by action cost ascending so that the cheapest repair is first.
        Results are cached by obstruction fingerprint.

        Parameters
        ----------
        obstruction:
            A :class:`~jugeo.encodings.structural_frontier.models.CountermodelObstruction`
            describing the failure and its frontier location.

        Returns
        -------
        list[RepairAction]
            A cost-ordered list of repair candidates.
        """
        fp = obstruction.fingerprint()
        if fp in self.suggestion_cache:
            logger.debug("RepairCandidateGenerator.generate: cache hit %s", fp[:8])
            return self.suggestion_cache[fp]

        actions: list[RepairAction] = []

        fc = obstruction.failure_class
        boundary = obstruction.repair_frontier

        # Strategy dispatch based on failure class
        if fc == FailureClass.ASSIGNMENT_CONFLICT:
            actions.append(RepairAction(
                action_type=RepairType.STRENGTHEN_PRECONDITION,
                description=self.strengthen_precondition(obstruction),
                smt_fragment=f"(assert (strengthen {boundary.inside_fragment}))",
                cost=1,
                origin="heuristic:assignment_conflict",
            ))
            actions.append(RepairAction(
                action_type=RepairType.WEAKEN_POSTCONDITION,
                description=self.weaken_postcondition(obstruction),
                smt_fragment=f"(assert (weaken-post {boundary.outside_fragment}))",
                cost=2,
                origin="heuristic:assignment_conflict",
            ))

        elif fc == FailureClass.SORT_VIOLATION:
            actions.append(RepairAction(
                action_type=RepairType.ADD_SORT_CONSTRAINT,
                description=self.add_type_invariant(obstruction),
                smt_fragment=f"(declare-sort {boundary.inside_fragment} 0)",
                cost=1,
                origin="heuristic:sort_violation",
            ))
            for case_desc in self.split_case(obstruction):
                actions.append(RepairAction(
                    action_type=RepairType.SPLIT_COVER,
                    description=case_desc,
                    smt_fragment=f"(split-case {boundary.inside_fragment})",
                    cost=3,
                    origin="heuristic:sort_violation:split",
                ))

        elif fc == FailureClass.FUNCTION_MISMATCH:
            actions.append(RepairAction(
                action_type=RepairType.REFINE_FUNCTION_SPEC,
                description=self.add_type_invariant(obstruction),
                smt_fragment=f"(refine-function-spec {boundary.inside_fragment})",
                cost=2,
                origin="heuristic:function_mismatch",
            ))
            actions.append(RepairAction(
                action_type=RepairType.ADD_INVARIANT,
                description=self.abstract_away(obstruction),
                smt_fragment=f"(abstract {obstruction.violated_invariant[:40]})",
                cost=3,
                origin="heuristic:function_mismatch:abstract",
            ))

        elif fc == FailureClass.ARRAY_OUT_OF_BOUNDS:
            actions.append(RepairAction(
                action_type=RepairType.ADD_INVARIANT,
                description=self.add_type_invariant(obstruction),
                smt_fragment="(assert (>= index 0) (< index length))",
                cost=1,
                origin="heuristic:array_oob",
            ))
            actions.append(RepairAction(
                action_type=RepairType.STRENGTHEN_PRECONDITION,
                description=self.strengthen_precondition(obstruction),
                smt_fragment=f"(assert (array-bounds {boundary.inside_fragment}))",
                cost=2,
                origin="heuristic:array_oob:precond",
            ))

        elif fc == FailureClass.QUANTIFIER_WITNESS:
            actions.append(RepairAction(
                action_type=RepairType.WEAKEN_POSTCONDITION,
                description=self.weaken_postcondition(obstruction),
                smt_fragment="(skolemize (exists x phi))",
                cost=2,
                origin="heuristic:quantifier_witness",
            ))
            actions.append(RepairAction(
                action_type=RepairType.ADD_INVARIANT,
                description=self.abstract_away(obstruction),
                smt_fragment="(abstract-quantifier-to-uf)",
                cost=4,
                origin="heuristic:quantifier_witness:abstract",
            ))

        else:
            # Unknown failure class — generate conservative generic repairs
            actions.append(RepairAction(
                action_type=RepairType.STRENGTHEN_PRECONDITION,
                description=self.strengthen_precondition(obstruction),
                cost=2,
                origin="heuristic:unknown",
            ))
            actions.append(RepairAction(
                action_type=RepairType.MANUAL_REVIEW,
                description=(
                    f"Manual review required for failure class "
                    f"{fc.value!r}.  Violated: {obstruction.violated_invariant[:60]}"
                ),
                cost=999,
                origin="heuristic:unknown:manual",
            ))

        # Sort by cost ascending
        actions.sort(key=lambda a: a.cost)
        self.suggestion_cache[fp] = actions
        logger.debug(
            "generate: produced %d actions for obstruction %s",
            len(actions),
            obstruction.obstruction_id[:8],
        )
        return actions

    # --- strengthen_precondition --------------------------------------------

    def strengthen_precondition(self, obstruction: CountermodelObstruction) -> str:
        """Return an SMT-LIB2 description strengthening the precondition.

        Builds a concise description of a precondition strengthening that would
        prevent the violated invariant from arising.  The description references
        the inside fragment of the repair frontier so that copilot can present
        a fragment-aware suggestion.

        Parameters
        ----------
        obstruction:
            The obstruction whose precondition should be strengthened.

        Returns
        -------
        str
            A human-readable description of the strengthened precondition.
        """
        frag = obstruction.repair_frontier.inside_fragment
        inv = obstruction.violated_invariant[:80]
        suggestion = (
            f"Strengthen precondition to exclude assignments violating "
            f"{inv!r}.  "
            f"Restrict inputs to the {frag!r} fragment by adding: "
            f"(assert (in-fragment {frag}))."
        )
        logger.debug("strengthen_precondition: %s", suggestion[:60])
        return suggestion

    # --- weaken_postcondition -----------------------------------------------

    def weaken_postcondition(self, obstruction: CountermodelObstruction) -> str:
        """Return a description of a weakened postcondition avoiding the violation.

        Constructs a suggestion to relax the postcondition so that the
        countermodel assignment no longer constitutes a violation.  The
        weakening is expressed in terms of the outside fragment so that copilot
        can contextualise it against the undecidable region.

        Parameters
        ----------
        obstruction:
            The obstruction whose postcondition should be weakened.

        Returns
        -------
        str
            A human-readable description of the weakened postcondition.
        """
        outside = obstruction.repair_frontier.outside_fragment
        inv = obstruction.violated_invariant[:80]
        suggestion = (
            f"Weaken postcondition to allow assignments in {outside!r} that "
            f"satisfy {inv!r}.  "
            f"Replace strong equality with an existential witness: "
            f"(exists x (satisfies-post x))."
        )
        logger.debug("weaken_postcondition: %s", suggestion[:60])
        return suggestion

    # --- add_type_invariant -------------------------------------------------

    def add_type_invariant(self, obstruction: CountermodelObstruction) -> str:
        """Return a suggestion to add a type invariant addressing the violation.

        Produces an SMT-LIB2-oriented description of a type invariant that
        would constrain the violating assignment to the decidable fragment.
        The invariant targets the inside fragment of the repair frontier.

        Parameters
        ----------
        obstruction:
            The obstruction that requires a type invariant.

        Returns
        -------
        str
            A human-readable description of the suggested type invariant.
        """
        frag = obstruction.repair_frontier.inside_fragment
        fc = obstruction.failure_class.value
        inv = obstruction.violated_invariant[:60]
        suggestion = (
            f"Add type invariant for {fc!r}: constrain all witnesses to "
            f"the {frag!r} sort.  For the violated invariant {inv!r}, add: "
            f"(declare-fun type-ok () Bool) (assert type-ok)."
        )
        logger.debug("add_type_invariant: %s", suggestion[:60])
        return suggestion

    # --- split_case ---------------------------------------------------------

    def split_case(self, obstruction: CountermodelObstruction) -> list[str]:
        """Split the violated invariant into sub-cases for case analysis.

        Decomposes the violated invariant string at logical connectives
        (``and``, ``or``, ``implies``) to produce a list of sub-case
        descriptions.  Each sub-case is annotated with the fragment it
        targets so that copilot can present fragment-specific suggestions.

        Parameters
        ----------
        obstruction:
            The obstruction whose invariant should be split.

        Returns
        -------
        list[str]
            A list of sub-case description strings.
        """
        frag = obstruction.repair_frontier.inside_fragment
        inv = obstruction.violated_invariant

        # Attempt syntactic split on logical connectives
        sub_cases: list[str] = []
        for sep in (" and ", " or ", " implies ", " => ", "∧", "∨", "⇒"):
            if sep in inv:
                parts = inv.split(sep)
                sub_cases = [
                    f"Case {i+1}/{len(parts)}: verify {p.strip()!r} in {frag!r}"
                    for i, p in enumerate(parts)
                    if p.strip()
                ]
                break

        if not sub_cases:
            # Fallback: generate two generic sub-cases
            sub_cases = [
                f"Case 1/2: verify positive branch of {inv[:40]!r} in {frag!r}",
                f"Case 2/2: verify negative branch of {inv[:40]!r} in {frag!r}",
            ]

        logger.debug("split_case: generated %d sub-cases", len(sub_cases))
        return sub_cases

    # --- abstract_away ------------------------------------------------------

    def abstract_away(self, obstruction: CountermodelObstruction) -> str:
        """Return a description of abstracting away the problematic term.

        Produces a suggestion to replace the problematic term or sub-formula
        (identified via the violated invariant) with an uninterpreted function
        or abstract sort, moving the formula into the decidable fragment.

        Parameters
        ----------
        obstruction:
            The obstruction whose term should be abstracted.

        Returns
        -------
        str
            A human-readable description of the abstraction.
        """
        frag = obstruction.repair_frontier.inside_fragment
        inv = obstruction.violated_invariant[:60]
        outside = obstruction.repair_frontier.outside_fragment

        suggestion = (
            f"Abstract the problematic term {inv!r} from {outside!r} into an "
            f"uninterpreted function symbol in {frag!r}.  "
            f"Replace (nonlinear-term t) with (declare-fun abstract-t () {frag}) "
            f"and add (abstract-axiom abstract-t t)."
        )
        logger.debug("abstract_away: %s", suggestion[:60])
        return suggestion

    # --- copilot_repair_suggestions -----------------------------------------

    def copilot_repair_suggestions(
        self, obstruction: CountermodelObstruction
    ) -> list[str]:
        """Return a list of copilot repair suggestion strings for the obstruction.

        Generates all repair actions via :meth:`generate` and formats each as
        a numbered copilot suggestion string.  The suggestions are suitable for
        display in IDE copilot extensions and for inclusion in repair tickets.

        Parameters
        ----------
        obstruction:
            A :class:`~jugeo.encodings.structural_frontier.models.CountermodelObstruction`
            to generate suggestions for.

        Returns
        -------
        list[str]
            Numbered suggestion strings, one per repair action.
        """
        actions = self.generate(obstruction)
        suggestions: list[str] = []
        for i, action in enumerate(actions, start=1):
            suggestion = (
                f"[Copilot Repair {i}] {action.action_type.value} "
                f"(cost={action.cost}, origin={action.origin})\n"
                f"  {action.description}"
            )
            if action.smt_fragment:
                suggestion += f"\n  SMT: {action.smt_fragment}"
            suggestions.append(suggestion)

        if not suggestions:
            suggestions.append(
                "[Copilot Repair] No automated repairs available.  Manual review required."
            )
        return suggestions


# ============================================================================
# Section 3: RepairFrontierNavigator
# ============================================================================

class RepairFrontierNavigator:
    """Navigates the decidability map to find the cheapest repair path.

    RepairFrontierNavigator is the third stage of the CountermodelToRepair
    pipeline.  It uses a :class:`~jugeo.encodings.structural_frontier.models.DecidabilityMap`
    to find the cheapest sequence of frontier boundary crossings that moves a
    formula from its current (undecidable) region into a decidable fragment.
    The copilot navigator presents ranked paths and applies repair actions
    sequentially until the obstruction is resolved.

    Navigation history is recorded in ``navigation_log`` for debugging and
    audit purposes.  Copilot integration is available via
    :meth:`copilot_navigation_hint`.
    """

    def __init__(
        self, decidability_map: DecidabilityMap | None = None
    ) -> None:
        """Initialise the navigator with an optional decidability map.

        Parameters
        ----------
        decidability_map:
            A :class:`~jugeo.encodings.structural_frontier.models.DecidabilityMap`
            to use for path-finding.  If None, the navigator will accept a
            map at call time via the ``map_`` parameter of :meth:`navigate_to_decidable`.
        """
        self.map: DecidabilityMap | None = decidability_map
        self.navigation_log: list[dict[str, Any]] = []
        logger.debug("RepairFrontierNavigator initialised")

    # --- navigate_to_decidable ----------------------------------------------

    def navigate_to_decidable(
        self,
        obstruction: CountermodelObstruction,
        map_: DecidabilityMap,
    ) -> list[FrontierBoundary]:
        """Find the cheapest path from the obstruction to a decidable fragment.

        Uses :meth:`DecidabilityMap.crossing_path` to enumerate all paths from
        the obstruction's current outside fragment to every decidable fragment
        in the map.  Returns the path with the lowest total crossing cost.
        Logs the navigation attempt and result.

        Parameters
        ----------
        obstruction:
            The obstruction to navigate away from.
        map_:
            The :class:`~jugeo.encodings.structural_frontier.models.DecidabilityMap`
            to use for path-finding.

        Returns
        -------
        list[FrontierBoundary]
            The cheapest sequence of boundary crossings, or an empty list if
            no decidable fragment is reachable.
        """
        outside = obstruction.repair_frontier.outside_fragment
        all_paths = map_.all_paths_to_decidable(outside)

        if not all_paths:
            logger.warning(
                "navigate_to_decidable: no paths from %r to any decidable fragment",
                outside,
            )
            self.navigation_log.append({
                "obstruction_id": obstruction.obstruction_id,
                "from": outside,
                "paths_found": 0,
                "chosen_path": [],
                "total_cost": None,
            })
            return []

        cheapest = self.pick_cheapest_path(all_paths)
        total_cost = self.cost_estimate(cheapest)

        self.navigation_log.append({
            "obstruction_id": obstruction.obstruction_id,
            "from": outside,
            "paths_found": len(all_paths),
            "chosen_path": [b.crossing_label for b in cheapest],
            "total_cost": total_cost,
        })
        logger.debug(
            "navigate_to_decidable: chose path of length %d, cost %d",
            len(cheapest),
            total_cost,
        )
        return cheapest

    # --- cost_estimate ------------------------------------------------------

    def cost_estimate(self, path: list[FrontierBoundary]) -> int:
        """Estimate the total cost of traversing a sequence of boundaries.

        Sums the ``crossing_cost`` of all boundaries in the path.  Used by
        :meth:`pick_cheapest_path` to rank competing repair paths.  A path
        cost of zero indicates that no crossings are needed (source is already
        in a decidable fragment).

        Parameters
        ----------
        path:
            A list of :class:`~jugeo.encodings.structural_frontier.models.FrontierBoundary`
            crossings constituting the path.

        Returns
        -------
        int
            The total crossing cost.
        """
        total = sum(b.crossing_cost for b in path)
        logger.debug("cost_estimate: path of %d boundaries has cost %d", len(path), total)
        return total

    # --- pick_cheapest_path -------------------------------------------------

    def pick_cheapest_path(
        self, paths: list[list[FrontierBoundary]]
    ) -> list[FrontierBoundary]:
        """Return the path with the minimum total crossing cost.

        When multiple paths have the same cost, the shorter path (fewer
        crossings) is preferred as a secondary criterion.  Returns an empty
        list if ``paths`` is empty.

        Parameters
        ----------
        paths:
            A list of candidate paths, each a list of
            :class:`~jugeo.encodings.structural_frontier.models.FrontierBoundary`
            crossings.

        Returns
        -------
        list[FrontierBoundary]
            The cheapest path, or ``[]`` if none are provided.
        """
        if not paths:
            logger.debug("pick_cheapest_path: no paths provided")
            return []

        best = min(paths, key=lambda p: (self.cost_estimate(p), len(p)))
        logger.debug(
            "pick_cheapest_path: chose path of length %d cost %d from %d candidates",
            len(best),
            self.cost_estimate(best),
            len(paths),
        )
        return best

    # --- apply_repair -------------------------------------------------------

    def apply_repair(
        self,
        obstruction: CountermodelObstruction,
        action: RepairAction,
    ) -> CountermodelObstruction:
        """Apply a repair action to the obstruction and return the updated record.

        Calls :meth:`CountermodelObstruction.add_action` to produce an
        obstruction with ``action`` appended to its suggested_actions list.
        Logs the application for audit purposes.  This is a non-destructive
        update; the original obstruction is not modified.

        Parameters
        ----------
        obstruction:
            The obstruction to apply the repair to.
        action:
            The :class:`~jugeo.encodings.structural_frontier.models.RepairAction`
            to apply.

        Returns
        -------
        CountermodelObstruction
            The updated obstruction with the action appended.
        """
        updated = obstruction.add_action(action)
        logger.debug(
            "apply_repair: applied %s to obstruction %s",
            action.action_type.value,
            obstruction.obstruction_id[:8],
        )
        return updated

    # --- verify_repair ------------------------------------------------------

    def verify_repair(self, repaired: CountermodelObstruction) -> bool:
        """Verify whether the repaired obstruction is resolvable.

        Delegates to :meth:`CountermodelObstruction.is_resolvable` which
        returns True when at least one non-MANUAL_REVIEW repair action has
        been suggested.  Logs the verification outcome.

        Parameters
        ----------
        repaired:
            A :class:`~jugeo.encodings.structural_frontier.models.CountermodelObstruction`
            after repair actions have been applied.

        Returns
        -------
        bool
            True if the obstruction is considered resolved, False otherwise.
        """
        result = repaired.is_resolvable()
        logger.debug(
            "verify_repair: obstruction %s is_resolvable=%s",
            repaired.obstruction_id[:8],
            result,
        )
        return result

    # --- copilot_navigation_hint --------------------------------------------

    def copilot_navigation_hint(
        self, obstruction: CountermodelObstruction
    ) -> str:
        """Return a structured copilot navigation hint for the obstruction.

        Summarises the last navigation attempt from ``navigation_log`` and
        provides a human-readable explanation of the chosen path, its cost,
        and the recommended next step.

        Parameters
        ----------
        obstruction:
            A :class:`~jugeo.encodings.structural_frontier.models.CountermodelObstruction`
            for which to produce a navigation hint.

        Returns
        -------
        str
            A multi-line copilot hint string.
        """
        # Find the most recent log entry for this obstruction
        entry: dict[str, Any] | None = None
        for log_entry in reversed(self.navigation_log):
            if log_entry.get("obstruction_id") == obstruction.obstruction_id:
                entry = log_entry
                break

        lines = [
            "=== Copilot Navigation Hint ===",
            f"Obstruction id  : {obstruction.obstruction_id[:8]}",
            f"Starting region : {obstruction.repair_frontier.outside_fragment!r}",
            f"Target region   : {obstruction.repair_frontier.inside_fragment!r}",
        ]
        if entry:
            path_labels = " → ".join(entry.get("chosen_path", []))
            lines += [
                f"Paths found     : {entry.get('paths_found', 0)}",
                f"Chosen path     : {path_labels or '(direct)'}",
                f"Total cost      : {entry.get('total_cost', 'N/A')}",
            ]
        else:
            lines.append("Navigation      : not yet attempted")
        lines += [
            "",
            "Next step: call RepairCandidateGenerator.generate() with this",
            "obstruction, then apply the first (cheapest) action.",
            "================================",
        ]
        return "\n".join(lines)


# ============================================================================
# Section 4: CountermodelToRepair (main pipeline class)
# ============================================================================

class CountermodelToRepair:
    """Main pipeline: from countermodel to ranked repair actions.

    CountermodelToRepair orchestrates the four pipeline stages:
    extraction, generation, navigation, and verification.  It owns an
    :class:`ObstructionClassifier`, a :class:`RepairCandidateGenerator`,
    a :class:`RepairFrontierNavigator`, and a
    :class:`~jugeo.encodings.structural_frontier.models.DecidabilityMap`.

    The pipeline is designed for both single-countermodel and batch usage.
    All pipeline results are stored in ``repair_history`` for auditing and
    copilot replay.  Batch statistics are accumulated in ``batch_stats``.

    Copilot integration is available via :meth:`copilot_repair_narrative`,
    which produces a multi-paragraph narrative suitable for documentation and
    IDE display.
    """

    def __init__(self) -> None:
        """Initialise the pipeline with default sub-components and an empty history."""
        self.classifier: ObstructionClassifier = ObstructionClassifier()
        self.generator: RepairCandidateGenerator = RepairCandidateGenerator()
        self.map_: DecidabilityMap = make_default_map()
        self.navigator: RepairFrontierNavigator = RepairFrontierNavigator(
            decidability_map=self.map_
        )
        self.repair_history: list[CountermodelObstruction] = []
        self.batch_stats: dict[str, Any] = {
            "total_processed": 0,
            "total_resolvable": 0,
            "total_manual_review": 0,
            "failure_class_counts": {},
        }
        logger.info("CountermodelToRepair pipeline initialised")

    # --- process ------------------------------------------------------------

    def process(
        self, countermodel: Any, context: str = ""
    ) -> CountermodelObstruction:
        """Run the full pipeline on a single countermodel.

        Executes all four pipeline stages in order:
        1. Extract obstruction from countermodel.
        2. Generate repair candidates.
        3. Navigate the repair frontier.
        4. Verify and record the result.

        Parameters
        ----------
        countermodel:
            A :class:`~jugeo.solver.countermodels.Countermodel` instance.
        context:
            Optional context string (e.g., module path) stored on the
            obstruction record.

        Returns
        -------
        CountermodelObstruction
            A fully populated obstruction with repair candidates and
            navigation data.
        """
        t_start = time.perf_counter()
        logger.info("CountermodelToRepair.process: starting pipeline")

        # Stage 1: Extract
        obstruction = self.extract_obstruction(countermodel)
        obstruction.context = context

        # Stage 2: Generate repair candidates
        actions = self.suggest_repairs(obstruction)
        obstruction.suggested_actions = actions

        # Stage 3: Navigate the repair frontier
        _path = self.navigate_repair_frontier(obstruction)

        # Stage 4: Update batch stats
        fc_val = obstruction.failure_class.value
        counts = self.batch_stats["failure_class_counts"]
        counts[fc_val] = counts.get(fc_val, 0) + 1
        self.batch_stats["total_processed"] += 1
        if obstruction.is_resolvable():
            self.batch_stats["total_resolvable"] += 1
        else:
            self.batch_stats["total_manual_review"] += 1

        self.repair_history.append(obstruction)

        elapsed_ms = (time.perf_counter() - t_start) * 1000
        logger.info(
            "CountermodelToRepair.process: completed in %.1fms, "
            "failure_class=%s, resolvable=%s",
            elapsed_ms,
            fc_val,
            obstruction.is_resolvable(),
        )
        return obstruction

    # --- extract_obstruction ------------------------------------------------

    def extract_obstruction(
        self, countermodel: Any
    ) -> CountermodelObstruction:
        """Extract a CountermodelObstruction from a raw countermodel.

        Calls the classifier to determine the failure class, extracts violated
        invariants, and locates the countermodel in the frontier.  Constructs
        a :class:`~jugeo.encodings.structural_frontier.models.CountermodelObstruction`
        with confidence computed as a function of the number of violated
        invariants found.

        Parameters
        ----------
        countermodel:
            A :class:`~jugeo.solver.countermodels.Countermodel` instance.

        Returns
        -------
        CountermodelObstruction
            A partially populated obstruction (no repair actions yet).
        """
        failure_class = self.classifier.classify(countermodel)
        invariants = self.classifier.extract_violated_invariants(countermodel)
        boundary = self.classifier.locate_in_frontier(countermodel)

        # Compute confidence: more invariants => higher confidence (capped at 0.95)
        confidence = min(0.95, 0.4 + 0.15 * len(invariants))

        violated = "; ".join(invariants[:3]) if invariants else "unknown"

        obstruction = CountermodelObstruction(
            countermodel=countermodel,
            failure_class=failure_class,
            violated_invariant=violated,
            repair_frontier=boundary,
            suggested_actions=[],
            confidence=confidence,
            obstruction_id=str(uuid.uuid4()),
        )
        logger.debug(
            "extract_obstruction: failure_class=%s, confidence=%.2f, "
            "frontier=%s → %s",
            failure_class.value,
            confidence,
            boundary.outside_fragment,
            boundary.inside_fragment,
        )
        return obstruction

    # --- suggest_repairs ----------------------------------------------------

    def suggest_repairs(
        self, obstruction: CountermodelObstruction
    ) -> list[RepairAction]:
        """Generate repair action candidates for the obstruction.

        Delegates to :meth:`RepairCandidateGenerator.generate` and logs
        the number of actions produced.

        Parameters
        ----------
        obstruction:
            The obstruction to generate repairs for.

        Returns
        -------
        list[RepairAction]
            A cost-ordered list of repair candidates.
        """
        actions = self.generator.generate(obstruction)
        logger.debug(
            "suggest_repairs: generated %d actions for %s",
            len(actions),
            obstruction.obstruction_id[:8],
        )
        return actions

    # --- navigate_repair_frontier -------------------------------------------

    def navigate_repair_frontier(
        self, obstruction: CountermodelObstruction
    ) -> list[FrontierBoundary]:
        """Navigate the decidability map to the cheapest repair path.

        Delegates to :meth:`RepairFrontierNavigator.navigate_to_decidable`
        using the pipeline's internal ``map_``.

        Parameters
        ----------
        obstruction:
            The obstruction to navigate from.

        Returns
        -------
        list[FrontierBoundary]
            The cheapest path from the obstruction's outside fragment to a
            decidable fragment, or an empty list if no path exists.
        """
        path = self.navigator.navigate_to_decidable(obstruction, self.map_)
        logger.debug(
            "navigate_repair_frontier: path length %d for obstruction %s",
            len(path),
            obstruction.obstruction_id[:8],
        )
        return path

    # --- apply_and_verify ---------------------------------------------------

    def apply_and_verify(
        self,
        obstruction: CountermodelObstruction,
        action: RepairAction,
    ) -> bool:
        """Apply a repair action and verify that the obstruction is resolved.

        Calls :meth:`RepairFrontierNavigator.apply_repair` followed by
        :meth:`RepairFrontierNavigator.verify_repair`.  Logs the outcome
        for audit purposes.

        Parameters
        ----------
        obstruction:
            The obstruction to repair.
        action:
            The repair action to apply.

        Returns
        -------
        bool
            True if the repaired obstruction is resolvable, False otherwise.
        """
        repaired = self.navigator.apply_repair(obstruction, action)
        verified = self.navigator.verify_repair(repaired)
        logger.info(
            "apply_and_verify: action=%s verified=%s obstruction=%s",
            action.action_type.value,
            verified,
            obstruction.obstruction_id[:8],
        )
        return verified

    # --- emit_repair_ticket -------------------------------------------------

    def emit_repair_ticket(
        self, obstruction: CountermodelObstruction
    ) -> dict[str, Any]:
        """Emit a full repair ticket dict for the given obstruction.

        Produces a JSON-serialisable dict containing all fields needed for
        downstream tooling: obstruction metadata, failure class, violated
        invariant, repair frontier, suggested actions, navigation hints, and
        the copilot narrative.  Used by CI pipelines and audit logs.

        Parameters
        ----------
        obstruction:
            A fully processed
            :class:`~jugeo.encodings.structural_frontier.models.CountermodelObstruction`.

        Returns
        -------
        dict[str, Any]
            A JSON-compatible repair ticket.
        """
        nav_hint = self.navigator.copilot_navigation_hint(obstruction)
        classifier_hint = self.classifier.copilot_obstruction_hint(
            obstruction.countermodel
        )
        repair_suggestions = self.generator.copilot_repair_suggestions(obstruction)

        ticket: dict[str, Any] = {
            "ticket_id": str(uuid.uuid4()),
            "created_at": time.time(),
            "obstruction": obstruction.to_dict(),
            "pipeline_stats": dict(self.batch_stats),
            "copilot_classifier_hint": classifier_hint,
            "copilot_navigation_hint": nav_hint,
            "copilot_repair_suggestions": repair_suggestions,
            "copilot_narrative": self.copilot_repair_narrative(obstruction),
        }
        logger.debug(
            "emit_repair_ticket: emitted ticket for obstruction %s",
            obstruction.obstruction_id[:8],
        )
        return ticket

    # --- batch_process ------------------------------------------------------

    def batch_process(
        self,
        countermodels: list[Any],
        context: str = "",
    ) -> list[CountermodelObstruction]:
        """Process a list of countermodels through the full pipeline.

        Iterates over ``countermodels``, calling :meth:`process` for each
        one.  Exceptions are caught and logged so that a single bad
        countermodel does not abort the batch.  Accumulates batch statistics
        in ``batch_stats``.

        Parameters
        ----------
        countermodels:
            A list of :class:`~jugeo.solver.countermodels.Countermodel`
            instances to process.
        context:
            Optional context string passed to each :meth:`process` call.

        Returns
        -------
        list[CountermodelObstruction]
            A list of obstructions, one per successfully processed countermodel.
        """
        results: list[CountermodelObstruction] = []
        logger.info(
            "CountermodelToRepair.batch_process: processing %d countermodels",
            len(countermodels),
        )
        for i, cm in enumerate(countermodels):
            try:
                obs = self.process(cm, context=context)
                results.append(obs)
            except Exception as exc:
                logger.error(
                    "batch_process: failed on countermodel %d: %s", i, exc
                )
        logger.info(
            "batch_process: completed %d/%d, resolvable=%d",
            len(results),
            len(countermodels),
            self.batch_stats["total_resolvable"],
        )
        return results

    # --- copilot_repair_narrative --------------------------------------------

    def copilot_repair_narrative(
        self, obstruction: CountermodelObstruction
    ) -> str:
        """Return a multi-paragraph copilot repair narrative for the obstruction.

        The narrative explains the countermodel failure in plain language,
        describes the frontier location, presents the top repair actions,
        and provides navigation guidance.  It is suitable for inclusion in
        PR comments, CI reports, and IDE copilot chat responses.

        Parameters
        ----------
        obstruction:
            A fully processed
            :class:`~jugeo.encodings.structural_frontier.models.CountermodelObstruction`.

        Returns
        -------
        str
            A multi-paragraph copilot narrative string.
        """
        fc = obstruction.failure_class.value
        inv = obstruction.violated_invariant[:120]
        inside = obstruction.repair_frontier.inside_fragment
        outside = obstruction.repair_frontier.outside_fragment
        cost = obstruction.repair_frontier.crossing_cost
        top_action = obstruction.most_likely_repair()
        resolvable = obstruction.is_resolvable()

        para1 = (
            f"**Countermodel Obstruction Report** (id={obstruction.obstruction_id[:8]})\n\n"
            f"The solver returned a countermodel indicating a {fc!r} failure.  "
            f"The violated invariant is: `{inv}`.  "
            f"This obstruction has a confidence score of {obstruction.confidence:.0%}, "
            f"meaning the copilot pipeline is {'highly' if obstruction.confidence > 0.75 else 'moderately'} "
            f"confident about this classification."
        )

        para2 = (
            f"**Frontier Location**\n\n"
            f"The formula currently resides in the `{outside}` region, which is "
            f"{'undecidable' if outside in KNOWN_UNDECIDABLE_REGIONS else 'potentially undecidable'}.  "
            f"The nearest decidable target is `{inside}`, reachable at a crossing "
            f"cost of {cost}.  The copilot navigator has identified "
            f"{'a' if resolvable else 'no'} viable automated repair path."
        )

        para3 = (
            f"**Recommended Repair**\n\n"
            f"The top-ranked repair action is: `{top_action.action_type.value}` "
            f"(cost={top_action.cost}, origin={top_action.origin}).  "
            f"Description: {top_action.description[:200]}"
        )
        if top_action.smt_fragment:
            para3 += f"\n\nSMT-LIB2 fragment:\n```smt2\n{top_action.smt_fragment}\n```"

        para4 = (
            f"**Pipeline Statistics**\n\n"
            f"Processed: {self.batch_stats['total_processed']} countermodels total.  "
            f"Resolvable: {self.batch_stats['total_resolvable']}.  "
            f"Manual review: {self.batch_stats['total_manual_review']}.  "
            f"This obstruction is {'resolvable automatically' if resolvable else 'flagged for manual review'}."
        )

        return "\n\n".join([para1, para2, para3, para4])


# ============================================================================
# Section 5: Module-level helpers
# ============================================================================

def _fingerprint_countermodel(countermodel: Any) -> str:
    """Return a stable hex fingerprint for a countermodel.

    Attempts to use the countermodel's formula string and assignment dict.
    Falls back to the object's repr.  Used as a cache key in
    :class:`ObstructionClassifier` and :class:`RepairCandidateGenerator`.

    Parameters
    ----------
    countermodel:
        A :class:`~jugeo.solver.countermodels.Countermodel` instance.

    Returns
    -------
    str
        A 16-character hex string.
    """
    try:
        formula_str = str(getattr(countermodel, "formula", ""))
        assignment = dict(getattr(countermodel, "assignment", {}))
        raw = json.dumps(
            {"formula": formula_str, "assignment": {str(k): str(v) for k, v in assignment.items()}},
            sort_keys=True,
        )
    except Exception:
        raw = repr(countermodel)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


# ============================================================================
# Section 6: Public exports
# ============================================================================

__all__ = [
    "ObstructionClassifier",
    "RepairCandidateGenerator",
    "RepairFrontierNavigator",
    "CountermodelToRepair",
    "_fingerprint_countermodel",
]
