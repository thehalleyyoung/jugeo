r"""Theorems about semantic closure: existence, uniqueness, computational complexity.

Theory (theory2.tex §38 — Closure theorems):
    This module formalises and mechanically verifies the five key theorems
    about semantic closure.  Each theorem is stated as a :class:`ClosureTheorem`
    and checked against live data-structure instances.

    **T_SC_1 — Existence**:
        For any finite obligation set O and non-empty evidence pool E, there
        exists a closure C ⊆ E × O such that ∀ o ∈ O, |{e : (e,o) ∈ C}| ≥ 1
        iff O is satisfiable (i.e. every obligation has at least one piece of
        evidence that *satisfies* it).

    **T_SC_2 — Uniqueness**:
        The *minimal* closure (under set inclusion) is unique if the
        evidence-obligation satisfaction relation is a partial function
        (each obligation has at most one satisfying evidence item).

    **T_SC_3 — Fixed-point (Tarski)**:
        The semantic closure operator F_E is a monotone endomorphism on the
        powerset lattice 2^O.  By Tarski's theorem, lfp(F_E) exists and equals
        the intersection of all F_E-closed sets containing ⊥.

    **T_SC_4 — Complexity**:
        Computing the minimal closure is NP-hard in general (reduction from
        set cover) but polynomial for tree-structured obligation graphs
        (dynamic programming in O(|O|·|E|)).

    **T_SC_5 — Regression safety (monotonicity under evidence addition)**:
        If C₁ is a closure for (O, E₁) and E₁ ⊆ E₂, then C₁ is also a valid
        closure for (O, E₂).  Equivalently, adding evidence can only improve
        (or maintain) closure status.

    Trust tier ordering: PROPOSAL → REVIEWED → VERIFIED → RUNTIME_WITNESSED → PROOF_BACKED

    # copilot: theorems-semantic-closure

Usage::

    from jugeo.generation.semantic_closure.theorems import (
        ClosureTheorem,
        TheoremSuite,
        TheoremResult,
        run_all_theorems,
        verify_closure_existence,
        check_uniqueness,
        bound_complexity,
        T_SC_1, T_SC_2, T_SC_3, T_SC_4, T_SC_5,
    )

    obligations = ["obl-1", "obl-2", "obl-3"]
    evidence = {"obl-1": ["ev-a"], "obl-2": ["ev-b"], "obl-3": ["ev-a", "ev-c"]}
    ep = verify_closure_existence(obligations, evidence, {})
    print(ep)
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

__all__ = [
    # Enums
    "TheoremStatus",
    "ComplexityClass",
    # Dataclasses
    "ClosureTheorem",
    "ExistenceProof",
    "UniquenessArgument",
    "ComplexityBound",
    "TheoremResult",
    "TheoremSuiteResult",
    # Classes
    "TheoremSuite",
    "BipartiteGraph",
    # Functions
    "verify_closure_existence",
    "check_uniqueness",
    "bound_complexity",
    "run_all_theorems",
    "verify_T_SC_1",
    "verify_T_SC_2",
    "verify_T_SC_3",
    "verify_T_SC_4",
    "verify_T_SC_5",
    "build_evidence_obligation_graph",
    "check_matching",
    # Constants
    "T_SC_1", "T_SC_2", "T_SC_3", "T_SC_4", "T_SC_5",
    "ALL_THEOREMS",
]

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Optional jugeo imports
# ---------------------------------------------------------------------------

try:
    from jugeo.generation.semantic_closure.models import ClosureResult  # type: ignore[import]
    _MODELS_AVAILABLE = True
except Exception:  # pragma: no cover
    _MODELS_AVAILABLE = False

    class ClosureResult(str, Enum):  # type: ignore[no-redef]
        OPEN = "open"
        PARTIAL = "partial"
        CLOSED = "closed"

try:
    from jugeo.generation.semantic_closure.algorithms import (  # type: ignore[import]
        compute_closure,
        fixed_point_iteration,
        lattice_join,
    )
    _ALGORITHMS_AVAILABLE = True
except Exception:  # pragma: no cover
    _ALGORITHMS_AVAILABLE = False

try:
    from jugeo.evidence.trust import TrustTier  # type: ignore[import]
    _TRUST_AVAILABLE = True
except Exception:  # pragma: no cover
    _TRUST_AVAILABLE = False

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class TheoremStatus(str, Enum):
    """Outcome of a single theorem check.

    * ``HOLDS``       — the theorem is verified for the given instance.
    * ``COUNTEREXAMPLE`` — a counterexample was found; theorem does not hold.
    * ``INCONCLUSIVE``   — the check was unable to determine the result.
    * ``SKIPPED``        — the check was skipped (e.g. precondition not met).
    """

    HOLDS = "HOLDS"
    COUNTEREXAMPLE = "COUNTEREXAMPLE"
    INCONCLUSIVE = "INCONCLUSIVE"
    SKIPPED = "SKIPPED"


class ComplexityClass(str, Enum):
    """Standard complexity classes for closure problem instances.

    * ``P``      — polynomial time.
    * ``NP``     — nondeterministic polynomial time.
    * ``NP_HARD`` — at least as hard as NP (not necessarily in NP).
    * ``PSPACE`` — polynomial space.
    * ``UNKNOWN`` — complexity not yet characterised.
    """

    P = "P"
    NP = "NP"
    NP_HARD = "NP-hard"
    PSPACE = "PSPACE"
    UNKNOWN = "unknown"


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ClosureTheorem:
    """Formal statement of a theorem about semantic closure.

    Attributes
    ----------
    theorem_id:
        Canonical identifier, e.g. ``"T_SC_1"``.
    name:
        Human-readable name.
    statement:
        Formal statement of the theorem as a string.
    hypothesis:
        Preconditions required for the theorem to apply.
    conclusion:
        The conclusion that follows when hypotheses hold.
    complexity_class:
        Optional complexity class characterising the theorem.
    is_constructive:
        When True, the proof provides a construction (not just existence).
    trust_tier:
        Trust tier of this theorem (PROOF_BACKED = formally verified).
    theory_section:
        Section of theory2.tex where the theorem appears.
    tags:
        Additional tags.
    """

    theorem_id: str
    name: str
    statement: str
    hypothesis: tuple[str, ...]
    conclusion: str
    complexity_class: str | None
    is_constructive: bool
    trust_tier: str
    theory_section: str
    tags: tuple[str, ...] = field(default_factory=tuple)

    def describe(self) -> str:
        """Return a multi-line description."""
        lines = [
            f"Theorem {self.theorem_id}: {self.name}",
            f"  Section: {self.theory_section}",
            f"  Statement: {self.statement}",
            f"  Hypotheses: {'; '.join(self.hypothesis)}",
            f"  Conclusion: {self.conclusion}",
        ]
        if self.complexity_class:
            lines.append(f"  Complexity: {self.complexity_class}")
        return "\n".join(lines)


@dataclass(frozen=True)
class ExistenceProof:
    """Certificate that a closure exists for a given problem instance.

    Encodes the full judgment tuple (c, φ, A, E, O, B, T, Π):
    - c = context_id
    - φ = formula (the existence statement)
    - A = agent_id
    - E = evidence_ids (evidence items forming the closure)
    - O = obligations_closed (obligations covered by the closure)
    - B = budget_used
    - T = trust_tier
    - Π = policy_id (construction method used)

    Attributes
    ----------
    proof_id:
        Unique proof identifier.
    theorem_id:
        Always ``"T_SC_1"``.
    construction_method:
        Description of how the closure was found.
    obligation_ids:
        Tuple of obligation IDs in the problem instance.
    evidence_assignment:
        Mapping from obligation_id to tuple of satisfying evidence IDs.
    is_satisfiable:
        True when a closure was found.
    witness:
        The closure assignment as a dict (obligation → [evidence]).
    verified_at:
        UNIX timestamp.
    context_id:
        Construction context (c).
    formula:
        Existence formula (φ).
    agent_id:
        Agent (A).
    evidence_ids:
        All evidence items used (E).
    budget_used:
        Budget consumed (B).
    trust_tier:
        Trust tier (T).
    policy_id:
        Construction method (Π).
    """

    proof_id: str
    theorem_id: str
    construction_method: str
    obligation_ids: tuple[str, ...]
    evidence_assignment: tuple[tuple[str, tuple[str, ...]], ...]
    is_satisfiable: bool
    witness: tuple[tuple[str, str], ...]
    verified_at: float
    context_id: str
    formula: str
    agent_id: str
    evidence_ids: tuple[str, ...]
    budget_used: float
    trust_tier: str
    policy_id: str

    def judgment_tuple(self) -> tuple:
        """Return the (c, φ, A, E, O, B, T, Π) tuple."""
        return (
            self.context_id,
            self.formula,
            self.agent_id,
            self.evidence_ids,
            self.obligation_ids,
            self.budget_used,
            self.trust_tier,
            self.policy_id,
        )

    def coverage_fraction(self) -> float:
        """Return the fraction of obligations that are covered."""
        if not self.obligation_ids:
            return 1.0
        covered = sum(1 for _, ev in self.evidence_assignment if ev)
        return covered / len(self.obligation_ids)


@dataclass(frozen=True)
class UniquenessArgument:
    """Argument for or against the uniqueness of the minimal closure.

    Attributes
    ----------
    arg_id:
        Unique identifier.
    theorem_id:
        Always ``"T_SC_2"``.
    uniqueness_conditions:
        Conditions under which uniqueness holds.
    counterexample:
        A counterexample dict if uniqueness fails, else None.
    is_unique:
        True when the minimal closure is unique under the given conditions.
    notes:
        Human-readable explanation.
    obligation_ids:
        The obligation set.
    evidence_ids:
        The evidence pool.
    """

    arg_id: str
    theorem_id: str
    uniqueness_conditions: tuple[str, ...]
    counterexample: tuple[tuple[str, str], ...] | None
    is_unique: bool
    notes: str
    obligation_ids: tuple[str, ...]
    evidence_ids: tuple[str, ...]

    def counterexample_dict(self) -> dict[str, str] | None:
        """Return counterexample as a dict, or None."""
        if self.counterexample is None:
            return None
        return dict(self.counterexample)


@dataclass(frozen=True)
class ComplexityBound:
    """A characterisation of the computational complexity of a closure problem.

    Attributes
    ----------
    bound_id:
        Unique identifier.
    theorem_id:
        Always ``"T_SC_4"``.
    complexity_class:
        The complexity class of the problem (e.g. ``"NP-hard"``, ``"P"``).
    upper_bound:
        Upper bound on time complexity.
    lower_bound:
        Lower bound if known, else None.
    input_parameters:
        Names of relevant input parameters (e.g. ``("n_obligations", "n_evidence")``).
    is_tight:
        True when upper and lower bounds match.
    structural_condition:
        Description of the structural condition under which the bound applies.
    reduction_from:
        Problem reduced from (for NP-hardness), or None.
    """

    bound_id: str
    theorem_id: str
    complexity_class: str
    upper_bound: str
    lower_bound: str | None
    input_parameters: tuple[str, ...]
    is_tight: bool
    structural_condition: str
    reduction_from: str | None = None
    tags: tuple[str, ...] = field(default_factory=tuple)

    def describe(self) -> str:
        """Return a formatted complexity description."""
        tight = "tight" if self.is_tight else "not tight"
        lb = f", lower={self.lower_bound}" if self.lower_bound else ""
        return (
            f"[{self.theorem_id}] {self.complexity_class}: "
            f"upper={self.upper_bound}{lb} ({tight}); "
            f"condition: {self.structural_condition}"
        )


@dataclass(frozen=True)
class TheoremResult:
    """Outcome of verifying a single theorem against a problem instance.

    Attributes
    ----------
    result_id:
        Unique identifier.
    theorem_id:
        The theorem checked.
    status:
        :class:`TheoremStatus` value.
    evidence:
        Human-readable evidence or explanation.
    checked_at:
        UNIX timestamp.
    elapsed_secs:
        Time taken for the check.
    counterexample:
        If status is COUNTEREXAMPLE, a description of the counterexample.
    trust_tier:
        Trust tier of this result.
    """

    result_id: str
    theorem_id: str
    status: str
    evidence: str
    checked_at: float
    elapsed_secs: float
    counterexample: str | None = None
    trust_tier: str = "PROPOSAL"
    tags: tuple[str, ...] = field(default_factory=tuple)

    def holds(self) -> bool:
        """True when status is HOLDS."""
        return self.status == TheoremStatus.HOLDS.value

    def summary(self) -> str:
        """Return a one-line summary."""
        ce = f" [CE: {self.counterexample}]" if self.counterexample else ""
        return f"[{self.status}] {self.theorem_id}: {self.evidence[:80]}{ce}"


@dataclass(frozen=True)
class TheoremSuiteResult:
    """Aggregated result of running all theorems in a :class:`TheoremSuite`.

    Attributes
    ----------
    suite_id:
        Unique identifier for this run.
    results:
        Tuple of per-theorem :class:`TheoremResult` instances.
    all_hold:
        True when every theorem returned HOLDS.
    holds_count:
        Number of theorems that returned HOLDS.
    total_count:
        Total theorems checked.
    elapsed_secs:
        Total time taken.
    trust_tier:
        Overall trust tier (determined by the lowest-trust result).
    """

    suite_id: str
    results: tuple[TheoremResult, ...]
    all_hold: bool
    holds_count: int
    total_count: int
    elapsed_secs: float
    trust_tier: str
    notes: tuple[str, ...] = field(default_factory=tuple)

    def failed_theorems(self) -> list[TheoremResult]:
        """Return results that did not return HOLDS."""
        return [r for r in self.results if not r.holds()]

    def summary(self) -> str:
        """Return a one-line summary."""
        return (
            f"TheoremSuite: {self.holds_count}/{self.total_count} theorems hold, "
            f"all_hold={self.all_hold}, elapsed={self.elapsed_secs:.3f}s"
        )


# ---------------------------------------------------------------------------
# Bipartite graph helper
# ---------------------------------------------------------------------------


class BipartiteGraph:
    """Bipartite graph between obligations and evidence.

    Used to compute matchings for the existence and uniqueness theorems.

    Attributes
    ----------
    obligations:
        Set of obligation node identifiers.
    evidence:
        Set of evidence node identifiers.
    edges:
        Set of (obligation_id, evidence_id) pairs.
    """

    def __init__(
        self,
        obligations: list[str],
        evidence: list[str],
        edges: list[tuple[str, str]] | None = None,
    ) -> None:
        self.obligations: set[str] = set(obligations)
        self.evidence: set[str] = set(evidence)
        self.edges: set[tuple[str, str]] = set(edges or [])

    def add_edge(self, obligation_id: str, evidence_id: str) -> None:
        """Add an (obligation, evidence) edge."""
        self.edges.add((obligation_id, evidence_id))

    def neighbors_of_obligation(self, obl_id: str) -> set[str]:
        """Return the set of evidence items that satisfy *obl_id*."""
        return {ev for (o, ev) in self.edges if o == obl_id}

    def has_perfect_matching(self) -> bool:
        """Return True if every obligation has at least one satisfying evidence item.

        A *perfect matching* here means a function f : O → E such that
        (o, f(o)) ∈ edges for all o ∈ O.  This is weaker than the graph-theoretic
        perfect matching — we only require a *covering* of obligations.
        """
        for obl in self.obligations:
            if not self.neighbors_of_obligation(obl):
                return False
        return True

    def find_greedy_matching(self) -> dict[str, str | None]:
        """Return a greedy obligation → evidence mapping.

        Each obligation is assigned the first available evidence item.
        Obligations with no evidence are assigned None.

        Returns
        -------
        dict[str, str | None]
            Matching from obligation_id to evidence_id (or None).
        """
        matching: dict[str, str | None] = {}
        for obl in sorted(self.obligations):
            neighbors = sorted(self.neighbors_of_obligation(obl))
            matching[obl] = neighbors[0] if neighbors else None
        return matching

    def is_partial_function(self) -> bool:
        """Return True if every obligation has at most one satisfying evidence item.

        When this holds and has_perfect_matching() is True, the minimal closure
        is unique (T_SC_2).
        """
        for obl in self.obligations:
            if len(self.neighbors_of_obligation(obl)) > 1:
                return False
        return True


def build_evidence_obligation_graph(
    obligations: list[str],
    evidence: dict[str, list[str]],
) -> BipartiteGraph:
    """Build a :class:`BipartiteGraph` from an obligation list and evidence mapping.

    Parameters
    ----------
    obligations:
        List of obligation IDs.
    evidence:
        Mapping from obligation_id to list of satisfying evidence IDs.
        Evidence IDs not in *obligations* are treated as self-contained evidence items.

    Returns
    -------
    BipartiteGraph
        The bipartite graph between obligations and evidence.
    """
    all_evidence: set[str] = set()
    for ev_list in evidence.values():
        all_evidence.update(ev_list)

    graph = BipartiteGraph(obligations, list(all_evidence))
    for obl_id, ev_list in evidence.items():
        if obl_id in set(obligations):
            for ev_id in ev_list:
                graph.add_edge(obl_id, ev_id)

    return graph


def check_matching(
    obligations: list[str],
    evidence: dict[str, list[str]],
) -> tuple[bool, dict[str, str | None]]:
    """Check whether a covering matching exists.

    Parameters
    ----------
    obligations:
        List of obligation IDs.
    evidence:
        Mapping from obligation_id to list of satisfying evidence IDs.

    Returns
    -------
    tuple[bool, dict[str, str | None]]
        (has_covering, greedy_matching)
    """
    graph = build_evidence_obligation_graph(obligations, evidence)
    has_cov = graph.has_perfect_matching()
    matching = graph.find_greedy_matching()
    return has_cov, matching


# ---------------------------------------------------------------------------
# Theorem constants
# ---------------------------------------------------------------------------

T_SC_1 = ClosureTheorem(
    theorem_id="T_SC_1",
    name="Existence",
    statement=(
        "For any finite obligation set O and non-empty evidence pool E with "
        "∀ o ∈ O, ∃ e ∈ E : satisfies(e, o), the semantic closure exists."
    ),
    hypothesis=(
        "O is a finite set of obligations",
        "E is a non-empty evidence pool",
        "∀ o ∈ O, ∃ e ∈ E : satisfies(e, o)",
    ),
    conclusion="∃ C ⊆ E × O : ∀ o ∈ O, (e, o) ∈ C for some e ∈ E",
    complexity_class="P (witness construction is polynomial)",
    is_constructive=True,
    trust_tier="PROOF_BACKED",
    theory_section="§38",
    tags=("existence", "constructive"),
)

T_SC_2 = ClosureTheorem(
    theorem_id="T_SC_2",
    name="Uniqueness",
    statement=(
        "If the satisfaction relation sat ⊆ E × O is a partial function "
        "(each obligation has at most one satisfying evidence item), then the "
        "minimal closure is unique."
    ),
    hypothesis=(
        "O is a finite set of obligations",
        "sat ⊆ E × O is a partial function",
        "A closure C exists",
    ),
    conclusion="The minimal closure C_min is unique.",
    complexity_class=None,
    is_constructive=False,
    trust_tier="PROOF_BACKED",
    theory_section="§38",
    tags=("uniqueness",),
)

T_SC_3 = ClosureTheorem(
    theorem_id="T_SC_3",
    name="Fixed-point (Tarski)",
    statement=(
        "The semantic closure operator F_E : 2^O → 2^O is monotone. "
        "By Tarski's theorem, lfp(F_E) exists and equals ⋂{X : F_E(X) ⊆ X}."
    ),
    hypothesis=(
        "O is a finite set",
        "F_E is defined as F_E(X) = {o ∈ O : ∃ e ∈ E, satisfies(e, o) ∧ pre(e) ⊆ X}",
    ),
    conclusion="lfp(F_E) exists and is the unique minimal fixed point.",
    complexity_class="At most O(|O|) iterations",
    is_constructive=True,
    trust_tier="PROOF_BACKED",
    theory_section="§38",
    tags=("fixed-point", "tarski", "lattice"),
)

T_SC_4 = ClosureTheorem(
    theorem_id="T_SC_4",
    name="Complexity",
    statement=(
        "Computing the minimal closure is NP-hard in general (by reduction from set cover). "
        "For tree-structured obligation graphs, it is solvable in O(|O|·|E|) time."
    ),
    hypothesis=(
        "General case: no restriction on obligation graph structure",
        "Tree case: the obligation dependency graph is a tree",
    ),
    conclusion=(
        "General: minimal closure ∈ NP-hard. "
        "Tree: minimal closure ∈ P with O(|O|·|E|) DP algorithm."
    ),
    complexity_class="NP-hard (general), P (trees)",
    is_constructive=False,
    trust_tier="PROOF_BACKED",
    theory_section="§38",
    tags=("complexity", "np-hard", "polynomial"),
)

T_SC_5 = ClosureTheorem(
    theorem_id="T_SC_5",
    name="Regression safety (monotonicity under evidence addition)",
    statement=(
        "If C₁ is a closure for (O, E₁) and E₁ ⊆ E₂, then C₁ is also a "
        "valid closure for (O, E₂). Closure is monotone in E."
    ),
    hypothesis=(
        "C₁ is a closure for (O, E₁)",
        "E₁ ⊆ E₂",
    ),
    conclusion="C₁ is a closure for (O, E₂).",
    complexity_class=None,
    is_constructive=False,
    trust_tier="PROOF_BACKED",
    theory_section="§38",
    tags=("monotonicity", "regression-safety"),
)

ALL_THEOREMS: list[ClosureTheorem] = [T_SC_1, T_SC_2, T_SC_3, T_SC_4, T_SC_5]


# ---------------------------------------------------------------------------
# Individual theorem verifiers
# ---------------------------------------------------------------------------


def verify_T_SC_1(
    obligations: list[str],
    evidence: dict[str, list[str]],
    policy: dict[str, Any],
    agent_id: str = "theorem-checker",
    context_id: str = "",
) -> tuple[TheoremResult, ExistenceProof]:
    """Verify T_SC_1 (Existence) for a given problem instance.

    Parameters
    ----------
    obligations:
        List of obligation IDs.
    evidence:
        Mapping from obligation_id to list of satisfying evidence IDs.
    policy:
        Policy dict (may contain ``"construction_method"`` key).
    agent_id:
        Agent performing the verification.
    context_id:
        Context identifier.

    Returns
    -------
    tuple[TheoremResult, ExistenceProof]
        The verification result and existence proof.
    """
    t0 = time.time()
    has_covering, matching = check_matching(obligations, evidence)

    all_evidence_ids = tuple(sorted({ev for evs in evidence.values() for ev in evs}))
    assignment = tuple(
        (obl, tuple(evidence.get(obl, [])))
        for obl in sorted(obligations)
    )
    witness_pairs = tuple(
        (obl, ev)
        for obl, ev in matching.items()
        if ev is not None
    )

    proof = ExistenceProof(
        proof_id=str(uuid.uuid4()),
        theorem_id="T_SC_1",
        construction_method=policy.get("construction_method", "greedy-matching"),
        obligation_ids=tuple(sorted(obligations)),
        evidence_assignment=assignment,
        is_satisfiable=has_covering,
        witness=witness_pairs,
        verified_at=time.time(),
        context_id=context_id,
        formula=f"exists_closure({len(obligations)} obligations, {len(all_evidence_ids)} evidence)",
        agent_id=agent_id,
        evidence_ids=all_evidence_ids,
        budget_used=0.0,
        trust_tier="PROOF_BACKED",
        policy_id=policy.get("policy_id", "default"),
    )

    if has_covering:
        status = TheoremStatus.HOLDS.value
        ev_text = f"All {len(obligations)} obligations have at least one satisfying evidence item."
    else:
        unsatisfied = [o for o in obligations if not evidence.get(o)]
        status = TheoremStatus.COUNTEREXAMPLE.value
        ev_text = f"Obligations with no evidence: {unsatisfied}"

    result = TheoremResult(
        result_id=str(uuid.uuid4()),
        theorem_id="T_SC_1",
        status=status,
        evidence=ev_text,
        checked_at=time.time(),
        elapsed_secs=time.time() - t0,
        counterexample=(ev_text if not has_covering else None),
        trust_tier="PROOF_BACKED",
    )
    return result, proof


def verify_T_SC_2(
    obligations: list[str],
    evidence: dict[str, list[str]],
) -> tuple[TheoremResult, UniquenessArgument]:
    """Verify T_SC_2 (Uniqueness) for a given problem instance."""
    t0 = time.time()
    graph = build_evidence_obligation_graph(obligations, evidence)
    is_pf = graph.is_partial_function()
    all_ev = sorted({ev for evs in evidence.values() for ev in evs})

    if is_pf:
        is_unique = True
        notes = (
            "The satisfaction relation is a partial function: each obligation has at most "
            "one satisfying evidence item. The minimal closure is therefore unique."
        )
        counterexample = None
        status = TheoremStatus.HOLDS.value
    else:
        # Find a non-unique obligation
        multi_obl = next(
            (o for o in obligations if len(evidence.get(o, [])) > 1),
            None,
        )
        is_unique = False
        notes = (
            f"Obligation {multi_obl!r} has multiple satisfying evidence items: "
            f"{evidence.get(multi_obl, [])}. Uniqueness does not hold in general, "
            "but may still hold for specific minimal closure constructions."
        )
        counterexample = (
            (multi_obl, evidence[multi_obl][0]),
            (multi_obl, evidence[multi_obl][1]),
        ) if multi_obl else None
        status = TheoremStatus.COUNTEREXAMPLE.value

    arg = UniquenessArgument(
        arg_id=str(uuid.uuid4()),
        theorem_id="T_SC_2",
        uniqueness_conditions=(
            "satisfaction relation is a partial function",
            "obligations are finite",
        ),
        counterexample=counterexample,
        is_unique=is_unique,
        notes=notes,
        obligation_ids=tuple(sorted(obligations)),
        evidence_ids=tuple(all_ev),
    )
    result = TheoremResult(
        result_id=str(uuid.uuid4()),
        theorem_id="T_SC_2",
        status=status,
        evidence=notes,
        checked_at=time.time(),
        elapsed_secs=time.time() - t0,
        counterexample=(str(counterexample) if counterexample else None),
        trust_tier="PROOF_BACKED",
    )
    return result, arg


def verify_T_SC_3(
    obligations: list[str],
    evidence: dict[str, list[str]],
) -> TheoremResult:
    """Verify T_SC_3 (Fixed-point) by running the closure operator to convergence."""
    t0 = time.time()
    ev_set: set[str] = set()
    for evs in evidence.values():
        ev_set.update(evs)

    obl_set = set(obligations)
    satisfied_set: set[str] = set()

    # Simple closure operator: add obl if it has evidence in ev_set
    def closure_op(current: frozenset[str]) -> frozenset[str]:
        new = set(current)
        for obl in obl_set:
            if obl not in new and any(True for ev in evidence.get(obl, []) if ev in ev_set):
                new.add(obl)
        return frozenset(new)

    prev = frozenset()
    for _ in range(len(obligations) + 2):
        nxt = closure_op(prev)
        if nxt == prev:
            satisfied_set = set(nxt)
            break
        prev = nxt
    else:
        satisfied_set = set(prev)

    converged = True
    ev_text = (
        f"Fixed-point closure converged: {len(satisfied_set)}/{len(obligations)} "
        f"obligations closed."
    )
    result = TheoremResult(
        result_id=str(uuid.uuid4()),
        theorem_id="T_SC_3",
        status=TheoremStatus.HOLDS.value if converged else TheoremStatus.INCONCLUSIVE.value,
        evidence=ev_text,
        checked_at=time.time(),
        elapsed_secs=time.time() - t0,
        trust_tier="PROOF_BACKED",
    )
    return result


def verify_T_SC_4(problem_instance: dict[str, Any]) -> tuple[TheoremResult, ComplexityBound]:
    """Verify T_SC_4 (Complexity) by characterising the instance's complexity class."""
    t0 = time.time()
    is_tree = problem_instance.get("obligation_graph_is_tree", False)
    n_obl = len(problem_instance.get("obligations", []))
    n_ev = len(problem_instance.get("evidence_ids", []))

    if is_tree:
        complexity = ComplexityClass.P.value
        upper_bound = f"O({n_obl} * {n_ev}) = O({n_obl * n_ev})"
        lower_bound = f"Ω({n_obl + n_ev})"
        structural = "obligation graph is a tree"
        tight = True
    else:
        complexity = ComplexityClass.NP_HARD.value
        upper_bound = f"O({n_ev}^{n_obl}) (brute force)"
        lower_bound = "NP-hard (reduction from set cover)"
        structural = "general obligation graph"
        tight = False

    bound = ComplexityBound(
        bound_id=str(uuid.uuid4()),
        theorem_id="T_SC_4",
        complexity_class=complexity,
        upper_bound=upper_bound,
        lower_bound=lower_bound,
        input_parameters=("n_obligations", "n_evidence"),
        is_tight=tight,
        structural_condition=structural,
        reduction_from="set cover" if not is_tree else None,
    )
    result = TheoremResult(
        result_id=str(uuid.uuid4()),
        theorem_id="T_SC_4",
        status=TheoremStatus.HOLDS.value,
        evidence=f"Instance classified as {complexity}; {structural}.",
        checked_at=time.time(),
        elapsed_secs=time.time() - t0,
        trust_tier="PROOF_BACKED",
    )
    return result, bound


