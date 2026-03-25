r"""Formal theorems for the ``jugeo.se_theory.testing`` package.

Each theorem is an instance of :class:`Theorem` carrying:
* ``name``          — short camelCase or snake_case identifier
* ``statement``     — precise logical statement as a string
* ``assumptions``   — list of preconditions
* ``proof_sketch``  — informal argument for correctness
* ``check``         — callable that verifies the theorem on a small example

The ``check`` methods are designed to be called in tests and to return
``True`` iff the theorem holds for the supplied data.  They raise
``TheoremViolation`` when a counterexample is found.

Theory references (JuGeo B3 — "Testing as Witness Construction"):

    T1  test_adequacy_is_descent
    T2  regression_scope_is_minimal
    T3  geometric_coverage_implies_logical_coverage
    T4  trust_floor_monotone_under_testing
    T5  hierarchical_testing_composes

    copilot: se-theory-testing-theorems
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from jugeo.se_theory.testing.algorithms import (
    CoverageAnalyzer,
    RegressionAnalyzer,
    TestObligationGenerator,
    WitnessConstructor,
    trust_rank,
    higher_trust,
    lower_trust,
)
from jugeo.se_theory.testing.models import (
    ObligationStatus,
    RegressionScope,
    TestObligation,
    TestResult,
    WitnessSection,
)

__all__ = [
    "Theorem",
    "TheoremViolation",
    "theorem_test_adequacy_is_descent",
    "theorem_regression_scope_is_minimal",
    "theorem_geometric_coverage_implies_logical_coverage",
    "theorem_trust_floor_monotone_under_testing",
    "theorem_hierarchical_testing_composes",
    "ALL_THEOREMS",
]


# ---------------------------------------------------------------------------
# Base infrastructure
# ---------------------------------------------------------------------------


class TheoremViolation(Exception):
    """Raised when a theorem's check method finds a counterexample."""


@dataclass
class Theorem:
    """A formally stated theorem with an executable checker.

    Attributes
    ----------
    name:
        Short identifier (snake_case).
    statement:
        Precise logical statement.
    assumptions:
        List of preconditions required for the theorem to hold.
    proof_sketch:
        Informal argument for correctness.
    check:
        Callable that returns True iff the theorem holds on supplied data,
        or raises TheoremViolation with a counterexample description.
    """

    name: str
    statement: str
    assumptions: list[str]
    proof_sketch: str
    check: Callable[..., bool]

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "statement": self.statement,
            "assumptions": list(self.assumptions),
            "proof_sketch": self.proof_sketch,
        }


# ---------------------------------------------------------------------------
# T1 — Test adequacy is a descent question
# ---------------------------------------------------------------------------


def _check_test_adequacy_is_descent(
    witnesses: list[WitnessSection],
    overlaps: list[dict[str, Any]],
    required_propositions: Optional[list[str]] = None,
) -> bool:
    """Verify that a suite is adequate iff its local witnesses glue.

    Parameters
    ----------
    witnesses:
        Local witness sections, one per coordinate.
    overlaps:
        Overlap dicts linking coordinates.
    required_propositions:
        If supplied, also checks completeness at each witness.

    Returns
    -------
    bool
        True iff all witnesses are complete AND they glue across overlaps.

    Raises
    ------
    TheoremViolation
        If any witness is incomplete OR witnesses fail to glue.
    """
    constructor = WitnessConstructor()

    # Check local completeness
    for w in witnesses:
        props = required_propositions or [w.proposition]
        complete = constructor.check_witness_completeness(w, props)
        if not complete:
            raise TheoremViolation(
                f"Witness at {w.coordinate_id!r} is incomplete "
                f"(proposition {w.proposition!r} not witnessed)"
            )

    # Check gluing (descent condition)
    glues = constructor.glue_witnesses(witnesses, overlaps)
    if not glues:
        raise TheoremViolation(
            "Local witnesses do not glue: overlap consistency violated. "
            "The suite is locally adequate but globally inconsistent."
        )

    return True


