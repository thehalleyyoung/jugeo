"""Theorem catalog for JuGeo ideation optimization (Ch50).

Contains formal theorem records for the mathematical foundations of
multi-objective optimization, Pareto theory, knapsack complexity, and
adaptive weight scheduling as applied to mathematical ideation research.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

# ---------------------------------------------------------------------------
# 1. Logging
# ---------------------------------------------------------------------------

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 2. Enumerations
# ---------------------------------------------------------------------------


class TheoremStatus(str, Enum):
    STATED = "stated"
    SKETCH_ONLY = "sketch_only"
    MECHANIZED = "mechanized"
    OPEN = "open"
    REFUTED = "refuted"


# ---------------------------------------------------------------------------
# 3. Core Dataclasses
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class TheoremRecord:
    """A single named theorem with statement, proof sketch, and metadata."""

    theorem_id: str
    title: str
    statement: str
    proof_sketch: str
    status: TheoremStatus
    dependencies: list[str] = field(default_factory=list)
    copilot_notes: str = ""
    chapter: str = "Ch50"
    section: str = ""

    # ------------------------------------------------------------------
    # Derived / query helpers
    # ------------------------------------------------------------------

    def short_statement(self) -> str:
        """Return the first 120 characters of the statement, truncated with '...' if longer."""
        if len(self.statement) <= 120:
            return self.statement
        return self.statement[:120] + "..."

    def is_usable(self) -> bool:
        """Return True when the theorem is in a state suitable for building on."""
        return self.status in {TheoremStatus.STATED, TheoremStatus.MECHANIZED}

    def has_proof(self) -> bool:
        """Return True when at least a proof sketch exists."""
        return self.status in {TheoremStatus.MECHANIZED, TheoremStatus.SKETCH_ONLY}

    def dependency_count(self) -> int:
        """Return the number of declared dependencies."""
        return len(self.dependencies)

    def summary(self) -> str:
        """Return a multi-line human-readable summary of this theorem record."""
        lines = [
            f"Theorem {self.theorem_id}: {self.title}",
            f"  Chapter : {self.chapter}",
            f"  Section : {self.section or '(none)'}",
            f"  Status  : {self.status.value}",
            f"  Deps    : {', '.join(self.dependencies) if self.dependencies else '(none)'}",
            f"  Statement (excerpt):",
            f"    {self.short_statement()}",
        ]
        if self.copilot_notes:
            lines.append(f"  Notes   : {self.copilot_notes}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# 4. Catalog
# ---------------------------------------------------------------------------


class TheoremCatalog:
    """Mutable collection of TheoremRecord objects indexed by theorem_id."""

    def __init__(self) -> None:
        self._records: dict[str, TheoremRecord] = {}

    # ------------------------------------------------------------------
    # Mutation
    # ------------------------------------------------------------------

    def add(self, record: TheoremRecord) -> None:
        """Register a theorem.  Overwrites any prior entry with the same id."""
        if record.theorem_id in self._records:
            _log.debug("Overwriting existing theorem %s", record.theorem_id)
        self._records[record.theorem_id] = record

    # ------------------------------------------------------------------
    # Retrieval
    # ------------------------------------------------------------------

    def get(self, theorem_id: str) -> TheoremRecord | None:
        """Return the theorem with the given id, or None if absent."""
        return self._records.get(theorem_id)

    def all_theorems(self) -> list[TheoremRecord]:
        """Return all theorems as a list, in insertion order."""
        return list(self._records.values())

    def by_status(self, status: TheoremStatus) -> list[TheoremRecord]:
        """Return all theorems with the given status."""
        return [r for r in self._records.values() if r.status == status]

    def by_chapter(self, chapter: str) -> list[TheoremRecord]:
        """Return all theorems belonging to the given chapter string."""
        return [r for r in self._records.values() if r.chapter == chapter]

    def by_section(self, section: str) -> list[TheoremRecord]:
        """Return all theorems belonging to the given section string."""
        return [r for r in self._records.values() if r.section == section]

    def count(self) -> int:
        """Return the total number of registered theorems."""
        return len(self._records)

    # ------------------------------------------------------------------
    # Dependency checking
    # ------------------------------------------------------------------

    def dependencies_met(
        self,
        theorem_id: str,
        catalog: TheoremCatalog | None = None,
    ) -> bool:
        """Return True when every dependency of *theorem_id* is present in *catalog*.

        If *catalog* is None, self is used as the reference catalog.
        """
        ref = catalog if catalog is not None else self
        record = self._records.get(theorem_id)
        if record is None:
            _log.warning("dependencies_met called for unknown theorem %s", theorem_id)
            return False
        return all(ref.get(dep) is not None for dep in record.dependencies)

    # ------------------------------------------------------------------
    # Reporting
    # ------------------------------------------------------------------

    def copilot_summary(self) -> str:
        """Return a rich multi-line summary of all theorems in the catalog."""
        header = (
            f"TheoremCatalog — {self.count()} theorem(s)\n"
            + "=" * 60
        )
        sections: dict[str, list[TheoremRecord]] = {}
        for rec in self._records.values():
            sec = rec.section or "(unsectioned)"
            sections.setdefault(sec, []).append(rec)

        parts = [header]
        for sec_name in sorted(sections):
            parts.append(f"\n[{sec_name}]")
            for rec in sections[sec_name]:
                parts.append(
                    f"  {rec.theorem_id:12s}  [{rec.status.value:12s}]  {rec.title}"
                )
        parts.append("\n" + "-" * 60)
        status_counts: dict[str, int] = {}
        for rec in self._records.values():
            status_counts[rec.status.value] = status_counts.get(rec.status.value, 0) + 1
        for sv, cnt in sorted(status_counts.items()):
            parts.append(f"  {sv}: {cnt}")
        return "\n".join(parts)


# ---------------------------------------------------------------------------
# 5. Verifier
# ---------------------------------------------------------------------------


class TheoremVerifier:
    """Stateless helper for basic sanity-checking of TheoremRecord objects."""

    # Keywords that indicate a non-trivial proof sketch
    _PROOF_KEYWORDS: frozenset[str] = frozenset(
        {
            "proof",
            "since",
            "therefore",
            "thus",
            "because",
            "suppose",
            "let",
            "assume",
            "hence",
        }
    )

    def verify_sketch(self, record: TheoremRecord) -> bool:
        """Return True when the proof sketch is non-trivial.

        A sketch is non-trivial when it is longer than 50 characters *and*
        contains at least one recognised proof keyword.
        """
        sketch = record.proof_sketch
        if len(sketch) <= 50:
            return False
        lower = sketch.lower()
        return any(kw in lower for kw in self._PROOF_KEYWORDS)

    def check_dependencies(
        self,
        record: TheoremRecord,
        catalog: TheoremCatalog,
    ) -> list[str]:
        """Return a list of dependency ids that are absent from *catalog*."""
        return [dep for dep in record.dependencies if catalog.get(dep) is None]

    def sketch_quality_score(self, record: TheoremRecord) -> float:
        """Return a quality score in [0, 1] for the proof sketch.

        Score components:
        - length / 500, capped at 0.5
        - +0.2 if there are declared dependencies
        - +0.3 if the sketch contains recognised proof keywords
        """
        length_score = min(len(record.proof_sketch) / 500.0, 0.5)
        dep_score = 0.2 if record.dependencies else 0.0
        keyword_score = 0.3 if _check_proof_keywords(record.proof_sketch) else 0.0
        return length_score + dep_score + keyword_score


# ---------------------------------------------------------------------------
# 6. Module-level helpers
# ---------------------------------------------------------------------------


def _check_proof_keywords(text: str) -> bool:
    """Return True when *text* contains at least one recognised proof keyword."""
    lower = text.lower()
    keywords = {
        "proof", "since", "therefore", "thus", "because",
        "suppose", "let", "assume", "hence",
    }
    return any(kw in lower for kw in keywords)


def _make_record(
    theorem_id: str,
    title: str,
    statement: str,
    proof_sketch: str,
    status: TheoremStatus,
    dependencies: list[str] | None = None,
    copilot_notes: str = "",
    section: str = "",
) -> TheoremRecord:
    """Factory helper that avoids repeating keyword arguments everywhere."""
    return TheoremRecord(
        theorem_id=theorem_id,
        title=title,
        statement=statement,
        proof_sketch=proof_sketch,
        status=status,
        dependencies=dependencies if dependencies is not None else [],
        copilot_notes=copilot_notes,
        chapter="Ch50",
        section=section,
    )


# ---------------------------------------------------------------------------
# 7. Default Catalog — 15 theorems
# ---------------------------------------------------------------------------

DEFAULT_CATALOG: TheoremCatalog = TheoremCatalog()

DEFAULT_CATALOG.add(
    _make_record(
        theorem_id="thm_50_1",
        title="Pareto Front Existence Under Compactness",
        statement=(
            "Let X be a compact feasible set and let f: X → R^k be a continuous "
            "vector-valued objective function. Then the Pareto front P* ⊆ X is "
            "non-empty. Moreover, every point in P* is the solution to a "
            "weighted-sum scalarization for some weight vector w ∈ Δ_k (the "
            "probability simplex)."
        ),
        proof_sketch=(
            "Since X is compact and f is continuous, f(X) is compact in R^k. "
            "The Pareto front consists of all points not dominated by any other "
            "feasible point. By compactness, the image f(X) is bounded and closed, "
            "and the set of non-dominated points is non-empty because no point can "
            "be dominated by all others simultaneously. The correspondence with "
            "weighted-sum scalarizations follows from the supporting hyperplane "
            "theorem applied to the convex hull of f(X)."
        ),
        status=TheoremStatus.STATED,
        dependencies=[],
        section="1. Foundations",
    )
)

DEFAULT_CATALOG.add(
    _make_record(
        theorem_id="thm_50_2",
        title="Weighted Sum Equivalence to Pareto Optimality for Convex Problems",
        statement=(
            "For a convex multi-objective optimization problem with k≥2 objectives, "
            "a point x* is Pareto optimal if and only if there exists a weight vector "
            "w in the relative interior of the simplex such that x* minimizes the "
            "weighted sum w^T f(x). For non-convex problems, weighted-sum "
            "scalarization may miss some Pareto optimal points."
        ),
        proof_sketch=(
            "The forward direction: if x* is Pareto optimal, construct a supporting "
            "hyperplane to the convex hull of f(X) at f(x*); the normal vector gives "
            "the weight vector. The reverse direction: if x* minimizes w^T f(x) for "
            "some w > 0 componentwise, then no other point can improve all objectives "
            "simultaneously without increasing the weighted sum. The non-convex "
            "counter-example uses a concave frontier where interior Pareto points "
            "have no corresponding weight vector."
        ),
        status=TheoremStatus.STATED,
        dependencies=["thm_50_1"],
        section="1. Foundations",
    )
)

DEFAULT_CATALOG.add(
    _make_record(
        theorem_id="thm_50_3",
        title="Novelty-Feasibility Tradeoff is Non-Convex in General",
        statement=(
            "The set of achievable (novelty, feasibility) pairs for mathematical "
            "research ideas does not in general form a convex set. There exist "
            "configurations of IdeaProposal objects where the Pareto frontier in "
            "the novelty-feasibility plane is concave, precluding identification of "
            "all Pareto optimal points via weighted-sum scalarization alone."
        ),
        proof_sketch=(
            "Construct a counter-example with three ideas: A=(0.9 novelty, 0.1 "
            "feasibility), B=(0.1 novelty, 0.9 feasibility), and C=(0.5 novelty, "
            "0.7 feasibility). The point C is not on the convex hull of {A,B,C} "
            "projected, but it IS Pareto optimal in the sense that it is "
            "non-dominated. The interior of the convex hull contains points not "
            "achievable by any single idea. This demonstrates that the frontier can "
            "have a concave shape requiring epsilon-constraint or NSGA-II methods."
        ),
        status=TheoremStatus.STATED,
        dependencies=["thm_50_1", "thm_50_2"],
        section="2. Tradeoff Analysis",
    )
)

DEFAULT_CATALOG.add(
    _make_record(
        theorem_id="thm_50_4",
        title="Budget-Constrained Pareto Front is a Subset of Unconstrained Front",
        statement=(
            "Given a budget constraint B ≥ 0, let P*(B) denote the Pareto front of "
            "feasible solutions with total cost ≤ B, and let P* denote the "
            "unconstrained Pareto front. Then P*(B) ⊆ P*. Furthermore, as B → ∞, "
            "P*(B) converges to P* in the Hausdorff metric on objective space."
        ),
        proof_sketch=(
            "Suppose x ∈ P*(B) but x ∉ P*. Then there exists y in the unconstrained "
            "feasible set that dominates x. If cost(y) ≤ B, then y ∈ P*(B) and y "
            "dominates x, contradicting x ∈ P*(B). But y might have cost > B, so "
            "the budget-constrained front can contain points dominated in the full "
            "space. The convergence statement follows from the fact that as B grows, "
            "all solutions eventually become feasible."
        ),
        status=TheoremStatus.STATED,
        dependencies=["thm_50_1"],
        section="3. Budget Optimization",
    )
)

DEFAULT_CATALOG.add(
    _make_record(
        theorem_id="thm_50_5",
        title="Crowding Distance Preserves Spread",
        statement=(
            "In the NSGA-II algorithm, the crowding distance assignment ensures that "
            "when selecting among solutions of the same Pareto rank, solutions in "
            "less-crowded regions are preferred. This selection pressure provably "
            "maintains diversity in the objective space, preventing convergence to "
            "a single region of the Pareto front."
        ),
        proof_sketch=(
            "The crowding distance of a solution is defined as the average side "
            "length of the largest cuboid containing the solution without any other "
            "solutions. Solutions with infinite crowding distance (boundary points) "
            "are always preferred. Among interior solutions, higher crowding distance "
            "indicates less crowding. The selection pressure toward high crowding "
            "distance creates a repulsion force in objective space, distributing the "
            "population across the Pareto front."
        ),
        status=TheoremStatus.STATED,
        dependencies=["thm_50_1", "thm_50_2"],
        section="4. NSGA-II",
    )
)

DEFAULT_CATALOG.add(
    _make_record(
        theorem_id="thm_50_6",
        title="Epsilon-Constraint Completeness",
        statement=(
            "The epsilon-constraint method is complete for multi-objective "
            "optimization: for any Pareto optimal point x*, there exists a choice "
            "of epsilon vector ε such that x* is the unique optimal solution to the "
            "single-objective problem obtained by optimizing the primary objective "
            "subject to all other objectives being at least ε. This completeness "
            "holds even for non-convex problems."
        ),
        proof_sketch=(
            "Given a Pareto optimal point x* with objective values f(x*) = "
            "(v_1, ..., v_k). Set ε_i = v_i - δ for small δ > 0 and i ≠ 1. "
            "Then x* satisfies all epsilon constraints. Any other feasible point "
            "satisfying the constraints must have f_1 ≤ v_1 (since x* is Pareto "
            "optimal and any improvement in objective 1 would require worsening "
            "another). The completeness follows from the ability to set δ "
            "arbitrarily small."
        ),
        status=TheoremStatus.STATED,
        dependencies=["thm_50_1"],
        section="2. Tradeoff Analysis",
    )
)

DEFAULT_CATALOG.add(
    _make_record(
        theorem_id="thm_50_7",
        title="Simulated Annealing Converges to Global Optimum in Probability",
        statement=(
            "Under a logarithmic cooling schedule T(t) = C / log(1 + t) with C "
            "sufficiently large, simulated annealing converges to the global optimum "
            "in probability as t → ∞. Specifically, P(X_t ≠ x*) → 0 as t → ∞, "
            "where X_t is the state at step t and x* is the global optimum."
        ),
        proof_sketch=(
            "This follows from the theory of non-homogeneous Markov chains. The "
            "acceptance probability exp(-ΔE/T(t)) ensures that the chain is ergodic "
            "at each temperature. The logarithmic schedule ensures that the chain "
            "spends sufficient time at each temperature level to find improvements. "
            "The convergence in probability follows from the Geman-Geman theorem "
            "applied to the configuration space of idea selections. In practice, "
            "finite-time guarantees require exponentially many steps."
        ),
        status=TheoremStatus.STATED,
        dependencies=[],
        section="5. Algorithms",
    )
)

DEFAULT_CATALOG.add(
    _make_record(
        theorem_id="thm_50_8",
        title="Knapsack NP-Hardness",
        statement=(
            "The 0-1 knapsack decision problem is NP-complete. Given a set of n "
            "items with integer weights w_i and values v_i, and a budget W, "
            "determining whether there exists a subset of items with total weight "
            "≤ W and total value ≥ V is NP-complete. Consequently, the optimization "
            "version (maximize value subject to weight constraint) is NP-hard."
        ),
        proof_sketch=(
            "NP membership follows from the polynomial-time verifiability of any "
            "proposed solution. NP-hardness follows by reduction from Subset Sum: "
            "given a Subset Sum instance with integers S = {s_1, ..., s_n} and "
            "target T, construct a Knapsack instance where w_i = v_i = s_i and "
            "W = V = T. Any solution to Knapsack with value exactly T solves Subset "
            "Sum. The reduction is polynomial in n and the bit-complexity of S."
        ),
        status=TheoremStatus.STATED,
        dependencies=[],
        section="3. Budget Optimization",
    )
)

DEFAULT_CATALOG.add(
    _make_record(
        theorem_id="thm_50_9",
        title="Greedy Approximation Ratio for Fractional Knapsack",
        statement=(
            "The fractional (greedy) relaxation of the knapsack problem, which sorts "
            "items by value-density (v_i/w_i) and selects greedily, achieves the "
            "optimal solution for the fractional version in O(n log n) time. For the "
            "integer version, the greedy algorithm achieves a 1/2-approximation: "
            "the greedy solution value is at least half the optimal integer solution "
            "value."
        ),
        proof_sketch=(
            "For the fractional version, optimality follows from an exchange "
            "argument: suppose the greedy solution is not optimal; swapping a "
            "lower-density item for a higher-density one increases total value while "
            "satisfying the capacity constraint. For the integer version, at most "
            "one item is fractionally included; taking either this item alone or all "
            "fully-included items gives at least half the optimal value."
        ),
        status=TheoremStatus.STATED,
        dependencies=["thm_50_8"],
        section="3. Budget Optimization",
    )
)

DEFAULT_CATALOG.add(
    _make_record(
        theorem_id="thm_50_10",
        title="Dominance Relation is a Partial Order",
        statement=(
            "The Pareto dominance relation ≻ on the objective space R^k defines a "
            "strict partial order: it is irreflexive (x ≻/ x), asymmetric (x ≻ y "
            "implies y ≻/ x), and transitive (x ≻ y and y ≻ z implies x ≻ z). "
            "The non-strict dominance relation ≽ defines a preorder."
        ),
        proof_sketch=(
            "Irreflexivity: a point cannot strictly dominate itself since that would "
            "require strict inequality in at least one objective while being equal "
            "in all, a contradiction. Asymmetry: if x ≻ y then x is strictly better "
            "in at least one objective; if also y ≻ x then y is strictly better in "
            "at least one objective; combining gives a contradiction. Transitivity "
            "follows directly from transitivity of the componentwise ordering on R^k."
        ),
        status=TheoremStatus.STATED,
        dependencies=[],
        section="1. Foundations",
    )
)

DEFAULT_CATALOG.add(
    _make_record(
        theorem_id="thm_50_11",
        title="NSGA-II Selection Pressure",
        statement=(
            "The NSGA-II algorithm maintains a population that converges to the "
            "Pareto front of the true objective function as the number of generations "
            "increases. The selection operator, combining non-dominated rank and "
            "crowding distance, ensures that (1) solutions of lower Pareto rank "
            "always outcompete higher-rank solutions, and (2) among equal-rank "
            "solutions, well-spread solutions are preferred."
        ),
        proof_sketch=(
            "The fast non-dominated sorting assigns rank 0 to the first Pareto "
            "front, rank 1 to the next, etc. The selection operator is elitist: "
            "the combined parent+offspring population is sorted by rank, and "
            "solutions of lower rank are selected first. Crowding distance breaks "
            "ties, preferring solutions with higher distance. The combination of "
            "elitism and crowding distance pressure has been shown empirically and "
            "theoretically to converge to a uniformly distributed approximation of "
            "the Pareto front."
        ),
        status=TheoremStatus.STATED,
        dependencies=["thm_50_1", "thm_50_5"],
        section="4. NSGA-II",
    )
)

DEFAULT_CATALOG.add(
    _make_record(
        theorem_id="thm_50_12",
        title="Weight Normalization Invariance",
        statement=(
            "The set of Pareto optimal solutions identified by weighted-sum "
            "optimization is invariant under positive rescaling of the weight "
            "vector. If w is a weight vector identifying Pareto optimal solution "
            "x*, then cw for any c > 0 identifies the same solution. Consequently, "
            "only the direction of the weight vector matters, not its magnitude."
        ),
        proof_sketch=(
            "The weighted-sum objective is W(x,w) = sum_i w_i f_i(x). For fixed "
            "w > 0 and c > 0, the minimizers of W(x,w) and W(x,cw) are identical "
            "since W(x,cw) = c * W(x,w), and positive scaling preserves minimizers. "
            "Therefore normalizing w to lie on the simplex (sum w_i = 1) without "
            "loss of generality is valid."
        ),
        status=TheoremStatus.STATED,
        dependencies=["thm_50_2"],
        section="1. Foundations",
    )
)

DEFAULT_CATALOG.add(
    _make_record(
        theorem_id="thm_50_13",
        title="Regret Bound for Minimax",
        statement=(
            "For the minimax regret criterion over a finite set of weight scenarios "
            "W = {w_1, ..., w_m}, the minimax regret solution "
            "x_mmr = argmin_{x ∈ X} max_{w ∈ W} R(x,w) exists and satisfies "
            "R(x_mmr, w) ≤ max_i ||w_i||_2 * diam(f(X)) for all w ∈ W, where "
            "diam denotes the diameter of the objective value set."
        ),
        proof_sketch=(
            "Existence follows from compactness of X and continuity of R(x,w) in x. "
            "The bound follows from the Lipschitz property of the regret function: "
            "|R(x,w) - R(x,w')| ≤ ||f(x)||_2 * ||w - w'||_2. The minimax regret "
            "is upper-bounded by the regret of the solution that maximizes the "
            "average of the objectives, which in turn is bounded by the range of "
            "objective values times the weight vector norm."
        ),
        status=TheoremStatus.STATED,
        dependencies=["thm_50_10"],
        section="2. Tradeoff Analysis",
    )
)

DEFAULT_CATALOG.add(
    _make_record(
        theorem_id="thm_50_14",
        title="Adaptive Weights Convergence",
        statement=(
            "An adaptive weight schedule that shifts weight toward objectives "
            "showing relative improvement over successive evaluations converges to "
            "a fixed point w* where all objectives contribute equally to measured "
            "improvement. Under mild regularity conditions on the performance "
            "landscape, this fixed point is reached in O(1/ε) iterations to within "
            "ε of w*."
        ),
        proof_sketch=(
            "Model the weight update as a dynamical system on the simplex. The "
            "update rule w_{t+1,i} ∝ w_{t,i} * (1 + α * improvement_i(t)) is a "
            "multiplicative weights update. By the multiplicative weights convergence "
            "theorem, after T iterations the average weight vector is within "
            "O(log k / sqrt(T)) of the fixed point in L1-norm. The convergence to "
            "equal improvement contributions follows from the Lyapunov function "
            "L(w) = sum_i w_i * log(w_i / w*_i)."
        ),
        status=TheoremStatus.STATED,
        dependencies=["thm_50_12"],
        section="2. Tradeoff Analysis",
    )
)

DEFAULT_CATALOG.add(
    _make_record(
        theorem_id="thm_50_15",
        title="Composite Objective Monotonicity",
        statement=(
            "A composite objective function formed as a non-negative weighted sum "
            "of individual objectives is monotone in each component: if a solution "
            "x improves objective i (with all other objectives held constant), the "
            "composite objective value either improves or stays the same if the "
            "weight for objective i is positive. This monotonicity property ensures "
            "that no trade-off is hidden by the composite objective."
        ),
        proof_sketch=(
            "Let F(x) = sum_i w_i f_i(x) with w_i ≥ 0. Suppose x' is obtained "
            "from x by improving objective i: f_i(x') ≥ f_i(x) (for maximization) "
            "while f_j(x') = f_j(x) for j ≠ i. Then F(x') - F(x) = "
            "w_i * (f_i(x') - f_i(x)) ≥ 0. The strictness follows when w_i > 0. "
            "Therefore any improvement in a positively-weighted component is "
            "reflected in the composite objective."
        ),
        status=TheoremStatus.STATED,
        dependencies=[],
        section="5. Algorithms",
    )
)

_log.debug(
    "DEFAULT_CATALOG populated with %d theorems.", DEFAULT_CATALOG.count()
)

# ---------------------------------------------------------------------------
# 8. Public API
# ---------------------------------------------------------------------------

__all__ = [
    "DEFAULT_CATALOG",
    "TheoremCatalog",
    "TheoremRecord",
    "TheoremStatus",
    "TheoremVerifier",
]