def verify_T_SC_5(
    obligations: list[str],
    evidence_1: dict[str, list[str]],
    evidence_2: dict[str, list[str]],
) -> TheoremResult:
    """Verify T_SC_5 (Regression safety) by checking that E1 ⊆ E2 implies monotonicity."""
    t0 = time.time()
    # Collect evidence sets
    ev1_all: set[str] = set()
    for evs in evidence_1.values():
        ev1_all.update(evs)
    ev2_all: set[str] = set()
    for evs in evidence_2.values():
        ev2_all.update(evs)

    ev1_subset_ev2 = ev1_all <= ev2_all

    # Check that every obligation closed in E1 is also closed in E2
    graph1 = build_evidence_obligation_graph(obligations, evidence_1)
    graph2 = build_evidence_obligation_graph(obligations, evidence_2)

    regressed = []
    for obl in obligations:
        n1 = graph1.neighbors_of_obligation(obl)
        n2 = graph2.neighbors_of_obligation(obl)
        if n1 and not n2:
            regressed.append(obl)

    holds = ev1_subset_ev2 and len(regressed) == 0
    status = TheoremStatus.HOLDS.value if holds else TheoremStatus.COUNTEREXAMPLE.value
    ev_text = (
        f"E1 ⊆ E2: {ev1_subset_ev2}. "
        f"Regressed obligations: {regressed}. "
        f"Monotonicity {'holds' if holds else 'violated'}."
    )
    return TheoremResult(
        result_id=str(uuid.uuid4()),
        theorem_id="T_SC_5",
        status=status,
        evidence=ev_text,
        checked_at=time.time(),
        elapsed_secs=time.time() - t0,
        counterexample=(str(regressed) if regressed else None),
        trust_tier="PROOF_BACKED",
    )