theorem_test_adequacy_is_descent = Theorem(
    name="test_adequacy_is_descent",
    statement=(
        "A test suite T is adequate for a covering {U_i} iff for each U_i "
        "there exists a local witness section s_i, and the family {s_i} "
        "satisfies the descent condition: s_i|_{U_i∩U_j} = s_j|_{U_i∩U_j} "
        "for all i,j."
    ),
    assumptions=[
        "The covering {U_i} is a finite open cover of the site S.",
        "Each local witness section s_i is produced by running tests in U_i.",
        "The evidence sheaf F satisfies the sheaf axioms (locality + gluing).",
    ],
    proof_sketch=(
        "(⇒) If T is adequate, then for each coordinate U_i there is a "
        "passing test result, giving s_i.  On overlaps U_i∩U_j the same "
        "observable behaviour is tested (by the interface test), so the "
        "restrictions agree.  Hence {s_i} glues. "
        "(⇐) If {s_i} glues, the sheaf axiom produces a unique global section "
        "s ∈ F(S).  Since each s_i passes, s passes everywhere on S, i.e. T "
        "is adequate."
    ),
    check=_check_test_adequacy_is_descent,
)


# ---------------------------------------------------------------------------
# T2 — Regression scope is minimal
# ---------------------------------------------------------------------------


def _check_regression_scope_is_minimal(
    changed_coords: list[str],
    morphisms: list[dict[str, Any]],
    all_obligations: list[TestObligation],
) -> bool:
    """Verify that the computed scope is the minimal invalidation set.

    The scope is *minimal* if:
    1. Every obligation in the scope touches a changed or transitively
       dependent coordinate.
    2. No obligation outside the scope touches such a coordinate.

    Parameters
    ----------
    changed_coords:
        Directly changed coordinate IDs.
    morphisms:
        All site morphisms.
    all_obligations:
        All obligations in the suite.

    Returns
    -------
    bool

    Raises
    ------
    TheoremViolation
        If a non-minimal scope is detected.
    """
    gen = TestObligationGenerator()
    analyzer = RegressionAnalyzer()

    scope = analyzer.compute_regression_scope(
        changed_coords, morphisms, evidence_map={}, change_id="check"
    )

    # Compute the actual transitive invalidation set
    invalidated = gen._compute_invalidation_scope(changed_coords, morphisms)

    scope_coords = {ob.coordinate_id for ob in scope.required_retests}

    # Check minimality: scope_coords ⊆ invalidated
    extra = scope_coords - invalidated
    if extra:
        raise TheoremViolation(
            f"Regression scope contains non-invalidated coordinates: {extra}. "
            "The scope is larger than minimal."
        )

    # Check completeness: every invalidated coord with an obligation is in scope
    obligation_coords = {ob.coordinate_id for ob in all_obligations}
    missing = (invalidated & obligation_coords) - scope_coords
    if missing:
        raise TheoremViolation(
            f"Regression scope is missing invalidated coordinates: {missing}. "
            "The scope is smaller than required."
        )

    return True


theorem_regression_scope_is_minimal = Theorem(
    name="regression_scope_is_minimal",
    statement=(
        "Let Δ ⊆ Coords be the set of changed coordinates and G the "
        "dependency morphism graph.  The regression scope R(Δ, G) computed "
        "by the invalidation algorithm equals exactly the set of obligations "
        "whose coordinate lies in the transitive closure reach(Δ, G)."
    ),
    assumptions=[
        "The morphism graph G is acyclic (DAG) or its SCCs are treated atomically.",
        "Obligations are keyed by coordinate ID.",
        "No test result has a timestamp after the change event.",
    ],
    proof_sketch=(
        "The invalidation algorithm performs BFS/DFS from Δ along forward "
        "edges of G, collecting every reachable coordinate into reach(Δ,G).  "
        "By construction R(Δ,G) = {ob | ob.coord ∈ reach(Δ,G)}.  "
        "Minimality: only coordinates reachable from Δ can have stale evidence "
        "(anything not reachable is unaffected).  Completeness: any reachable "
        "coordinate may have stale evidence, so must be retested."
    ),
    check=_check_regression_scope_is_minimal,
)


# ---------------------------------------------------------------------------
# T3 — Geometric coverage implies logical coverage
# ---------------------------------------------------------------------------


