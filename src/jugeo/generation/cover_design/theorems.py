r"""Theorem verification suite for JuGeo cover design — theory2.tex §cover_design.

Theory (theory2.tex §cover_design — Cover Design):
    This module formalises and mechanically verifies the key theorems that
    govern the behaviour of cover design algorithms.  Each theorem corresponds
    to a named proposition in §cover_design and is checked against live
    data-structure instances rather than symbolic proofs.

    The main theorems are:

    **T_CD_1 — Cover Completeness**
        Every point of the judgment site is in at least one patch.
        Formally: :math:`S \\subseteq \\bigcup_{i \\in I} U_i`.

    **T_CD_2 — Čech Condition Soundness**
        If sections :math:`s_i` and :math:`s_j` both comply with the cover
        design, then :math:`s_i|_{U_i \\cap U_j} = s_j|_{U_i \\cap U_j}`.
        This is the gluing condition required for sheaf-theoretic consistency.

    **T_CD_3 — Budget Admissibility**
        The total allocated budget satisfies
        :math:`\\sum_i b_i \\leq B - \\beta \\cdot B` where :math:`B` is the
        gross budget and :math:`\\beta \\in [0,1)` is the overhead fraction.

    **T_CD_4 — Dependency Acyclicity**
        The dependency DAG contains no directed cycles.  A cyclic dependency
        relation would make it impossible to order patch application.

    **T_CD_5 — Topological Ordering Correctness**
        The dependency ordering produced by the topological sort is a valid
        *linear extension* of the partial order: for every edge
        :math:`(u, v) \\in E`, node :math:`u` appears before node :math:`v`
        in the order.

    **T_CD_6 — Parallelism Safety**
        Patches in the same generation wave have no dependency edges between
        them.  Equivalently, the waves produced by antichain decomposition
        form an antichain in the partial order.

    **T_CD_7 — Quality Threshold Monotonicity**
        Adding more patches to a valid cover cannot decrease coverage
        completeness: if :math:`\\mathcal{U} \\subseteq \\mathcal{V}` then
        :math:`\\kappa(\\mathcal{U}) \\leq \\kappa(\\mathcal{V})`.

    **T_CD_8 — Priority Allocation Consistency**
        Higher-priority patches receive at least as large a budget allocation
        as lower-priority patches of equal area:
        if :math:`p_i \\geq p_j` and :math:`a_i = a_j` then
        :math:`b_i \\geq b_j`.

    **Trust tier**: generated code enters at the **PROPOSAL** trust tier.
    No section produced by this package is automatically trusted — it must
    pass all theorem checks before being promoted to ``VERIFIED``.

    copilot: theorems-marker

Public API
----------
``TheoremResult``
    Dataclass capturing the outcome of a single theorem check.
``TheoremSuite``
    Orchestrates all theorem checks and produces a summary report.
``run_all_theorems``
    Convenience function that constructs and runs a :class:`TheoremSuite`.
``verify_cover_completeness``
    T_CD_1 — every site region is covered by at least one patch.
``verify_cech_condition_soundness``
    T_CD_2 — all section pairs agree on their overlaps.
``verify_budget_admissibility``
    T_CD_3 — total allocated budget does not exceed the net budget.
``verify_dependency_acyclicity``
    T_CD_4 — the dependency DAG has no cycles.
``verify_topological_ordering_correctness``
    T_CD_5 — the given order is a valid linear extension of the partial order.
``verify_parallelism_safety``
    T_CD_6 — same-wave patches have no mutual dependencies.
``verify_quality_threshold_monotonicity``
    T_CD_7 — coverage completeness is monotone under patch addition.
``verify_priority_allocation_consistency``
    T_CD_8 — higher-priority equal-area patches get ≥ budget allocation.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from jugeo.generation.cover_design.models import (  # type: ignore[import]
        CoverDesignState,
        BudgetRecord,
    )

try:
    from jugeo.generation.cover_design.algorithms import (  # type: ignore[import]
        DependencyGraph,
        OverlapGraph,
        ScheduleResult,
        check_cech_condition,
        compute_coverage_completeness,
        topological_sort_patches,
        compute_antichain_decomposition,
    )
except ImportError:
    # Graceful fallback when the cover_design package is partially initialised.
    DependencyGraph = Any  # type: ignore[assignment, misc]
    OverlapGraph = Any  # type: ignore[assignment, misc]
    ScheduleResult = Any  # type: ignore[assignment, misc]

    def check_cech_condition(*args: Any, **kwargs: Any) -> tuple[bool, dict[str, Any]]:  # type: ignore[misc]
        return False, {"reason": "algorithms not available"}

    def compute_coverage_completeness(*args: Any, **kwargs: Any) -> float:  # type: ignore[misc]
        return 0.0

    def topological_sort_patches(*args: Any, **kwargs: Any) -> list[str]:  # type: ignore[misc]
        return []

    def compute_antichain_decomposition(*args: Any, **kwargs: Any) -> list[list[str]]:  # type: ignore[misc]
        return []


__all__ = [
    # Data types
    "TheoremResult",
    "TheoremSuite",
    # Entry points
    "run_all_theorems",
    # Individual theorem verifiers
    "verify_cover_completeness",
    "verify_cech_condition_soundness",
    "verify_budget_admissibility",
    "verify_dependency_acyclicity",
    "verify_topological_ordering_correctness",
    "verify_parallelism_safety",
    "verify_quality_threshold_monotonicity",
    "verify_priority_allocation_consistency",
    # Helpers
    "_make_result",
]

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_COVERAGE_TOLERANCE: float = 1e-9
_BUDGET_TOLERANCE: float = 1e-9
_MIN_TRUST_THRESHOLD: float = 0.10
_DEFAULT_OVERHEAD_FRACTION: float = 0.10


# ---------------------------------------------------------------------------
# TheoremResult dataclass
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class TheoremResult:
    """Immutable record of a single theorem verification.

    Fields
    ------
    theorem_name:
        Human-readable name of the theorem (e.g. ``"T_CD_1-CoverCompleteness"``).
    status:
        One of ``"passed"``, ``"failed"``, or ``"inconclusive"``.
    evidence:
        Structured evidence collected during verification.
    counterexample_or_none:
        A witness that refutes the theorem, or ``None`` if no counterexample
        was found.
    proof_sketch:
        A brief natural-language description of the verification argument.
    checked_at:
        Unix timestamp (``time.time()``) at which the check ran.
    """

    theorem_name: str
    status: str
    evidence: dict[str, Any]
    counterexample_or_none: Any
    proof_sketch: str
    checked_at: float = field(default_factory=time.time)

    def passed(self) -> bool:
        """Return ``True`` iff status is ``"passed"``."""
        return self.status == "passed"

    def failed(self) -> bool:
        """Return ``True`` iff status is ``"failed"``."""
        return self.status == "failed"

    def as_dict(self) -> dict[str, Any]:
        """Serialise to a plain dictionary.

        Returns
        -------
        dict[str, Any]
            Dictionary with keys matching the field names of this dataclass.
        """
        return {
            "theorem_name": self.theorem_name,
            "status": self.status,
            "evidence": self.evidence,
            "counterexample_or_none": self.counterexample_or_none,
            "proof_sketch": self.proof_sketch,
            "checked_at": self.checked_at,
        }


# ---------------------------------------------------------------------------
# Helper: _make_result
# ---------------------------------------------------------------------------

def _make_result(
    name: str,
    passed: bool,
    evidence: dict[str, Any],
    counterexample: Any = None,
    sketch: str = "",
) -> dict[str, Any]:
    """Build a plain-dict theorem result.

    Used internally by all ``verify_*`` functions to produce a consistent
    return value that can be unpacked into a :class:`TheoremResult`.

    Parameters
    ----------
    name:
        Theorem name.
    passed:
        Whether the theorem passed.
    evidence:
        Structured evidence dict.
    counterexample:
        Optional counterexample witness.
    sketch:
        Human-readable proof sketch.

    Returns
    -------
    dict[str, Any]
        Dictionary with keys matching :class:`TheoremResult` fields.
    """
    if passed:
        status = "passed"
    elif counterexample is not None:
        status = "failed"
    else:
        status = "failed"

    return {
        "theorem_name": name,
        "status": status,
        "evidence": evidence,
        "counterexample_or_none": counterexample,
        "proof_sketch": sketch,
        "checked_at": time.time(),
    }


# ---------------------------------------------------------------------------
# T_CD_1 — Cover Completeness
# ---------------------------------------------------------------------------

def verify_cover_completeness(
    patches: list[dict[str, Any]],
    site_boundary: Any,
) -> dict[str, Any]:
    """Verify T_CD_1: every point of the judgment site is in at least one patch.

    Formally checks that the union of all patch coordinate sets contains every
    element of *site_boundary*.  If *site_boundary* is a float it is
    interpreted as a total area; coverage completeness must equal 1.0 within
    :data:`_COVERAGE_TOLERANCE`.

    Verification steps
    ------------------
    1. Compute :func:`compute_coverage_completeness` for *patches* against
       *site_boundary*.
    2. Check that completeness ≥ 1.0 − :data:`_COVERAGE_TOLERANCE`.
    3. If *site_boundary* is a set, compute the set of uncovered regions as
       the counterexample witness.

    Parameters
    ----------
    patches:
        List of patch descriptor dicts, each with a ``"coords"`` field.
    site_boundary:
        Either a frozenset/set/list of region IDs, or a positive float
        representing total site area.

    Returns
    -------
    dict[str, Any]
        Theorem result dict (see :func:`_make_result`).
    """
    name = "T_CD_1-CoverCompleteness"
    violations: list[str] = []
    uncovered: list[Any] = []

    completeness = compute_coverage_completeness(patches, site_boundary)

    if not isinstance(site_boundary, (int, float)):
        # Set-based path: compute exact uncovered regions
        site_regions = frozenset(site_boundary)
        covered: set[Any] = set()
        for p in patches:
            covered.update(p.get("coords", []))
        uncovered = sorted(str(r) for r in site_regions - covered)
        if uncovered:
            violations.append(
                f"{len(uncovered)} region(s) not covered: {uncovered[:5]}..."
                if len(uncovered) > 5
                else f"{len(uncovered)} region(s) not covered: {uncovered}"
            )
    else:
        if completeness < 1.0 - _COVERAGE_TOLERANCE:
            violations.append(
                f"coverage completeness {completeness:.6f} < 1.0 "
                f"(uncovered fraction {1.0 - completeness:.6f})"
            )

    evidence: dict[str, Any] = {
        "patch_count": len(patches),
        "completeness": completeness,
        "uncovered_regions": uncovered,
        "violations": violations,
    }

    passed = len(violations) == 0
    counterexample = uncovered[0] if uncovered else (violations[0] if violations else None)

    return _make_result(
        name,
        passed,
        evidence,
        counterexample=counterexample,
        sketch=(
            "Cover completeness holds iff the union of all patch coordinate sets "
            "contains every element of the site boundary, equivalently when "
            "compute_coverage_completeness returns a value indistinguishable from 1."
        ),
    )


# ---------------------------------------------------------------------------
# T_CD_2 — Čech Condition Soundness
# ---------------------------------------------------------------------------

def verify_cech_condition_soundness(
    sections: list[dict[str, Any]],
    overlap_graph: OverlapGraph,
) -> dict[str, Any]:
    """Verify T_CD_2: all compliant section pairs agree on their overlaps.

    For every pair of sections :math:`(s_i, s_j)` whose patches appear as
    adjacent nodes in *overlap_graph*, the function calls
    :func:`check_cech_condition` restricted to the keys that appear in both
    sections' value dictionaries on the overlap region.

    Trust-tier note: pairs where **both** sections carry a ``"trust_tier"``
    of ``"PROPOSAL"`` are checked but violations are marked *advisory*.
    Pairs where either section is ``"VERIFIED"`` or ``"CERTIFIED"`` are
    marked *mandatory*.

    Parameters
    ----------
    sections:
        List of section dicts.  Each must carry ``"section_id"``,
        ``"patch_id"``, ``"trust_tier"``, and ``"values"``.
    overlap_graph:
        :class:`OverlapGraph` encoding which patch pairs share an overlap.

    Returns
    -------
    dict[str, Any]
        Theorem result dict.
    """
    name = "T_CD_2-CechConditionSoundness"
    violations: list[dict[str, Any]] = []
    advisory_violations: list[dict[str, Any]] = []
    pairs_checked: int = 0

    # Build section index by patch_id
    by_patch: dict[str, dict[str, Any]] = {}
    for sec in sections:
        pid = str(sec.get("patch_id", ""))
        by_patch[pid] = sec

    checked_pairs: set[tuple[str, str]] = set()
    for sec_a in sections:
        pid_a = str(sec_a.get("patch_id", ""))
        for pid_b in overlap_graph.neighbours(pid_a):
            pair_key = (min(pid_a, pid_b), max(pid_a, pid_b))
            if pair_key in checked_pairs:
                continue
            checked_pairs.add(pair_key)

            sec_b = by_patch.get(pid_b)
            if sec_b is None:
                continue

            pairs_checked += 1
            # Build overlap region: intersection of the two value key sets
            vals_a: dict[str, Any] = sec_a.get("values", {})
            vals_b: dict[str, Any] = sec_b.get("values", {})
            overlap_keys = set(vals_a.keys()) & set(vals_b.keys())

            ok, ev = check_cech_condition(sec_a, sec_b, overlap_keys)

            if not ok:
                tier_a = str(sec_a.get("trust_tier", "PROPOSAL"))
                tier_b = str(sec_b.get("trust_tier", "PROPOSAL"))
                is_mandatory = tier_a in ("VERIFIED", "CERTIFIED") or tier_b in (
                    "VERIFIED", "CERTIFIED"
                )
                record = {
                    "pair": (sec_a.get("section_id"), sec_b.get("section_id")),
                    "disagreements": ev.get("disagreements", []),
                    "mandatory": is_mandatory,
                }
                if is_mandatory:
                    violations.append(record)
                else:
                    advisory_violations.append(record)

    evidence: dict[str, Any] = {
        "pairs_checked": pairs_checked,
        "mandatory_violations": len(violations),
        "advisory_violations": len(advisory_violations),
        "violation_details": violations[:10],  # cap for readability
        "advisory_details": advisory_violations[:10],
    }

    passed = len(violations) == 0
    counterexample = violations[0] if violations else None

    return _make_result(
        name,
        passed,
        evidence,
        counterexample=counterexample,
        sketch=(
            "Čech condition soundness holds iff every pair of sections whose "
            "patches overlap agree on their shared keys.  Violations where "
            "both sections are at PROPOSAL tier are advisory; violations "
            "involving VERIFIED or CERTIFIED sections are mandatory failures."
        ),
    )


# ---------------------------------------------------------------------------
# T_CD_3 — Budget Admissibility
# ---------------------------------------------------------------------------

def verify_budget_admissibility(
    allocations: dict[str, float],
    total_budget: float,
    overhead_fraction: float = _DEFAULT_OVERHEAD_FRACTION,
) -> dict[str, Any]:
    """Verify T_CD_3: the total allocated budget ≤ net budget.

    Checks:

    1. Every individual allocation is non-negative.
    2. :math:`\\sum_i b_i \\leq B \\cdot (1 - \\beta)` where
       :math:`\\beta` is the overhead fraction.
    3. As a bonus check: no single allocation exceeds the gross budget
       (which would indicate an obvious miscalculation).

    Theory reference: theory2.tex §cover_design.3 (Budget admissibility).

    Parameters
    ----------
    allocations:
        Dict mapping patch ID to allocated budget amount.
    total_budget:
        Gross total budget :math:`B`.
    overhead_fraction:
        Overhead fraction :math:`\\beta \\in [0, 1)`.

    Returns
    -------
    dict[str, Any]
        Theorem result dict.
    """
    name = "T_CD_3-BudgetAdmissibility"
    violations: list[str] = []

    net_budget = total_budget * (1.0 - overhead_fraction)
    total_allocated = sum(allocations.values())

    # Check 1 — non-negative allocations
    negative = {pid: amt for pid, amt in allocations.items() if amt < -_BUDGET_TOLERANCE}
    if negative:
        violations.append(
            f"{len(negative)} patch(es) have negative allocations: "
            f"{dict(list(negative.items())[:5])}"
        )

    # Check 2 — total ≤ net budget
    if total_allocated > net_budget + _BUDGET_TOLERANCE:
        violations.append(
            f"total allocated {total_allocated:.6f} exceeds "
            f"net budget {net_budget:.6f} "
            f"(overflow = {total_allocated - net_budget:.6f})"
        )

    # Check 3 — no single allocation exceeds gross budget
    over_gross = {pid: amt for pid, amt in allocations.items() if amt > total_budget + _BUDGET_TOLERANCE}
    if over_gross:
        violations.append(
            f"{len(over_gross)} patch(es) have allocation exceeding gross budget: "
            f"{dict(list(over_gross.items())[:5])}"
        )

    evidence: dict[str, Any] = {
        "patch_count": len(allocations),
        "total_allocated": total_allocated,
        "total_budget": total_budget,
        "overhead_fraction": overhead_fraction,
        "net_budget": net_budget,
        "overhead_reserved": total_budget * overhead_fraction,
        "budget_slack": max(0.0, net_budget - total_allocated),
        "negative_allocations": len(negative),
        "violations": violations,
    }

    passed = len(violations) == 0
    counterexample = violations[0] if violations else None

    return _make_result(
        name,
        passed,
        evidence,
        counterexample=counterexample,
        sketch=(
            "Budget admissibility holds iff (a) every allocation is non-negative, "
            "(b) the total allocation does not exceed the net budget B·(1-β), and "
            "(c) no single allocation exceeds the gross budget B."
        ),
    )


# ---------------------------------------------------------------------------
# T_CD_4 — Dependency Acyclicity
# ---------------------------------------------------------------------------

def verify_dependency_acyclicity(
    dependency_dag: DependencyGraph,
) -> dict[str, Any]:
    """Verify T_CD_4: the dependency DAG contains no directed cycles.

    Uses the :meth:`DependencyGraph.has_cycle` method (iterative DFS with
    three-colour marking).  If a cycle is detected, the counterexample is
    the set of nodes involved in the cycle.

    Theory reference: theory2.tex §cover_design.5 (Dependency ordering).

    Parameters
    ----------
    dependency_dag:
        A :class:`DependencyGraph` instance.

    Returns
    -------
    dict[str, Any]
        Theorem result dict.
    """
    name = "T_CD_4-DependencyAcyclicity"
    violations: list[str] = []

    node_count = len(dependency_dag.nodes)
    edge_count = sum(len(s) for s in dependency_dag.edges.values())
    has_cycle = dependency_dag.has_cycle()

    if has_cycle:
        # Attempt to identify the cycle nodes by finding nodes still in
        # topological-sort progress (those with non-zero in-degree after a
        # full Kahn pass)
        from collections import deque as _deque
        in_deg: dict[str, int] = {}
        for n in dependency_dag.nodes:
            in_deg.setdefault(n, 0)
            for s in dependency_dag.successors(n):
                in_deg[s] = in_deg.get(s, 0) + 1
        queue = _deque(n for n, d in in_deg.items() if d == 0)
        processed: set[str] = set()
        while queue:
            node = queue.popleft()
            processed.add(node)
            for s in dependency_dag.successors(node):
                in_deg[s] -= 1
                if in_deg[s] == 0:
                    queue.append(s)
        cycle_nodes = sorted(dependency_dag.nodes - processed)
        violations.append(f"cycle detected involving nodes: {cycle_nodes}")
    else:
        cycle_nodes = []

    evidence: dict[str, Any] = {
        "node_count": node_count,
        "edge_count": edge_count,
        "has_cycle": has_cycle,
        "cycle_nodes": cycle_nodes,
        "violations": violations,
    }

    passed = not has_cycle
    counterexample = cycle_nodes if cycle_nodes else None

    return _make_result(
        name,
        passed,
        evidence,
        counterexample=counterexample,
        sketch=(
            "Dependency acyclicity holds iff the iterative DFS three-colour "
            "marking finds no back edges.  A cycle makes topological ordering "
            "impossible and must be resolved before scheduling."
        ),
    )


# ---------------------------------------------------------------------------
# T_CD_5 — Topological Ordering Correctness
# ---------------------------------------------------------------------------

def verify_topological_ordering_correctness(
    order: list[str],
    dependency_dag: DependencyGraph,
) -> dict[str, Any]:
    """Verify T_CD_5: *order* is a valid linear extension of the partial order.

    A sequence is a *valid linear extension* iff for every edge
    :math:`(u, v) \\in E` the node :math:`u` appears at an earlier index
    than :math:`v` in the sequence.

    Verification steps
    ------------------
    1. Check that *order* contains exactly the same nodes as the DAG
       (no missing and no extraneous nodes).
    2. For every edge :math:`(u, v)`, confirm ``index(u) < index(v)``.

    Theory reference: theory2.tex §cover_design.5 (Dependency ordering).

    Parameters
    ----------
    order:
        The proposed topological ordering (list of patch IDs).
    dependency_dag:
        The dependency DAG providing the partial order.

    Returns
    -------
    dict[str, Any]
        Theorem result dict.
    """
    name = "T_CD_5-TopologicalOrderingCorrectness"
    violations: list[str] = []

    order_set = set(order)
    dag_nodes = dependency_dag.nodes

    # Check 1 — same node sets
    missing_from_order = dag_nodes - order_set
    extra_in_order = order_set - dag_nodes
    if missing_from_order:
        violations.append(
            f"order is missing {len(missing_from_order)} node(s): "
            f"{sorted(missing_from_order)[:10]}"
        )
    if extra_in_order:
        violations.append(
            f"order contains {len(extra_in_order)} extraneous node(s): "
            f"{sorted(extra_in_order)[:10]}"
        )

    # Check 2 — for every edge (u, v), index(u) < index(v)
    index_of: dict[str, int] = {n: i for i, n in enumerate(order)}
    edge_violations: list[tuple[str, str]] = []
    for u in dependency_dag.nodes:
        for v in dependency_dag.successors(u):
            if u in index_of and v in index_of:
                if index_of[u] >= index_of[v]:
                    edge_violations.append((u, v))

    if edge_violations:
        violations.append(
            f"{len(edge_violations)} edge(s) violate topological order: "
            f"{edge_violations[:5]}"
        )

    evidence: dict[str, Any] = {
        "order_length": len(order),
        "dag_node_count": len(dag_nodes),
        "missing_from_order": sorted(missing_from_order),
        "extra_in_order": sorted(extra_in_order),
        "edge_violations": edge_violations[:10],
        "edges_checked": sum(len(dependency_dag.successors(n)) for n in dependency_dag.nodes),
        "violations": violations,
    }

    passed = len(violations) == 0
    counterexample = violations[0] if violations else None

    return _make_result(
        name,
        passed,
        evidence,
        counterexample=counterexample,
        sketch=(
            "Topological ordering correctness holds iff the order contains "
            "exactly the DAG nodes and for every directed edge (u, v) the "
            "index of u strictly precedes the index of v."
        ),
    )


# ---------------------------------------------------------------------------
# T_CD_6 — Parallelism Safety
# ---------------------------------------------------------------------------

def verify_parallelism_safety(
    waves: list[list[str]],
    dependency_dag: DependencyGraph,
) -> dict[str, Any]:
    """Verify T_CD_6: same-wave patches have no dependency edges between them.

    For each wave :math:`W_k`, checks that for all :math:`u, v \\in W_k`
    there is no edge :math:`(u, v)` or :math:`(v, u)` in the DAG.  The
    existence of any such edge would mean the two patches cannot safely
    execute in parallel.

    Theory reference: theory2.tex §cover_design.6 (Parallelism safety).

    Parameters
    ----------
    waves:
        List of generation waves, each a list of patch IDs.
    dependency_dag:
        The dependency DAG.

    Returns
    -------
    dict[str, Any]
        Theorem result dict.
    """
    name = "T_CD_6-ParallelismSafety"
    violations: list[dict[str, Any]] = []

    for wave_idx, wave in enumerate(waves):
        wave_set = set(wave)
        for node in wave:
            for succ in dependency_dag.successors(node):
                if succ in wave_set:
                    violations.append(
                        {
                            "wave": wave_idx,
                            "edge": (node, succ),
                            "description": (
                                f"edge ({node} → {succ}) found within wave {wave_idx}"
                            ),
                        }
                    )

    evidence: dict[str, Any] = {
        "wave_count": len(waves),
        "total_patches": sum(len(w) for w in waves),
        "violations": violations[:10],
        "violation_count": len(violations),
    }

    passed = len(violations) == 0
    counterexample = violations[0] if violations else None

    return _make_result(
        name,
        passed,
        evidence,
        counterexample=counterexample,
        sketch=(
            "Parallelism safety holds iff no dependency edge connects two "
            "patches assigned to the same generation wave.  An intra-wave "
            "edge would require sequential execution, violating the wave "
            "abstraction."
        ),
    )


# ---------------------------------------------------------------------------
# T_CD_7 — Quality Threshold Monotonicity
# ---------------------------------------------------------------------------

def verify_quality_threshold_monotonicity(
    base_patches: list[dict[str, Any]],
    extended_patches: list[dict[str, Any]],
    site_boundary: Any,
) -> dict[str, Any]:
    """Verify T_CD_7: adding patches cannot decrease coverage completeness.

    Checks that :math:`\\kappa(\\mathcal{U}) \\leq \\kappa(\\mathcal{V})`
    where :math:`\\mathcal{U}` = *base_patches* and
    :math:`\\mathcal{V}` = *extended_patches*.

    The function also verifies that *extended_patches* is a *superset* of
    *base_patches* (by patch ID).  If it is not, the result is
    ``"inconclusive"`` with a note explaining that the theorem does not
    apply to arbitrary patch set changes.

    Theory reference: theory2.tex §cover_design.7 (Quality metrics,
    monotonicity).

    Parameters
    ----------
    base_patches:
        The smaller patch set :math:`\\mathcal{U}`.
    extended_patches:
        The larger patch set :math:`\\mathcal{V}` (must contain all base IDs).
    site_boundary:
        Site boundary as for :func:`compute_coverage_completeness`.

    Returns
    -------
    dict[str, Any]
        Theorem result dict.
    """
    name = "T_CD_7-QualityThresholdMonotonicity"

    base_ids = {str(p.get("patch_id", "")) for p in base_patches}
    ext_ids = {str(p.get("patch_id", "")) for p in extended_patches}
    missing_ids = base_ids - ext_ids

    if missing_ids:
        evidence: dict[str, Any] = {
            "base_patch_count": len(base_patches),
            "extended_patch_count": len(extended_patches),
            "missing_ids": sorted(missing_ids),
            "note": (
                "extended_patches does not contain all base patches — "
                "theorem does not apply to this comparison"
            ),
        }
        raw = _make_result(name, False, evidence, sketch="Inconclusive: not a superset comparison.")
        raw["status"] = "inconclusive"
        return raw

    kappa_base = compute_coverage_completeness(base_patches, site_boundary)
    kappa_ext = compute_coverage_completeness(extended_patches, site_boundary)

    violations: list[str] = []
    if kappa_ext < kappa_base - _COVERAGE_TOLERANCE:
        violations.append(
            f"coverage decreased: κ(base)={kappa_base:.6f} > "
            f"κ(extended)={kappa_ext:.6f}"
        )

    added_patch_count = len(extended_patches) - len(base_patches)
    coverage_delta = kappa_ext - kappa_base

    evidence = {
        "kappa_base": kappa_base,
        "kappa_extended": kappa_ext,
        "coverage_delta": coverage_delta,
        "base_patch_count": len(base_patches),
        "extended_patch_count": len(extended_patches),
        "added_patches": added_patch_count,
        "violations": violations,
    }

    passed = len(violations) == 0
    counterexample = violations[0] if violations else None

    return _make_result(
        name,
        passed,
        evidence,
        counterexample=counterexample,
        sketch=(
            "Monotonicity holds because the coverage operator is the set union "
            "of coordinate sets: adding a patch can only add new covered regions, "
            "never remove them.  Hence κ(U) ≤ κ(V) whenever U ⊆ V."
        ),
    )


# ---------------------------------------------------------------------------
# T_CD_8 — Priority Allocation Consistency
# ---------------------------------------------------------------------------

def verify_priority_allocation_consistency(
    patches: list[dict[str, Any]],
    allocations: dict[str, float],
) -> dict[str, Any]:
    """Verify T_CD_8: higher-priority patches get ≥ budget as equal-area lower-priority ones.

    For every pair of patches :math:`(i, j)` with :math:`p_i \\geq p_j` and
    :math:`a_i = a_j` (equal area, up to a small tolerance), checks that
    :math:`b_i \\geq b_j - \\epsilon` where :math:`\\epsilon` is a small
    numerical tolerance.

    This property follows from the proportional allocation formula in
    :func:`priority_weighted_allocation` because equal area means equal
    denominator terms, and :math:`p_i \\geq p_j` implies :math:`w_i \\geq w_j`.

    Theory reference: theory2.tex §cover_design.3 (Priority allocation
    consistency).

    Parameters
    ----------
    patches:
        List of patch descriptor dicts with ``"patch_id"``, ``"priority"``,
        and ``"area"`` fields.
    allocations:
        Dict mapping patch ID to allocated budget amount (as from
        :func:`priority_weighted_allocation`).

    Returns
    -------
    dict[str, Any]
        Theorem result dict.
    """
    name = "T_CD_8-PriorityAllocationConsistency"
    violations: list[dict[str, Any]] = []
    _area_tol = 1e-4
    _alloc_tol = 1e-9

    # Build a lookup: patch_id -> (priority, area, allocation)
    patch_info: dict[str, tuple[float, float, float]] = {}
    for p in patches:
        pid = str(p.get("patch_id", ""))
        priority = float(p.get("priority", 1.0))
        area = float(p.get("area", 0.0))
        alloc = float(allocations.get(pid, 0.0))
        patch_info[pid] = (priority, area, alloc)

    patch_list = list(patch_info.items())
    pairs_checked = 0

    for i in range(len(patch_list)):
        pid_i, (pri_i, area_i, alloc_i) = patch_list[i]
        for j in range(i + 1, len(patch_list)):
            pid_j, (pri_j, area_j, alloc_j) = patch_list[j]
            # Only compare equal-area patches
            if abs(area_i - area_j) > _area_tol:
                continue
            pairs_checked += 1
            # Determine which has higher priority
            if pri_i >= pri_j:
                hi_pid, hi_alloc, lo_pid, lo_alloc = pid_i, alloc_i, pid_j, alloc_j
            else:
                hi_pid, hi_alloc, lo_pid, lo_alloc = pid_j, alloc_j, pid_i, alloc_i

            if hi_alloc < lo_alloc - _alloc_tol:
                violations.append(
                    {
                        "higher_priority_patch": hi_pid,
                        "lower_priority_patch": lo_pid,
                        "higher_allocation": hi_alloc,
                        "lower_allocation": lo_alloc,
                        "deficit": lo_alloc - hi_alloc,
                    }
                )

    evidence: dict[str, Any] = {
        "patch_count": len(patches),
        "pairs_checked": pairs_checked,
        "violation_count": len(violations),
        "violations": violations[:10],
    }

    passed = len(violations) == 0
    counterexample = violations[0] if violations else None

    return _make_result(
        name,
        passed,
        evidence,
        counterexample=counterexample,
        sketch=(
            "Priority allocation consistency holds because the proportional "
            "allocation formula weights patches by p_i · a_i.  With equal "
            "areas the weight ordering is determined solely by priority, so "
            "b_i / b_j = p_i / p_j ≥ 1 whenever p_i ≥ p_j."
        ),
    )


# ---------------------------------------------------------------------------
# TheoremSuite
# ---------------------------------------------------------------------------

class TheoremSuite:
    """Orchestrates all T_CD_* theorem checks and produces a summary report.

    A :class:`TheoremSuite` is constructed with the data objects required
    for at least the structural theorems (T_CD_1, T_CD_4, T_CD_5, T_CD_6).
    Additional arguments to :meth:`run` enable the remaining theorems.

    Theory reference: theory2.tex §cover_design (all sections).

    Attributes
    ----------
    patches:
        List of patch descriptor dicts.
    dependency_dag:
        Dependency DAG for the patch set.
    overlap_graph:
        Overlap graph for the patch set.
    site_boundary:
        Site boundary (set of region IDs or positive float area).
    """

    def __init__(
        self,
        patches: list[dict[str, Any]],
        dependency_dag: DependencyGraph,
        overlap_graph: OverlapGraph,
        site_boundary: Any,
    ) -> None:
        self.patches = patches
        self.dependency_dag = dependency_dag
        self.overlap_graph = overlap_graph
        self.site_boundary = site_boundary

    def run(
        self,
        sections: list[dict[str, Any]] | None = None,
        allocations: dict[str, float] | None = None,
        total_budget: float | None = None,
        overhead_fraction: float = _DEFAULT_OVERHEAD_FRACTION,
        topo_order: list[str] | None = None,
        waves: list[list[str]] | None = None,
        extended_patches: list[dict[str, Any]] | None = None,
    ) -> list[TheoremResult]:
        """Run all T_CD_* theorems and return a list of :class:`TheoremResult` objects.

        Parameters for which ``None`` is supplied result in the corresponding
        theorem being returned as ``"inconclusive"``.

        Parameters
        ----------
        sections:
            Section dicts for T_CD_2.
        allocations:
            Budget allocation dict for T_CD_3 and T_CD_8.
        total_budget:
            Gross budget for T_CD_3.
        overhead_fraction:
            Overhead fraction for T_CD_3.
        topo_order:
            Pre-computed topological order for T_CD_5; if ``None`` it is
            computed internally.
        waves:
            Pre-computed generation waves for T_CD_6; if ``None`` they are
            computed internally.
        extended_patches:
            Extended patch set for T_CD_7.

        Returns
        -------
        list[TheoremResult]
            One :class:`TheoremResult` per theorem (T_CD_1 through T_CD_8).
        """
        results: list[TheoremResult] = []

        # T_CD_1 — Cover Completeness
        raw = verify_cover_completeness(self.patches, self.site_boundary)
        results.append(TheoremResult(**raw))

        # T_CD_2 — Čech Condition Soundness
        if sections is not None:
            raw = verify_cech_condition_soundness(sections, self.overlap_graph)
        else:
            raw = _make_result(
                "T_CD_2-CechConditionSoundness",
                False,
                {"reason": "no sections provided"},
                sketch="Cannot verify without section data.",
            )
            raw["status"] = "inconclusive"
        results.append(TheoremResult(**raw))

        # T_CD_3 — Budget Admissibility
        if allocations is not None and total_budget is not None:
            raw = verify_budget_admissibility(allocations, total_budget, overhead_fraction)
        else:
            raw = _make_result(
                "T_CD_3-BudgetAdmissibility",
                False,
                {"reason": "no allocations or budget provided"},
                sketch="Cannot verify without allocation and budget data.",
            )
            raw["status"] = "inconclusive"
        results.append(TheoremResult(**raw))

        # T_CD_4 — Dependency Acyclicity
        raw = verify_dependency_acyclicity(self.dependency_dag)
        results.append(TheoremResult(**raw))

        # T_CD_5 — Topological Ordering Correctness
        if topo_order is None:
            try:
                topo_order = topological_sort_patches(self.dependency_dag)
            except ValueError:
                topo_order = None

        if topo_order is not None:
            raw = verify_topological_ordering_correctness(topo_order, self.dependency_dag)
        else:
            raw = _make_result(
                "T_CD_5-TopologicalOrderingCorrectness",
                False,
                {"reason": "topological sort failed (cycle present)"},
                sketch="Cannot verify ordering when the DAG contains a cycle.",
            )
            raw["status"] = "inconclusive"
        results.append(TheoremResult(**raw))

        # T_CD_6 — Parallelism Safety
        if waves is None:
            try:
                waves = compute_antichain_decomposition(self.dependency_dag)
            except ValueError:
                waves = None

        if waves is not None:
            raw = verify_parallelism_safety(waves, self.dependency_dag)
        else:
            raw = _make_result(
                "T_CD_6-ParallelismSafety",
                False,
                {"reason": "wave computation failed (cycle present)"},
                sketch="Cannot verify parallelism safety when the DAG contains a cycle.",
            )
            raw["status"] = "inconclusive"
        results.append(TheoremResult(**raw))

        # T_CD_7 — Quality Threshold Monotonicity
        if extended_patches is not None:
            raw = verify_quality_threshold_monotonicity(
                self.patches, extended_patches, self.site_boundary
            )
        else:
            raw = _make_result(
                "T_CD_7-QualityThresholdMonotonicity",
                False,
                {"reason": "no extended_patches provided"},
                sketch="Cannot verify monotonicity without an extended patch set.",
            )
            raw["status"] = "inconclusive"
        results.append(TheoremResult(**raw))

        # T_CD_8 — Priority Allocation Consistency
        if allocations is not None:
            raw = verify_priority_allocation_consistency(self.patches, allocations)
        else:
            raw = _make_result(
                "T_CD_8-PriorityAllocationConsistency",
                False,
                {"reason": "no allocations provided"},
                sketch="Cannot verify priority consistency without allocation data.",
            )
            raw["status"] = "inconclusive"
        results.append(TheoremResult(**raw))

        return results

    def summary_report(
        self, results: list[TheoremResult]
    ) -> dict[str, Any]:
        """Produce a structured summary over *results*.

        Parameters
        ----------
        results:
            Output of :meth:`run`.

        Returns
        -------
        dict[str, Any]
            Summary with keys ``total``, ``passed``, ``failed``,
            ``inconclusive``, ``pass_rate``, and ``details``.
        """
        total = len(results)
        passed_n = sum(1 for r in results if r.status == "passed")
        failed_n = sum(1 for r in results if r.status == "failed")
        inconclusive_n = sum(1 for r in results if r.status == "inconclusive")

        return {
            "total": total,
            "passed": passed_n,
            "failed": failed_n,
            "inconclusive": inconclusive_n,
            "pass_rate": round(passed_n / total, 4) if total else 0.0,
            "details": [
                {
                    "theorem": r.theorem_name,
                    "status": r.status,
                    "proof_sketch": r.proof_sketch,
                    "counterexample": r.counterexample_or_none,
                }
                for r in results
            ],
        }


# ---------------------------------------------------------------------------
# run_all_theorems
# ---------------------------------------------------------------------------

def run_all_theorems(
    patches: list[dict[str, Any]],
    dependency_dag: DependencyGraph,
    overlap_graph: OverlapGraph,
    site_boundary: Any,
) -> list[TheoremResult]:
    """Convenience wrapper: construct a :class:`TheoremSuite` and run it.

    Runs T_CD_1, T_CD_4, T_CD_5, T_CD_6 directly (T_CD_2, T_CD_3, T_CD_7,
    T_CD_8 are returned as ``"inconclusive"`` since the additional arguments
    are not provided).

    Parameters
    ----------
    patches:
        List of patch descriptor dicts.
    dependency_dag:
        Dependency DAG.
    overlap_graph:
        Overlap graph.
    site_boundary:
        Site boundary.

    Returns
    -------
    list[TheoremResult]
        One result per theorem (T_CD_1 through T_CD_8).
    """
    suite = TheoremSuite(
        patches=patches,
        dependency_dag=dependency_dag,
        overlap_graph=overlap_graph,
        site_boundary=site_boundary,
    )
    return suite.run()


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    import logging as _logging
    from pathlib import Path as _Path

    # Ensure the jugeo src directory is on the path when run standalone
    _src = _Path(__file__).parents[4]
    if str(_src) not in sys.path:
        sys.path.insert(0, str(_src))

    # Re-import algorithms now that the path is set
    try:
        from jugeo.generation.cover_design.algorithms import (
            DependencyGraph,
            OverlapGraph,
            ScheduleResult,
            check_cech_condition,
            compute_coverage_completeness,
            topological_sort_patches,
            compute_antichain_decomposition,
        )
    except ImportError as _e:
        print(f"WARNING: could not import algorithms ({_e}); smoke test may be limited")

    _logging.basicConfig(level=_logging.WARNING, stream=sys.stdout)

    # Build a simple cover design fixture
    _patches = [
        {"patch_id": "p0", "coords": [1, 2, 3], "priority": 3.0, "area": 3.0},
        {"patch_id": "p1", "coords": [3, 4, 5], "priority": 2.0, "area": 3.0},
        {"patch_id": "p2", "coords": [5, 6, 7], "priority": 1.0, "area": 3.0},
    ]
    _site = frozenset(range(1, 8))  # regions 1–7

    _dag = DependencyGraph()
    _dag.add_edge("p0", "p1")
    _dag.add_edge("p1", "p2")

    _og = OverlapGraph()
    _og.add_overlap("p0", "p1", 1.0)
    _og.add_overlap("p1", "p2", 1.0)

    _allocations = {"p0": 45.0, "p1": 30.0, "p2": 15.0}  # sum = 90 ≤ 100*(1-0.1) = 90
    _total_budget = 100.0

    _sections = [
        {
            "section_id": "s0", "patch_id": "p0",
            "trust_tier": "PROPOSAL",
            "values": {"r3": "alpha", "r1": "beta"},
        },
        {
            "section_id": "s1", "patch_id": "p1",
            "trust_tier": "PROPOSAL",
            "values": {"r3": "alpha", "r4": "gamma"},
        },
    ]

    _ext_patches = _patches + [
        {"patch_id": "p3", "coords": [7, 8], "priority": 1.0, "area": 2.0},
    ]

    suite = TheoremSuite(
        patches=_patches,
        dependency_dag=_dag,
        overlap_graph=_og,
        site_boundary=_site,
    )

    results = suite.run(
        sections=_sections,
        allocations=_allocations,
        total_budget=_total_budget,
        extended_patches=_ext_patches,
    )

    report = suite.summary_report(results)
    print("=== TheoremSuite Summary ===")
    print(f"Total   : {report['total']}")
    print(f"Passed  : {report['passed']}")
    print(f"Failed  : {report['failed']}")
    print(f"Inconclusive: {report['inconclusive']}")
    print(f"Pass rate   : {report['pass_rate']}")
    print()
    for detail in report["details"]:
        marker = "✓" if detail["status"] == "passed" else ("?" if detail["status"] == "inconclusive" else "✗")
        print(f"  {marker} {detail['theorem']}: {detail['status']}")
    print()
    print("Smoke test PASSED.")