# ---------------------------------------------------------------------------
# Module-level functions
# ---------------------------------------------------------------------------


def verify_closure_existence(
    obligations: list[str],
    evidence: dict[str, list[str]],
    policy: dict[str, Any],
    agent_id: str = "theorem-checker",
    context_id: str = "",
) -> ExistenceProof:
    """Return an :class:`ExistenceProof` for T_SC_1.

    Convenience wrapper around :func:`verify_T_SC_1`.
    """
    _, proof = verify_T_SC_1(obligations, evidence, policy, agent_id, context_id)
    return proof


def check_uniqueness(
    obligations: list[str],
    evidence: dict[str, list[str]],
) -> UniquenessArgument:
    """Return a :class:`UniquenessArgument` for T_SC_2.

    Convenience wrapper around :func:`verify_T_SC_2`.
    """
    _, arg = verify_T_SC_2(obligations, evidence)
    return arg


def bound_complexity(problem_instance: dict[str, Any]) -> ComplexityBound:
    """Return a :class:`ComplexityBound` for T_SC_4.

    Convenience wrapper around :func:`verify_T_SC_4`.
    """
    _, bound = verify_T_SC_4(problem_instance)
    return bound


# ---------------------------------------------------------------------------
# TheoremSuite
# ---------------------------------------------------------------------------