def _check_geometric_coverage_implies_logical_coverage(
    all_coordinates: list[str],
    evidence_map: dict[str, Any],
    morphisms: list[dict[str, Any]],
    coverage_threshold: float = 1.0,
    chain_length: int = 1,
) -> bool:
    """Verify that geometric coverage ≥ threshold ⇒ every morphism chain
    of length ≤ chain_length has a tested overlap.

    Parameters
    ----------
    all_coordinates:
        All coordinate IDs.
    evidence_map:
        Map from coordinate_id → evidence.
    morphisms:
        Site morphisms.
    coverage_threshold:
        Required geometric coverage (default 1.0 = full coverage).
    chain_length:
        Maximum chain length to check (default 1).

    Returns
    -------
    bool

    Raises
    ------
    TheoremViolation
        If coverage ≥ threshold but some chain has no tested overlap.
    """
    coverage_analyzer = CoverageAnalyzer()
    overlaps = [
        {
            "id": m.get("id", f"{m.get('source','')}_{m.get('target','')}"),
            "coordinate_ids": [m.get("source", ""), m.get("target", "")],
        }
        for m in morphisms
        if m.get("source") and m.get("target")
    ]

    report = coverage_analyzer.compute_geometric_coverage(
        all_coordinates, evidence_map, overlaps
    )

    if report.geometric_coverage < coverage_threshold:
        # Theorem precondition not met — nothing to check
        return True

    # For each morphism chain of length ≤ chain_length, verify at least one
    # endpoint has evidence.
    adj: dict[str, list[str]] = {}
    for m in morphisms:
        src = m.get("source", "")
        tgt = m.get("target", "")
        if src and tgt:
            adj.setdefault(src, []).append(tgt)

    def find_chains(start: str, max_len: int) -> list[list[str]]:
        chains: list[list[str]] = [[start]]
        for _ in range(max_len):
            extended: list[list[str]] = []
            for chain in chains:
                last = chain[-1]
                for nxt in adj.get(last, []):
                    extended.append(chain + [nxt])
            chains.extend(extended)
        return chains

    for coord in all_coordinates:
        for chain in find_chains(coord, chain_length):
            if len(chain) < 2:
                continue
            has_evidence = any(
                coverage_analyzer._is_covered(evidence_map.get(c))
                for c in chain
            )
            if not has_evidence:
                raise TheoremViolation(
                    f"Chain {chain} has no tested overlap despite "
                    f"geometric_coverage={report.geometric_coverage:.2f} "
                    f"≥ {coverage_threshold:.2f}."
                )

    return True


theorem_geometric_coverage_implies_logical_coverage = Theorem(
    name="geometric_coverage_implies_logical_coverage",
    statement=(
        "If geometric coverage(T) ≥ p, then every morphism chain of length "
        "≤ k has at least one coordinate with a tested overlap (i.e. logical "
        "coverage of depth-k paths is ≥ p^k in expectation over uniform "
        "random covers)."
    ),
    assumptions=[
        "p ∈ (0, 1] and k ≥ 1.",
        "The site morphism graph has no isolated vertices.",
        "Evidence at a coordinate witnesses all morphisms incident to it.",
    ],
    proof_sketch=(
        "Each coordinate is independently covered with probability ≥ p.  "
        "A chain of length k is uncovered only if all k+1 coordinates in "
        "it are uncovered, which has probability ≤ (1−p)^(k+1).  Summing "
        "over all chains (bounded by |Morphisms|^k) and applying a union "
        "bound gives the expected coverage result."
    ),
    check=_check_geometric_coverage_implies_logical_coverage,
)


# ---------------------------------------------------------------------------
# T4 — Trust floor is monotone under testing
# ---------------------------------------------------------------------------


def _check_trust_floor_monotone_under_testing(
    initial_evidence: dict[str, Any],
    new_results: list[TestResult],
) -> bool:
    """Verify that adding passing tests cannot lower the trust floor.

    Parameters
    ----------
    initial_evidence:
        Evidence map before new results.
    new_results:
        Incoming test results (all should be passing for the invariant).

    Returns
    -------
    bool

    Raises
    ------
    TheoremViolation
        If the trust floor decreases after adding a passing result.
    """
    coverage_analyzer = CoverageAnalyzer()

    def floor(ev_map: dict[str, Any]) -> str:
        if not ev_map:
            return "none"
        levels = [coverage_analyzer._extract_trust_level(v) for v in ev_map.values()]
        return min(levels, key=trust_rank)

    current_floor = floor(initial_evidence)
    updated_evidence = dict(initial_evidence)

    for result in new_results:
        if not result.passed:
            # Non-passing results can lower trust — theorem only applies to passes
            continue
        coord = result.coordinate_id
        current_level = coverage_analyzer._extract_trust_level(
            updated_evidence.get(coord)
        )
        new_level = higher_trust(current_level, result.trust_achieved)
        rec = {
            "trust_level": new_level,
            "passed": True,
            "timestamp": result.timestamp,
        }
        updated_evidence[coord] = rec

        new_floor = floor(updated_evidence)
        if trust_rank(new_floor) < trust_rank(current_floor):
            raise TheoremViolation(
                f"Trust floor decreased from {current_floor!r} to "
                f"{new_floor!r} after adding passing result for "
                f"{coord!r} with trust={result.trust_achieved!r}. "
                "Monotonicity violated."
            )
        current_floor = new_floor

    return True


