"""Algorithms for structural frontier exploration and repair scheduling.

This module implements algorithms for structural frontier exploration,
decidability bisection, countermodel aggregation, and repair priority
scheduling.  These algorithms operationalise the theory of Chapter 25 —
they take formulas, countermodels, and frontier maps as input and produce
classifications, repair paths, and priority queues as output.

Architecture overview
---------------------
The module is organised into four collaborating classes plus a set of
top-level helper functions:

* :func:`classify_formula_fragment` -- maps a formula string to a
  :class:`~jugeo.encodings.structural_frontier.models.DecidabilityClass`.
* :func:`compute_frontier_boundary` -- finds the best-matching
  :class:`~jugeo.encodings.structural_frontier.models.FrontierBoundary`
  for a formula from a known list of frontiers.
* :func:`find_cheapest_encoding` -- finds the decidable fragment name
  reachable at the lowest cost from a formula's current location.
* :func:`batch_classify` -- classifies a list of formula strings in one call.
* :class:`FrontierExplorer` -- enumerates and analyses the reachable frontier
  graph from a starting formula.
* :class:`DecidabilityBisector` -- splits a formula at its main connective
  and verifies that the parts cover the original.
* :class:`CountermodelAggregator` -- converts and deduplicates batches of
  countermodels into prioritised obstruction lists.
* :class:`RepairPriorityScheduler` -- sorts and groups obstructions into a
  repair schedule optimised for minimum total cost.

Copilot integration is available throughout for hints, exploration reports,
and schedule summaries.  All classes use ``logger`` for structured debug
output, and all value objects use frozen dataclasses where mutation is not
required.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterator

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Optional imports — guarded so the module can run without the full solver.
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
# Section 1: Top-level algorithm functions
# ============================================================================

# --- Fragment keyword maps for formula classification -----------------------

_DECIDABLE_KEYWORDS: dict[str, str] = {
    "qf_lia": "linear_integer_arithmetic",
    "qf_lra": "linear_real_arithmetic",
    "qf_bv": "bitvector",
    "qf_uf": "uninterpreted",
    "propositional": "propositional",
    "linear_arithmetic": "linear",
}
_UNDECIDABLE_KEYWORDS: dict[str, str] = {
    "qf_nra": "nonlinear_real",
    "qf_nia": "nonlinear_int",
    "ho_logic": "higher_order",
    "fo_array": "first_order_array",
    "heap_logic": "heap_separation",
}
_NONLINEAR_SIGNALS: frozenset[str] = frozenset(
    {"*", "**", "^", "pow", "sqrt", "mod", "rem", "nonlinear", "nra", "nia"}
)
_QUANTIFIER_SIGNALS: frozenset[str] = frozenset(
    {"forall", "exists", "∀", "∃", "quantifier"}
)
_ARRAY_SIGNALS: frozenset[str] = frozenset(
    {"select", "store", "array", "arr", "buf"}
)
_BV_SIGNALS: frozenset[str] = frozenset(
    {"bvadd", "bvsub", "bvmul", "bitvec", "bvand", "bvor", "extract"}
)
_UF_SIGNALS: frozenset[str] = frozenset(
    {"declare-fun", "uninterpreted", "uf", "apply"}
)


def classify_formula_fragment(formula_smt: str) -> DecidabilityClass:
    """Classify a formula string into a DecidabilityClass.

    Uses :func:`jugeo.solver.fragments.classify_fragment` when available to
    obtain a :class:`~jugeo.solver.fragments.LogicalFragment` label, then
    maps it to the corresponding
    :class:`~jugeo.encodings.structural_frontier.models.DecidabilityClass`.
    Falls back to a keyword-based heuristic when the solver fragment
    classifier is unavailable.

    Parameters
    ----------
    formula_smt:
        An SMT-LIB2 formula string to classify.

    Returns
    -------
    DecidabilityClass
        The decidability class inferred for the formula.
    """
    formula_lower = formula_smt.lower()

    # Attempt solver-level classification
    if classify_fragment is not None:
        try:
            sf = classify_fragment(formula_smt)
            frag_name = getattr(sf, "fragment", None)
            if frag_name is not None:
                frag_str = str(frag_name).lower()
                if any(d in frag_str for d in ("propositional", "equality", "horn")):
                    logger.debug(
                        "classify_formula_fragment: solver classified as %s → DECIDABLE",
                        frag_str,
                    )
                    return DecidabilityClass.DECIDABLE
                if "unknown" in frag_str:
                    pass  # fall through to heuristic
                else:
                    return DecidabilityClass.DECIDABLE
        except Exception as exc:
            logger.debug("classify_formula_fragment: solver classify failed: %s", exc)

    # Keyword heuristic
    tokens = set(formula_lower.replace("(", " ").replace(")", " ").split())

    if tokens & _NONLINEAR_SIGNALS:
        if "integer" in formula_lower or "int" in formula_lower:
            logger.debug("classify_formula_fragment: nonlinear int → UNDECIDABLE")
            return DecidabilityClass.UNDECIDABLE
        logger.debug("classify_formula_fragment: nonlinear real → SEMI_DECIDABLE")
        return DecidabilityClass.SEMI_DECIDABLE

    if tokens & _QUANTIFIER_SIGNALS:
        logger.debug("classify_formula_fragment: quantified → SEMI_DECIDABLE")
        return DecidabilityClass.SEMI_DECIDABLE

    if tokens & _BV_SIGNALS:
        logger.debug("classify_formula_fragment: bitvector → DECIDABLE")
        return DecidabilityClass.DECIDABLE

    if tokens & _ARRAY_SIGNALS:
        if tokens & _QUANTIFIER_SIGNALS:
            logger.debug("classify_formula_fragment: quantified array → UNDECIDABLE")
            return DecidabilityClass.UNDECIDABLE
        logger.debug("classify_formula_fragment: qf array → DECIDABLE")
        return DecidabilityClass.DECIDABLE

    if tokens & _UF_SIGNALS:
        logger.debug("classify_formula_fragment: UF → DECIDABLE")
        return DecidabilityClass.DECIDABLE

    # Default to decidable for simple linear-looking formulas
    linear_ops = {"+", "-", "<=", ">=", "<", ">", "=", "and", "or", "not"}
    if tokens & linear_ops:
        logger.debug("classify_formula_fragment: linear → DECIDABLE")
        return DecidabilityClass.DECIDABLE

    logger.debug("classify_formula_fragment: unknown → UNKNOWN")
    return DecidabilityClass.UNKNOWN


def compute_frontier_boundary(
    formula_smt: str,
    known_frontiers: list[StructuralFrontier],
) -> FrontierBoundary | None:
    """Find the FrontierBoundary whose boundary formula best matches the input.

    Iterates over ``known_frontiers`` and computes a simple token-overlap
    similarity between ``formula_smt`` and each frontier's
    ``boundary_formula_smt``.  Returns the :class:`FrontierBoundary` for the
    best-matching frontier (inside) vs its undecidable complement (outside),
    or None if no frontier achieves a non-zero overlap.

    Parameters
    ----------
    formula_smt:
        An SMT-LIB2 formula string to locate.
    known_frontiers:
        A list of :class:`~jugeo.encodings.structural_frontier.models.StructuralFrontier`
        objects to search.

    Returns
    -------
    FrontierBoundary | None
        The best-matching boundary, or None if no match is found.
    """
    if not known_frontiers:
        logger.debug("compute_frontier_boundary: no frontiers provided")
        return None

    formula_tokens = set(formula_smt.lower().split())
    best_score = 0.0
    best_frontier: StructuralFrontier | None = None

    for frontier in known_frontiers:
        if not frontier.boundary_formula_smt:
            continue
        boundary_tokens = set(frontier.boundary_formula_smt.lower().split())
        intersection = len(formula_tokens & boundary_tokens)
        union = len(formula_tokens | boundary_tokens)
        if union == 0:
            continue
        jaccard = intersection / union
        # Weight decidable frontiers slightly higher
        weight = 1.1 if frontier.is_decidable() else 1.0
        score = jaccard * weight
        if score > best_score:
            best_score = score
            best_frontier = frontier

    if best_frontier is None or best_score == 0.0:
        logger.debug(
            "compute_frontier_boundary: no matching frontier for formula (len=%d)",
            len(formula_smt),
        )
        return None

    # Determine the outside fragment: find the nearest undecidable sibling
    outside = "nonlinear"
    for frontier in known_frontiers:
        if not frontier.is_decidable() and frontier.name != best_frontier.name:
            outside = frontier.name
            break

    boundary = FrontierBoundary(
        inside_fragment=best_frontier.name,
        outside_fragment=outside,
        crossing_cost=max(1, int(math.ceil(1.0 / (best_score + 1e-9) * 0.1))),
        boundary_formula_smt=best_frontier.boundary_formula_smt,
        crossing_label=f"enter_{best_frontier.name}",
    )
    logger.debug(
        "compute_frontier_boundary: matched %r (score=%.3f)", best_frontier.name, best_score
    )
    return boundary


def find_cheapest_encoding(
    formula_smt: str, map_: DecidabilityMap
) -> str:
    """Find the decidable fragment reachable at the lowest crossing cost.

    Classifies the formula to determine its current decidability class,
    then uses the decidability map to find all paths to decidable fragments
    and returns the name of the fragment reachable at the lowest total cost.

    Parameters
    ----------
    formula_smt:
        An SMT-LIB2 formula string.
    map_:
        A :class:`~jugeo.encodings.structural_frontier.models.DecidabilityMap`
        used for path-finding.

    Returns
    -------
    str
        The name of the cheapest reachable decidable fragment, or
        ``"unknown"`` if no path exists.
    """
    # Classify the formula to find its starting fragment
    dc = classify_formula_fragment(formula_smt)
    if dc == DecidabilityClass.DECIDABLE:
        # Already decidable — find the best-fitting decidable fragment
        formula_tokens = set(formula_smt.lower().split())
        for name in KNOWN_DECIDABLE_FRAGMENTS:
            if any(t in formula_tokens for t in name.split("_")):
                logger.debug("find_cheapest_encoding: already decidable as %r", name)
                return name
        return KNOWN_DECIDABLE_FRAGMENTS[0] if KNOWN_DECIDABLE_FRAGMENTS else "qf_lia"

    # Determine starting fragment name
    starting = "nonlinear"
    if dc == DecidabilityClass.UNDECIDABLE:
        starting = "qf_nia"
    elif dc == DecidabilityClass.SEMI_DECIDABLE:
        starting = "qf_nra"

    # Find all paths to decidable fragments
    decidable_names = map_.decidable_frontier_names()
    best_name = "unknown"
    best_cost = math.inf

    for target in decidable_names:
        path = map_.crossing_path(starting, target)
        if not path:
            continue
        total_cost = sum(b.crossing_cost for b in path)
        if total_cost < best_cost:
            best_cost = total_cost
            best_name = target

    logger.debug(
        "find_cheapest_encoding: starting=%r best=%r cost=%s",
        starting,
        best_name,
        best_cost,
    )
    return best_name


def batch_classify(formulas: list[str]) -> dict[str, DecidabilityClass]:
    """Classify a list of formula strings, returning a mapping to DecidabilityClass.

    Calls :func:`classify_formula_fragment` for each formula and collects
    results into a dict keyed by formula string.  Identical formulas are
    classified only once (the second occurrence reuses the cached result).

    Parameters
    ----------
    formulas:
        A list of SMT-LIB2 formula strings to classify.

    Returns
    -------
    dict[str, DecidabilityClass]
        A mapping from each formula string to its
        :class:`~jugeo.encodings.structural_frontier.models.DecidabilityClass`.
    """
    cache: dict[str, DecidabilityClass] = {}
    result: dict[str, DecidabilityClass] = {}
    for formula in formulas:
        if formula in cache:
            result[formula] = cache[formula]
        else:
            dc = classify_formula_fragment(formula)
            cache[formula] = dc
            result[formula] = dc
    logger.debug(
        "batch_classify: classified %d formulas (%d unique)", len(formulas), len(cache)
    )
    return result


# ============================================================================
# Section 2: FrontierExplorer
# ============================================================================

class FrontierExplorer:
    """Explores and analyses the reachable frontier graph from a starting point.

    FrontierExplorer traverses the decidability map from a given starting
    formula or frontier, recording visited fragments and classifying them.
    It identifies bottlenecks (fragments that appear on many paths),
    enumerates all reachable decidable fragments, and finds the nearest
    decidable target.  The copilot exploration report summarises findings
    for display and audit.

    Attributes
    ----------
    start_frontier:
        The initial :class:`~jugeo.encodings.structural_frontier.models.StructuralFrontier`
        (may be None for lazy initialisation).
    visited:
        A set of fragment names already explored.
    exploration_log:
        A list of dicts recording each exploration step.
    """

    def __init__(
        self, start_frontier: StructuralFrontier | None = None
    ) -> None:
        """Initialise the explorer with an optional starting frontier.

        Parameters
        ----------
        start_frontier:
            An optional :class:`~jugeo.encodings.structural_frontier.models.StructuralFrontier`
            to begin exploration from.  If None, exploration starts from the
            default map's first frontier.
        """
        self.start_frontier: StructuralFrontier | None = start_frontier
        self.visited: set[str] = set()
        self.exploration_log: list[dict[str, Any]] = []
        self._map: DecidabilityMap = make_default_map()
        self._classification_cache: dict[str, DecidabilityClass] = {}
        logger.debug("FrontierExplorer initialised with start_frontier=%r", start_frontier)

    # --- explore ------------------------------------------------------------

    def explore(self, start_formula: str) -> None:
        """Start exploration from a formula string, classifying and recording results.

        Classifies the formula, locates it in the default decidability map,
        and performs a breadth-first traversal of all reachable fragments
        from the formula's inferred starting fragment.  Each visited fragment
        is logged to ``exploration_log`` with its decidability class and the
        cost to reach it from the start.

        Parameters
        ----------
        start_formula:
            An SMT-LIB2 formula string to begin exploration from.
        """
        self.visited.clear()
        self.exploration_log.clear()

        dc = classify_formula_fragment(start_formula)
        self._classification_cache[start_formula] = dc
        logger.debug("explore: starting formula classified as %s", dc.value)

        # Determine starting fragment name
        starting = "nonlinear"
        if dc == DecidabilityClass.DECIDABLE:
            starting = "linear_arithmetic"
        elif dc == DecidabilityClass.SEMI_DECIDABLE:
            starting = "qf_nra"
        elif dc == DecidabilityClass.UNDECIDABLE:
            starting = "qf_nia"

        # BFS over the fragment graph
        from collections import deque
        queue: deque[tuple[str, int]] = deque([(starting, 0)])
        self.visited.add(starting)

        while queue:
            fragment_name, depth = queue.popleft()
            frontier = self._map.get_frontier(fragment_name)
            frag_dc = (
                frontier.decidability_class
                if frontier is not None
                else DecidabilityClass.UNKNOWN
            )

            self.exploration_log.append({
                "fragment": fragment_name,
                "depth": depth,
                "decidability_class": frag_dc.value,
                "timestamp": time.time(),
            })

            if depth >= 6:
                continue  # cap exploration depth

            for boundary in self._map.boundaries:
                neighbour: str | None = None
                if boundary.outside_fragment == fragment_name:
                    neighbour = boundary.inside_fragment
                elif boundary.inside_fragment == fragment_name:
                    neighbour = boundary.outside_fragment

                if neighbour and neighbour not in self.visited:
                    self.visited.add(neighbour)
                    queue.append((neighbour, depth + 1))

        logger.debug(
            "explore: visited %d fragments, logged %d steps",
            len(self.visited),
            len(self.exploration_log),
        )

    # --- enumerate_reachable ------------------------------------------------

    def enumerate_reachable(self) -> list[str]:
        """Return all fragment names visited during the last exploration.

        Returns the contents of the ``visited`` set as a sorted list.
        Useful for copilot reports and for verifying map coverage.

        Returns
        -------
        list[str]
            Sorted list of visited fragment names.
        """
        reachable = sorted(self.visited)
        logger.debug("enumerate_reachable: %d fragments", len(reachable))
        return reachable

    # --- find_nearest_decidable ---------------------------------------------

    def find_nearest_decidable(self, formula: str) -> str:
        """Return the name of the nearest decidable fragment for a formula.

        Classifies the formula, determines its starting fragment, then
        iterates over all decidable fragments in the map to find the one
        with the shortest BFS path.

        Parameters
        ----------
        formula:
            An SMT-LIB2 formula string.

        Returns
        -------
        str
            The name of the nearest decidable fragment, or ``"unknown"``
            if no decidable fragment is reachable.
        """
        dc = classify_formula_fragment(formula)
        if dc == DecidabilityClass.DECIDABLE:
            # Already decidable — return first reachable decidable fragment
            for name in KNOWN_DECIDABLE_FRAGMENTS:
                if name in self.visited:
                    logger.debug("find_nearest_decidable: already decidable at %r", name)
                    return name
            return KNOWN_DECIDABLE_FRAGMENTS[0]

        starting = "qf_nia" if dc == DecidabilityClass.UNDECIDABLE else "qf_nra"
        decidable_names = self._map.decidable_frontier_names()

        best_name = "unknown"
        best_cost = math.inf

        for target in decidable_names:
            path = self._map.crossing_path(starting, target, max_depth=6)
            if not path:
                continue
            cost = sum(b.crossing_cost for b in path)
            if cost < best_cost:
                best_cost = cost
                best_name = target

        logger.debug(
            "find_nearest_decidable: best=%r cost=%s for formula (len=%d)",
            best_name,
            best_cost,
            len(formula),
        )
        return best_name

    # --- map_boundary_crossings ---------------------------------------------

    def map_boundary_crossings(self) -> dict[str, list[str]]:
        """Return a dict mapping each visited fragment to reachable neighbours.

        For each fragment in ``visited``, looks up all boundaries in the map
        and records which fragments can be reached in one crossing.  Used by
        copilot exploration reports and bottleneck analysis.

        Returns
        -------
        dict[str, list[str]]
            Mapping from fragment name to list of directly reachable fragment names.
        """
        crossings: dict[str, list[str]] = {}
        for frag in self.visited:
            neighbours: list[str] = []
            for boundary in self._map.boundaries:
                if boundary.outside_fragment == frag:
                    neighbours.append(boundary.inside_fragment)
                elif boundary.inside_fragment == frag:
                    neighbours.append(boundary.outside_fragment)
            crossings[frag] = sorted(set(neighbours))

        logger.debug(
            "map_boundary_crossings: computed crossings for %d fragments", len(crossings)
        )
        return crossings

    # --- identify_bottlenecks -----------------------------------------------

    def identify_bottlenecks(self) -> list[str]:
        """Return fragment names that appear on many crossing paths.

        A bottleneck is a fragment that is reachable from many other fragments
        in the map (high in-degree in the crossing graph).  These are important
        chokepoints where copilot should focus repair suggestions.

        Returns
        -------
        list[str]
            Fragment names sorted by in-degree descending (most connected first).
        """
        crossings = self.map_boundary_crossings()
        in_degree: dict[str, int] = defaultdict(int)

        for frag, neighbours in crossings.items():
            for neighbour in neighbours:
                in_degree[neighbour] += 1

        # Also add direct out-degree contribution
        for frag in self.visited:
            in_degree.setdefault(frag, 0)
            in_degree[frag] += len(crossings.get(frag, []))

        bottlenecks = sorted(in_degree, key=lambda f: in_degree[f], reverse=True)
        logger.debug(
            "identify_bottlenecks: top bottleneck is %r (degree=%d)",
            bottlenecks[0] if bottlenecks else "none",
            in_degree.get(bottlenecks[0], 0) if bottlenecks else 0,
        )
        return bottlenecks

    # --- copilot_exploration_report -----------------------------------------

    def copilot_exploration_report(self) -> str:
        """Return a structured copilot exploration report.

        Summarises the fragments visited, the decidable targets found, the
        bottleneck fragments, and the boundary crossing map.  Formatted for
        display in IDE copilot extensions and for inclusion in audit logs.

        Returns
        -------
        str
            A multi-section report string.
        """
        reachable = self.enumerate_reachable()
        decidable = [n for n in reachable if n in KNOWN_DECIDABLE_FRAGMENTS]
        undecidable = [n for n in reachable if n in KNOWN_UNDECIDABLE_REGIONS]
        bottlenecks = self.identify_bottlenecks()[:3]
        crossings = self.map_boundary_crossings()

        lines = [
            "=== Copilot Frontier Exploration Report ===",
            f"Total fragments visited : {len(reachable)}",
            f"Decidable fragments     : {', '.join(decidable) or 'none'}",
            f"Undecidable fragments   : {', '.join(undecidable) or 'none'}",
            f"Top bottlenecks         : {', '.join(bottlenecks) or 'none'}",
            "",
            "Boundary crossings (fragment → reachable):",
        ]
        for frag in sorted(crossings)[:10]:
            lines.append(f"  {frag!r:30s} → {crossings[frag]}")
        if not crossings:
            lines.append("  (no crossings found — run explore() first)")
        lines.append("==========================================")
        return "\n".join(lines)


# ============================================================================
# Section 3: DecidabilityBisector
# ============================================================================

class DecidabilityBisector:
    """Splits formulas at decidability boundaries for case analysis.

    DecidabilityBisector decomposes a formula at its main logical connective
    or at a specified :class:`~jugeo.encodings.structural_frontier.models.FrontierBoundary`,
    producing a pair of sub-formulas that together cover the original.  The
    bisector is used by the copilot repair pipeline when a formula straddles
    a decidability boundary and needs to be handled in two decidable fragments
    separately.

    Results are cached by formula content hash to avoid redundant work.
    Copilot integration is available via :meth:`copilot_bisection_hint`.
    """

    def __init__(self) -> None:
        """Initialise the bisector with an empty split cache and an empty log."""
        self.split_cache: dict[str, tuple[str, str]] = {}
        self.bisection_log: list[dict[str, Any]] = []
        logger.debug("DecidabilityBisector initialised")

    # --- bisect -------------------------------------------------------------

    def bisect(self, formula_smt: str) -> tuple[str, str]:
        """Split a formula at its main connective into two sub-formulas.

        Attempts to split at ``(and ...)``, ``(or ...)``, ``(implies ...)``,
        or ``(=> ...)``.  If no connective is found, splits the formula string
        at its midpoint (a heuristic bisection).  Results are cached by formula
        content hash.

        Parameters
        ----------
        formula_smt:
            An SMT-LIB2 formula string to bisect.

        Returns
        -------
        tuple[str, str]
            A pair of sub-formula strings (left, right).
        """
        cache_key = hashlib.md5(formula_smt.encode()).hexdigest()[:12]
        if cache_key in self.split_cache:
            logger.debug("bisect: cache hit %s", cache_key)
            return self.split_cache[cache_key]

        stripped = formula_smt.strip()
        parts: tuple[str, str]

        # Try structural split on main connectives
        for connective in ("(and ", "(or ", "(implies ", "(=> "):
            if stripped.startswith(connective):
                inner = stripped[len(connective):-1].strip()
                # Find the split point between first and second sub-formula
                depth = 0
                split_idx = 0
                for i, ch in enumerate(inner):
                    if ch == "(":
                        depth += 1
                    elif ch == ")":
                        depth -= 1
                    if depth == 0 and ch == " ":
                        split_idx = i
                        break
                if split_idx > 0:
                    left = inner[:split_idx].strip()
                    right = inner[split_idx:].strip()
                    if left and right:
                        parts = (left, right)
                        self.split_cache[cache_key] = parts
                        self._log_bisection(formula_smt, parts, "connective")
                        return parts

        # Fallback: midpoint string split
        mid = len(stripped) // 2
        parts = (stripped[:mid], stripped[mid:])
        self.split_cache[cache_key] = parts
        self._log_bisection(formula_smt, parts, "midpoint")
        logger.debug(
            "bisect: midpoint split at %d for formula (len=%d)", mid, len(stripped)
        )
        return parts

    # --- split_at_boundary --------------------------------------------------

    def split_at_boundary(
        self, formula: str, boundary: FrontierBoundary
    ) -> tuple[str, str]:
        """Split a formula into inside and outside parts based on a boundary.

        Produces two fragments: one that is annotated as lying in the inside
        (decidable) fragment and one that lies in the outside (undecidable)
        fragment.  The split is based on keyword signals associated with each
        fragment name.

        Parameters
        ----------
        formula:
            An SMT-LIB2 formula string to split.
        boundary:
            A :class:`~jugeo.encodings.structural_frontier.models.FrontierBoundary`
            defining the inside and outside fragments.

        Returns
        -------
        tuple[str, str]
            A pair ``(inside_part, outside_part)`` of formula fragments.
        """
        inside_kws = set(boundary.inside_fragment.lower().replace("_", " ").split())
        outside_kws = set(boundary.outside_fragment.lower().replace("_", " ").split())
        tokens = formula.lower().split()

        inside_tokens: list[str] = []
        outside_tokens: list[str] = []

        for token in tokens:
            clean = token.strip("()")
            if any(kw in clean for kw in outside_kws):
                outside_tokens.append(token)
            elif any(kw in clean for kw in inside_kws):
                inside_tokens.append(token)
            else:
                inside_tokens.append(token)  # default to inside

        inside_part = " ".join(inside_tokens) if inside_tokens else formula
        outside_part = " ".join(outside_tokens) if outside_tokens else ""

        self._log_bisection(
            formula,
            (inside_part, outside_part),
            f"boundary:{boundary.crossing_label}",
        )
        logger.debug(
            "split_at_boundary: inside_len=%d outside_len=%d",
            len(inside_part),
            len(outside_part),
        )
        return (inside_part, outside_part)

    # --- reconstruct_from_parts ---------------------------------------------

    def reconstruct_from_parts(self, parts: tuple[str, str]) -> str:
        """Reconstruct a formula from two parts using a logical conjunction.

        Wraps both parts in ``(and ...)`` if both are non-empty; otherwise
        returns whichever part is non-empty.  Used after boundary splitting
        to verify that the parts together cover the original formula.

        Parameters
        ----------
        parts:
            A pair of formula strings ``(left, right)``.

        Returns
        -------
        str
            The reconstructed formula.
        """
        left, right = parts
        left = left.strip()
        right = right.strip()

        if left and right:
            reconstructed = f"(and {left} {right})"
        elif left:
            reconstructed = left
        elif right:
            reconstructed = right
        else:
            reconstructed = "(true)"

        logger.debug(
            "reconstruct_from_parts: reconstructed formula (len=%d)", len(reconstructed)
        )
        return reconstructed

    # --- verify_bisection ---------------------------------------------------

    def verify_bisection(
        self, formula: str, parts: tuple[str, str]
    ) -> bool:
        """Verify that a bisection covers all significant tokens of the original.

        Tokenises the original formula and each part, then checks that every
        token from the original appears in at least one of the parts.
        Parentheses and whitespace are ignored.  This is a syntactic
        coverage check used by the copilot repair pipeline to confirm that
        no sub-formula has been dropped.

        Parameters
        ----------
        formula:
            The original SMT-LIB2 formula string.
        parts:
            A pair of sub-formula strings.

        Returns
        -------
        bool
            True if every token in ``formula`` appears in ``parts[0]`` or
            ``parts[1]``.
        """
        clean = lambda s: set(s.replace("(", " ").replace(")", " ").lower().split())
        original_tokens = clean(formula)
        covered_tokens = clean(parts[0]) | clean(parts[1])

        # Structural tokens like connectives may not appear after splitting
        structural = {"and", "or", "implies", "=>", "not", "true", "false"}
        missing = (original_tokens - covered_tokens) - structural

        if missing:
            logger.debug(
                "verify_bisection: %d tokens missing from parts: %s",
                len(missing),
                sorted(missing)[:5],
            )
            return False

        logger.debug("verify_bisection: all tokens covered")
        return True

    # --- cost_of_split ------------------------------------------------------

    def cost_of_split(self, formula: str) -> int:
        """Estimate the cost of bisecting a formula.

        Uses the formula length divided by 10 as a rough proxy for complexity.
        Longer formulas have more sub-terms and thus higher bisection cost.
        Used by the copilot scheduler to decide whether bisection is cheaper
        than abstraction.

        Parameters
        ----------
        formula:
            An SMT-LIB2 formula string.

        Returns
        -------
        int
            The estimated bisection cost (minimum 1).
        """
        cost = max(1, len(formula) // 10)
        logger.debug("cost_of_split: formula len=%d → cost=%d", len(formula), cost)
        return cost

    # --- copilot_bisection_hint ---------------------------------------------

    def copilot_bisection_hint(self, formula: str) -> str:
        """Return a structured copilot bisection hint for a formula.

        Performs a bisection and describes the result, including the split
        strategy used, the sizes of the parts, and the verification outcome.

        Parameters
        ----------
        formula:
            An SMT-LIB2 formula string to hint about.

        Returns
        -------
        str
            A multi-line copilot hint string.
        """
        parts = self.bisect(formula)
        verified = self.verify_bisection(formula, parts)
        cost = self.cost_of_split(formula)
        dc = classify_formula_fragment(formula)
        reconstructed = self.reconstruct_from_parts(parts)

        lines = [
            "=== Copilot Bisection Hint ===",
            f"Formula length      : {len(formula)}",
            f"Decidability class  : {dc.value}",
            f"Split cost estimate : {cost}",
            f"Verification passed : {verified}",
            "",
            f"Part 1 (len={len(parts[0])}): {parts[0][:80]}{'...' if len(parts[0]) > 80 else ''}",
            f"Part 2 (len={len(parts[1])}): {parts[1][:80]}{'...' if len(parts[1]) > 80 else ''}",
            "",
            f"Reconstructed (len={len(reconstructed)}): {reconstructed[:80]}",
            "",
            "Recommendation: apply classify_formula_fragment() to each part",
            "and solve them independently if they fall in decidable fragments.",
            "==============================",
        ]
        return "\n".join(lines)

    # --- internal -----------------------------------------------------------

    def _log_bisection(
        self,
        formula: str,
        parts: tuple[str, str],
        strategy: str,
    ) -> None:
        """Record a bisection attempt in ``bisection_log``."""
        self.bisection_log.append({
            "formula_len": len(formula),
            "strategy": strategy,
            "part1_len": len(parts[0]),
            "part2_len": len(parts[1]),
            "timestamp": time.time(),
        })


# ============================================================================
# Section 4: CountermodelAggregator
# ============================================================================

class CountermodelAggregator:
    """Aggregates and deduplicates batches of countermodels into obstructions.

    CountermodelAggregator converts a list of raw
    :class:`~jugeo.solver.countermodels.Countermodel` instances into a
    deduplicated, prioritised list of
    :class:`~jugeo.encodings.structural_frontier.models.CountermodelObstruction`
    values.  It clusters obstructions by their inside fragment for targeted
    repair scheduling, and ranks by confidence for copilot display.

    Deduplication is performed by obstruction fingerprint (hash of
    violated_invariant and obstruction_id).  Aggregation results are cached
    to avoid redundant processing of the same batch.

    Copilot integration is available via :meth:`copilot_aggregate_report`.
    """

    def __init__(self) -> None:
        """Initialise the aggregator with an empty aggregation cache."""
        self.aggregation_cache: dict[str, list[CountermodelObstruction]] = {}
        logger.debug("CountermodelAggregator initialised")

    # --- aggregate ----------------------------------------------------------

    def aggregate(
        self, countermodels: list[Any]
    ) -> list[CountermodelObstruction]:
        """Convert and deduplicate a list of countermodels into obstructions.

        For each countermodel, creates a
        :class:`~jugeo.encodings.structural_frontier.models.CountermodelObstruction`
        by inspecting available attributes.  Deduplicates by
        ``fingerprint()``, then returns the deduplicated list sorted by
        confidence descending.

        Parameters
        ----------
        countermodels:
            A list of :class:`~jugeo.solver.countermodels.Countermodel`
            instances (or any object with ``assignment`` and ``formula``
            attributes).

        Returns
        -------
        list[CountermodelObstruction]
            A deduplicated, confidence-sorted list of obstructions.
        """
        batch_key = _batch_fingerprint(countermodels)
        if batch_key in self.aggregation_cache:
            logger.debug("aggregate: cache hit %s", batch_key[:8])
            return self.aggregation_cache[batch_key]

        raw_obstructions: list[CountermodelObstruction] = []
        for cm in countermodels:
            try:
                obs = _countermodel_to_obstruction(cm)
                raw_obstructions.append(obs)
            except Exception as exc:
                logger.debug("aggregate: failed to convert countermodel: %s", exc)

        deduplicated = self.deduplicate(raw_obstructions)
        ranked = self.rank_by_impact(deduplicated)

        self.aggregation_cache[batch_key] = ranked
        logger.debug(
            "aggregate: %d countermodels → %d obstructions (after dedup)",
            len(countermodels),
            len(ranked),
        )
        return ranked

    # --- cluster_by_fragment ------------------------------------------------

    def cluster_by_fragment(
        self, obstructions: list[CountermodelObstruction]
    ) -> dict[str, list[CountermodelObstruction]]:
        """Group obstructions by their inside fragment (repair target).

        Groups by ``repair_frontier.inside_fragment`` so that the repair
        scheduler can process all obstructions targeting the same decidable
        fragment together.  Useful for copilot batch repair reports.

        Parameters
        ----------
        obstructions:
            A list of
            :class:`~jugeo.encodings.structural_frontier.models.CountermodelObstruction`
            values to cluster.

        Returns
        -------
        dict[str, list[CountermodelObstruction]]
            Mapping from inside fragment name to list of obstructions.
        """
        clusters: dict[str, list[CountermodelObstruction]] = defaultdict(list)
        for obs in obstructions:
            key = obs.repair_frontier.inside_fragment
            clusters[key].append(obs)

        logger.debug(
            "cluster_by_fragment: %d clusters from %d obstructions",
            len(clusters),
            len(obstructions),
        )
        return dict(clusters)

    # --- rank_by_impact -----------------------------------------------------

    def rank_by_impact(
        self, obstructions: list[CountermodelObstruction]
    ) -> list[CountermodelObstruction]:
        """Sort obstructions by confidence descending (highest impact first).

        Higher confidence indicates the aggregator is more certain about the
        failure class and repair path.  Secondary sort: lower crossing cost
        (cheaper repairs first within the same confidence band).

        Parameters
        ----------
        obstructions:
            A list of
            :class:`~jugeo.encodings.structural_frontier.models.CountermodelObstruction`
            values to rank.

        Returns
        -------
        list[CountermodelObstruction]
            A new list sorted by (confidence descending, crossing_cost ascending).
        """
        ranked = sorted(
            obstructions,
            key=lambda o: (-o.confidence, o.repair_frontier.crossing_cost),
        )
        logger.debug("rank_by_impact: ranked %d obstructions", len(ranked))
        return ranked

    # --- deduplicate --------------------------------------------------------

    def deduplicate(
        self, obstructions: list[CountermodelObstruction]
    ) -> list[CountermodelObstruction]:
        """Remove obstructions with duplicate (violated_invariant, obstruction_id) hashes.

        Computes the ``fingerprint()`` of each obstruction and keeps only the
        first occurrence of each fingerprint.  This prevents the same
        countermodel from generating duplicate repair tickets in batch
        processing scenarios.

        Parameters
        ----------
        obstructions:
            A list of
            :class:`~jugeo.encodings.structural_frontier.models.CountermodelObstruction`
            values to deduplicate.

        Returns
        -------
        list[CountermodelObstruction]
            A new list with duplicate fingerprints removed.
        """
        seen: set[str] = set()
        unique: list[CountermodelObstruction] = []
        for obs in obstructions:
            fp = obs.fingerprint()
            if fp not in seen:
                seen.add(fp)
                unique.append(obs)
            else:
                logger.debug(
                    "deduplicate: dropping duplicate obstruction %s (fp=%s)",
                    obs.obstruction_id[:8],
                    fp,
                )

        logger.debug(
            "deduplicate: %d → %d after deduplication",
            len(obstructions),
            len(unique),
        )
        return unique

    # --- copilot_aggregate_report -------------------------------------------

    def copilot_aggregate_report(
        self, obstructions: list[CountermodelObstruction]
    ) -> str:
        """Return a structured copilot aggregation report.

        Summarises the number of obstructions, their failure class distribution,
        fragment cluster sizes, and the top-ranked obstruction.  Formatted for
        display in IDE copilot extensions and CI reports.

        Parameters
        ----------
        obstructions:
            A list of
            :class:`~jugeo.encodings.structural_frontier.models.CountermodelObstruction`
            values to report on.

        Returns
        -------
        str
            A multi-section report string.
        """
        if not obstructions:
            return (
                "=== Copilot Aggregate Report ===\n"
                "No obstructions to report.\n"
                "================================"
            )

        clusters = self.cluster_by_fragment(obstructions)
        fc_counts: dict[str, int] = defaultdict(int)
        for obs in obstructions:
            fc_counts[obs.failure_class.value] += 1

        top = obstructions[0] if obstructions else None
        resolvable = sum(1 for o in obstructions if o.is_resolvable())

        lines = [
            "=== Copilot Aggregate Report ===",
            f"Total obstructions  : {len(obstructions)}",
            f"Resolvable          : {resolvable}",
            f"Manual review       : {len(obstructions) - resolvable}",
            "",
            "Failure class distribution:",
        ]
        for fc_val, count in sorted(fc_counts.items(), key=lambda x: -x[1]):
            lines.append(f"  {fc_val:35s} : {count}")
        lines += [
            "",
            "Fragment clusters:",
        ]
        for frag, obs_list in sorted(clusters.items()):
            lines.append(f"  {frag!r:30s} : {len(obs_list)} obstructions")
        if top:
            lines += [
                "",
                f"Top obstruction (confidence={top.confidence:.2f}):",
                f"  {top.copilot_summary()}",
            ]
        lines.append("================================")
        return "\n".join(lines)


# ============================================================================
# Section 5: RepairPriorityScheduler
# ============================================================================

class RepairPriorityScheduler:
    """Schedules obstructions into a prioritised repair queue.

    RepairPriorityScheduler orders
    :class:`~jugeo.encodings.structural_frontier.models.CountermodelObstruction`
    values by a combination of confidence (higher is more actionable) and
    repair cost (lower is cheaper to fix).  It groups obstructions by their
    most likely repair action type for batch repair execution, and estimates
    the total repair effort for CI budget planning.

    Schedule decisions are recorded in ``schedule_log`` for audit and
    copilot replay.  Copilot integration is available via
    :meth:`copilot_schedule_report`.
    """

    def __init__(self) -> None:
        """Initialise the scheduler with an empty schedule log."""
        self.schedule_log: list[dict[str, Any]] = []
        logger.debug("RepairPriorityScheduler initialised")

    # --- schedule -----------------------------------------------------------

    def schedule(
        self, obstructions: list[CountermodelObstruction]
    ) -> list[CountermodelObstruction]:
        """Return obstructions sorted by priority (confidence desc, then cost asc).

        Primary sort key: ``-confidence`` (highest confidence first, since
        those repairs are most actionable).  Secondary sort key:
        ``repair_frontier.crossing_cost`` (cheaper first within the same
        confidence band).  Logs the schedule to ``schedule_log``.

        Parameters
        ----------
        obstructions:
            A list of
            :class:`~jugeo.encodings.structural_frontier.models.CountermodelObstruction`
            values to schedule.

        Returns
        -------
        list[CountermodelObstruction]
            A new list sorted by priority.
        """
        scheduled = sorted(
            obstructions,
            key=lambda o: (
                -o.confidence,
                o.repair_frontier.crossing_cost,
                o.most_likely_repair().cost,
            ),
        )
        self.schedule_log.append({
            "count": len(obstructions),
            "scheduled_at": time.time(),
            "total_cost": self.estimate_total_cost(scheduled),
        })
        logger.debug(
            "schedule: scheduled %d obstructions, total_cost=%d",
            len(scheduled),
            self.estimate_total_cost(scheduled),
        )
        return scheduled

    # --- prioritize_by_cost -------------------------------------------------

    def prioritize_by_cost(
        self, obstructions: list[CountermodelObstruction]
    ) -> list[CountermodelObstruction]:
        """Sort obstructions by repair frontier crossing cost ascending.

        Returns a list sorted purely by ``repair_frontier.crossing_cost``
        (cheapest repairs first), ignoring confidence.  Used when the copilot
        pipeline is operating in cost-minimisation mode.

        Parameters
        ----------
        obstructions:
            A list of
            :class:`~jugeo.encodings.structural_frontier.models.CountermodelObstruction`
            values to sort.

        Returns
        -------
        list[CountermodelObstruction]
            A new list sorted by crossing cost ascending.
        """
        by_cost = sorted(
            obstructions,
            key=lambda o: (
                o.repair_frontier.crossing_cost,
                o.most_likely_repair().cost,
            ),
        )
        logger.debug("prioritize_by_cost: sorted %d obstructions", len(by_cost))
        return by_cost

    # --- group_by_repair_action ---------------------------------------------

    def group_by_repair_action(
        self, obstructions: list[CountermodelObstruction]
    ) -> dict[str, list[CountermodelObstruction]]:
        """Group obstructions by the value of their most likely repair action.

        Uses :meth:`CountermodelObstruction.most_likely_repair` to determine
        the primary repair type for each obstruction, then groups accordingly.
        This allows the copilot pipeline to batch-execute all obstructions
        sharing the same repair type.

        Parameters
        ----------
        obstructions:
            A list of
            :class:`~jugeo.encodings.structural_frontier.models.CountermodelObstruction`
            values to group.

        Returns
        -------
        dict[str, list[CountermodelObstruction]]
            Mapping from repair action type value to list of obstructions.
        """
        groups: dict[str, list[CountermodelObstruction]] = defaultdict(list)
        for obs in obstructions:
            action_val = obs.most_likely_repair().action_type.value
            groups[action_val].append(obs)

        logger.debug(
            "group_by_repair_action: %d groups from %d obstructions",
            len(groups),
            len(obstructions),
        )
        return dict(groups)

    # --- estimate_total_cost ------------------------------------------------

    def estimate_total_cost(
        self, obstructions: list[CountermodelObstruction]
    ) -> int:
        """Estimate the total repair cost for a list of obstructions.

        Sums the ``crossing_cost`` of each obstruction's ``repair_frontier``
        plus the cost of the most likely repair action.  Used by CI pipelines
        for budget estimation and by copilot to prioritise sprint planning.

        Parameters
        ----------
        obstructions:
            A list of
            :class:`~jugeo.encodings.structural_frontier.models.CountermodelObstruction`
            values to estimate for.

        Returns
        -------
        int
            The total estimated repair cost.
        """
        total = sum(
            o.repair_frontier.crossing_cost + o.most_likely_repair().cost
            for o in obstructions
        )
        logger.debug(
            "estimate_total_cost: total=%d for %d obstructions", total, len(obstructions)
        )
        return total

    # --- copilot_schedule_report --------------------------------------------

    def copilot_schedule_report(
        self, obstructions: list[CountermodelObstruction]
    ) -> str:
        """Return a structured copilot schedule report.

        Summarises the scheduled repair order, total cost estimate, repair
        action group sizes, and the top three scheduled obstructions.
        Formatted for display in IDE copilot extensions and CI dashboards.

        Parameters
        ----------
        obstructions:
            A list of
            :class:`~jugeo.encodings.structural_frontier.models.CountermodelObstruction`
            values to report on.

        Returns
        -------
        str
            A multi-section report string.
        """
        if not obstructions:
            return (
                "=== Copilot Schedule Report ===\n"
                "No obstructions to schedule.\n"
                "==============================="
            )

        scheduled = self.schedule(obstructions)
        total_cost = self.estimate_total_cost(scheduled)
        groups = self.group_by_repair_action(scheduled)
        by_cost = self.prioritize_by_cost(scheduled)
        cheapest = by_cost[0] if by_cost else None

        lines = [
            "=== Copilot Schedule Report ===",
            f"Total obstructions  : {len(scheduled)}",
            f"Total repair cost   : {total_cost}",
            f"Repair action groups: {len(groups)}",
            "",
            "Repair action group sizes:",
        ]
        for action_val, group in sorted(groups.items(), key=lambda x: -len(x[1])):
            group_cost = self.estimate_total_cost(group)
            lines.append(
                f"  {action_val:40s} : {len(group):3d} obstructions, cost={group_cost}"
            )
        lines += ["", "Top 3 scheduled obstructions:"]
        for i, obs in enumerate(scheduled[:3], start=1):
            action = obs.most_likely_repair()
            lines.append(
                f"  {i}. [{obs.obstruction_id[:8]}] "
                f"conf={obs.confidence:.2f} "
                f"frontier_cost={obs.repair_frontier.crossing_cost} "
                f"action={action.action_type.value} "
                f"action_cost={action.cost}"
            )
        if cheapest:
            lines += [
                "",
                f"Cheapest single repair: [{cheapest.obstruction_id[:8]}] "
                f"cost={cheapest.repair_frontier.crossing_cost + cheapest.most_likely_repair().cost}",
            ]
        lines.append("===============================")
        return "\n".join(lines)


# ============================================================================
# Section 6: Internal helpers
# ============================================================================

def _batch_fingerprint(countermodels: list[Any]) -> str:
    """Return a stable fingerprint for a batch of countermodels.

    Used as a cache key by :class:`CountermodelAggregator` to avoid re-
    processing the same batch.  Hashes the repr of each countermodel.

    Parameters
    ----------
    countermodels:
        A list of countermodel objects.

    Returns
    -------
    str
        A 16-character hex string.
    """
    batch_repr = json.dumps(
        [repr(cm)[:100] for cm in countermodels], sort_keys=True
    )
    return hashlib.sha256(batch_repr.encode()).hexdigest()[:16]


def _countermodel_to_obstruction(countermodel: Any) -> CountermodelObstruction:
    """Convert a raw countermodel to a CountermodelObstruction.

    Inspects the countermodel for ``formula``, ``assignment``, and other
    attributes to populate the obstruction fields.  Uses
    :func:`classify_formula_fragment` to determine the decidability class.
    This is the lightweight version used by :class:`CountermodelAggregator`;
    the full pipeline should use
    :class:`~jugeo.encodings.structural_frontier.countermodel_to_repair.CountermodelToRepair`.

    Parameters
    ----------
    countermodel:
        A countermodel object with optional ``formula`` and ``assignment``
        attributes.

    Returns
    -------
    CountermodelObstruction
        A partially populated obstruction.
    """
    formula_str = ""
    try:
        formula_str = str(getattr(countermodel, "formula", ""))
    except Exception:
        pass

    assignment: dict[str, Any] = {}
    try:
        assignment = dict(getattr(countermodel, "assignment", {}))
    except Exception:
        pass

    dc = classify_formula_fragment(formula_str) if formula_str else DecidabilityClass.UNKNOWN
    boundary = make_default_boundary()

    # Refine boundary based on decidability class
    if dc == DecidabilityClass.UNDECIDABLE:
        boundary = make_default_boundary("qf_lia", "qf_nia", cost=3)
    elif dc == DecidabilityClass.SEMI_DECIDABLE:
        boundary = make_default_boundary("qf_lra", "qf_nra", cost=2)

    # Build violated invariant string from first few assignments
    inv_parts = [f"{k}={v}" for k, v in list(assignment.items())[:3]]
    violated = "; ".join(inv_parts) if inv_parts else "unknown"

    confidence = 0.5 + 0.1 * min(5, len(assignment))

    return CountermodelObstruction(
        countermodel=countermodel,
        failure_class=FailureClass.UNKNOWN,
        violated_invariant=violated,
        repair_frontier=boundary,
        suggested_actions=[],
        confidence=min(0.95, confidence),
        obstruction_id=str(uuid.uuid4()),
    )


# ============================================================================
# Section 7: Public exports
# ============================================================================

__all__ = [
    # Top-level functions
    "classify_formula_fragment",
    "compute_frontier_boundary",
    "find_cheapest_encoding",
    "batch_classify",
    # Classes
    "FrontierExplorer",
    "DecidabilityBisector",
    "CountermodelAggregator",
    "RepairPriorityScheduler",
    # Internal helpers (exported for testing)
    "_batch_fingerprint",
    "_countermodel_to_obstruction",
    # Judgment-geometric cross-references
    "decidability_over_site",
    "countermodel_frontier",
    "repair_frontier_encoding",
]


# ---------------------------------------------------------------------------
# Judgment-geometric cross-references
# ---------------------------------------------------------------------------

try:
    from jugeo.geometry import site as _geo_site
except ImportError:
    _geo_site = None  # type: ignore[assignment]

try:
    from jugeo.solver import countermodels as _countermodels_mod
except ImportError:
    _countermodels_mod = None  # type: ignore[assignment]

try:
    from jugeo.problem_modes import repair_semantics as _repair_semantics
except ImportError:
    _repair_semantics = None  # type: ignore[assignment]


def decidability_over_site(site: Any) -> dict[str, Any]:
    """Classify decidability of the frontier at a geometric site.

    Bridges the structural-frontier decidability analysis with the
    geometry subsystem by evaluating the decidability class of the
    formulas associated with *site*.

    Parameters
    ----------
    site:
        A geometric site object from ``jugeo.geometry.site``.

    Returns
    -------
    dict[str, Any]
        A dict with ``"site"``, ``"decidability_class"``, and ``"fragment"``
        keys.
    """
    if _geo_site is None:
        raise RuntimeError("jugeo.geometry.site is not available")
    formulas = _geo_site.formulas_at(site) if hasattr(_geo_site, "formulas_at") else []
    if formulas:
        dc = classify_formula_fragment(formulas[0])
        return {"site": site, "decidability_class": str(dc), "fragment": str(formulas[0])}
    return {"site": site, "decidability_class": "unknown", "fragment": None}


def countermodel_frontier(countermodel: Any) -> dict[str, Any]:
    """Map a countermodel into a frontier obstruction encoding.

    Uses the solver countermodel subsystem to extract the countermodel
    payload and converts it into a frontier-compatible obstruction record.

    Parameters
    ----------
    countermodel:
        A countermodel object from ``jugeo.solver.countermodels``.

    Returns
    -------
    dict[str, Any]
        A dict with ``"countermodel"``, ``"obstruction"``, and
        ``"frontier_boundary"`` keys.
    """
    if _countermodels_mod is None:
        raise RuntimeError("jugeo.solver.countermodels is not available")
    assignment = _countermodels_mod.to_assignment(countermodel) if hasattr(_countermodels_mod, "to_assignment") else {}
    obstruction = _countermodel_to_obstruction(countermodel, assignment)
    return {
        "countermodel": countermodel,
        "obstruction": obstruction,
        "frontier_boundary": getattr(obstruction, "repair_frontier", None),
    }


def repair_frontier_encoding(repair_plan: Any) -> dict[str, Any]:
    """Produce a frontier encoding from a repair plan.

    Bridges the problem-modes repair semantics into the structural
    frontier by converting a repair plan into an encoding that the
    frontier explorer can schedule.

    Parameters
    ----------
    repair_plan:
        A repair plan from ``jugeo.problem_modes.repair_semantics``.

    Returns
    -------
    dict[str, Any]
        A dict with ``"repair_plan"``, ``"encoding"``, and ``"priority"``
        keys.
    """
    if _repair_semantics is None:
        raise RuntimeError("jugeo.problem_modes.repair_semantics is not available")
    priority = _repair_semantics.priority(repair_plan) if hasattr(_repair_semantics, "priority") else 0
    encoding = _repair_semantics.to_encoding(repair_plan) if hasattr(_repair_semantics, "to_encoding") else {"raw": str(repair_plan)}
    return {
        "repair_plan": repair_plan,
        "encoding": encoding,
        "priority": priority,
    }