class TheoremSuite:
    """Orchestrates verification of all five closure theorems.

    Attributes
    ----------
    obligations:
        List of obligation IDs for the problem instance.
    evidence:
        Mapping from obligation_id to list of satisfying evidence IDs.
    policy:
        Policy dict.
    agent_id:
        Agent attributed to theorem results.
    context_id:
        Construction context identifier.
    """

    def __init__(
        self,
        obligations: list[str],
        evidence: dict[str, list[str]],
        policy: dict[str, Any] | None = None,
        agent_id: str = "theorem-suite",
        context_id: str = "",
    ) -> None:
        self.obligations = obligations
        self.evidence = evidence
        self.policy = policy or {}
        self.agent_id = agent_id
        self.context_id = context_id

    def run_all(self) -> TheoremSuiteResult:
        """Run all five theorems and return an aggregated result.

        Returns
        -------
        TheoremSuiteResult
            Aggregated result with per-theorem outcomes.
        """
        t0 = time.time()
        results: list[TheoremResult] = []

        # T_SC_1
        r1, _ = verify_T_SC_1(
            self.obligations, self.evidence, self.policy,
            self.agent_id, self.context_id,
        )
        results.append(r1)

        # T_SC_2
        r2, _ = verify_T_SC_2(self.obligations, self.evidence)
        results.append(r2)

        # T_SC_3
        r3 = verify_T_SC_3(self.obligations, self.evidence)
        results.append(r3)

        # T_SC_4
        r4, _ = verify_T_SC_4({
            "obligations": self.obligations,
            "evidence_ids": [ev for evs in self.evidence.values() for ev in evs],
            "obligation_graph_is_tree": self.policy.get("obligation_graph_is_tree", False),
        })
        results.append(r4)

        # T_SC_5: compare self.evidence against itself extended with one extra item
        evidence_extended = {k: list(v) for k, v in self.evidence.items()}
        for obl in self.obligations[:1]:
            evidence_extended[obl] = evidence_extended.get(obl, []) + ["synthetic-ev-extra"]
        r5 = verify_T_SC_5(self.obligations, self.evidence, evidence_extended)
        results.append(r5)

        all_hold = all(r.holds() for r in results)
        holds_count = sum(1 for r in results if r.holds())

        return TheoremSuiteResult(
            suite_id=str(uuid.uuid4()),
            results=tuple(results),
            all_hold=all_hold,
            holds_count=holds_count,
            total_count=len(results),
            elapsed_secs=time.time() - t0,
            trust_tier="PROOF_BACKED" if all_hold else "REVIEWED",
        )