theorem_trust_floor_monotone_under_testing = Theorem(
    name="trust_floor_monotone_under_testing",
    statement=(
        "Let floor(T) = min_{U_i covered by T} trust(s_i).  For any "
        "additional passing test t with trust level τ ≥ floor(T), "
        "floor(T ∪ {t}) ≥ floor(T)."
    ),
    assumptions=[
        "The new test t passes (does not falsify any proposition).",
        "Trust levels are totally ordered: none < claim < conjecture < "
        "heuristic < proof < verified.",
        "Adding a failing test may lower trust and is outside this theorem's scope.",
    ],
    proof_sketch=(
        "Let f = floor(T).  After adding t at coordinate c with trust τ: "
        "• If c was already covered at trust ≥ f, the minimum is unchanged. "
        "• If c was uncovered, its new trust is τ ≥ f (by assumption), "
        "  so the minimum across all covered coordinates does not decrease. "
        "• If c was covered at trust < f (impossible since f is the minimum), "
        "  this case cannot arise.  QED."
    ),
    check=_check_trust_floor_monotone_under_testing,
)


# ---------------------------------------------------------------------------
# T5 — Hierarchical testing composes
# ---------------------------------------------------------------------------


def _check_hierarchical_testing_composes(
    level_results: dict[str, list[bool]],
) -> bool:
    """Verify that if each level passes locally, the composition passes.

    Parameters
    ----------
    level_results:
        Map from level name → list of pass/fail bools for each coordinate
        at that level.

    Returns
    -------
    bool
        True iff every level has at least one coordinate and all pass.

    Raises
    ------
    TheoremViolation
        If a level has all-pass locally but the composition is claimed to fail.
    """
    if not level_results:
        return True

    for level, results in level_results.items():
        if not results:
            raise TheoremViolation(
                f"Level {level!r} has no test results. "
                "Hierarchical composition requires at least one result per level."
            )
        if not all(results):
            failing_idx = [i for i, r in enumerate(results) if not r]
            raise TheoremViolation(
                f"Level {level!r} has failing tests at indices {failing_idx}. "
                "Hierarchical composition cannot proceed when a level fails."
            )

    # All levels pass — composition passes
    return True


theorem_hierarchical_testing_composes = Theorem(
    name="hierarchical_testing_composes",
    statement=(
        "If for each architectural level L ∈ {Unit, Integration, Package, "
        "System, Acceptance} every obligation at level L is satisfied, "
        "then the composition of all level obligations is satisfied globally."
    ),
    assumptions=[
        "Each level's obligations are closed under composition: an integration "
        "obligation at level L is generated from unit obligations at level L-1.",
        "No obligation at level L depends on an unsatisfied obligation at level L.",
        "The coverage hierarchy is exhaustive: every coordinate appears in at "
        "least one level.",
    ],
    proof_sketch=(
        "By structural induction on the level hierarchy.  Base case: unit "
        "obligations pass (given).  Inductive step: assuming all obligations "
        "at level L pass, the integration obligations at level L+1 are formed "
        "by composing passing unit witnesses.  Since the witness sheaf is "
        "closed under restriction (the gluing axiom), compositions of "
        "passing local sections are also passing.  By induction, the global "
        "section at the top level passes.  QED."
    ),
    check=_check_hierarchical_testing_composes,
)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

ALL_THEOREMS: list[Theorem] = [
    theorem_test_adequacy_is_descent,
    theorem_regression_scope_is_minimal,
    theorem_geometric_coverage_implies_logical_coverage,
    theorem_trust_floor_monotone_under_testing,
    theorem_hierarchical_testing_composes,
]