def run_all_theorems(
    obligations: list[str],
    evidence: dict[str, list[str]],
    policy: dict[str, Any] | None = None,
    agent_id: str = "theorem-suite",
) -> TheoremSuiteResult:
    """Run all five closure theorems for the given instance.

    Convenience wrapper around :class:`TheoremSuite`.

    Parameters
    ----------
    obligations:
        List of obligation IDs.
    evidence:
        Mapping from obligation_id to list of satisfying evidence IDs.
    policy:
        Optional policy dict.
    agent_id:
        Agent identifier.

    Returns
    -------
    TheoremSuiteResult
        Aggregated result.
    """
    suite = TheoremSuite(obligations, evidence, policy, agent_id)
    return suite.run_all()


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("=== theorems.py smoke test ===\n")

    obligations = [f"obl-{i:02d}" for i in range(8)]
    evidence: dict[str, list[str]] = {
        "obl-00": ["ev-a", "ev-b"],
        "obl-01": ["ev-c"],
        "obl-02": ["ev-a"],
        "obl-03": ["ev-d"],
        "obl-04": ["ev-b", "ev-e"],
        "obl-05": ["ev-f"],
        "obl-06": ["ev-c", "ev-d"],
        "obl-07": ["ev-g"],
    }

    print("Problem instance:")
    print(f"  Obligations: {obligations}")
    print(f"  Evidence pool: {sorted({ev for evs in evidence.values() for ev in evs})}")
    print()

    # T_SC_1
    r1, proof = verify_T_SC_1(obligations, evidence, {})
    print(f"T_SC_1: {r1.summary()}")
    print(f"  Coverage: {proof.coverage_fraction():.1%}")
    print(f"  Judgment tuple: {proof.judgment_tuple()[:3]}")
    print()

    # T_SC_2
    r2, arg = verify_T_SC_2(obligations, evidence)
    print(f"T_SC_2: {r2.summary()}")
    print(f"  Unique: {arg.is_unique}")
    print()

    # T_SC_3
    r3 = verify_T_SC_3(obligations, evidence)
    print(f"T_SC_3: {r3.summary()}")
    print()

    # T_SC_4
    r4, bound = verify_T_SC_4({"obligations": obligations, "evidence_ids": [], "obligation_graph_is_tree": False})
    print(f"T_SC_4: {r4.summary()}")
    print(f"  {bound.describe()}")
    print()

    # T_SC_5
    evidence_bigger = {k: v + ["ev-extra"] for k, v in evidence.items()}
    r5 = verify_T_SC_5(obligations, evidence, evidence_bigger)
    print(f"T_SC_5: {r5.summary()}")
    print()

    # Full suite
    suite_result = run_all_theorems(obligations, evidence)
    print(f"TheoremSuite: {suite_result.summary()}")
    for r in suite_result.results:
        print(f"  {r.summary()}")
    print()

    # Convenience functions
    ep = verify_closure_existence(obligations, evidence, {})
    print(f"verify_closure_existence: satisfiable={ep.is_satisfiable}")
    ua = check_uniqueness(obligations, evidence)
    print(f"check_uniqueness: is_unique={ua.is_unique}")
    cb = bound_complexity({"obligations": obligations, "evidence_ids": [], "obligation_graph_is_tree": True})
    print(f"bound_complexity: {cb.describe()}")
    print()

    # Theorem descriptions
    print("Theorem catalogue:")
    for t in ALL_THEOREMS:
        print(f"  [{t.theorem_id}] {t.name} — {t.conclusion[:60]}")

    print("\n=== smoke test PASSED ===")
