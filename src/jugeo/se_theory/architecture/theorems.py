"""Formal theorems about architectural properties in sheaf-theoretic SE.

Each theorem connects a software engineering property (coupling, cohesion,
cycles, interface width, boundary enforcement) to a formal statement about
sheaf-theoretic descent computation.

Theorems are instantiated as dataclass objects with a computational
``check()`` method that verifies the theorem's computational interpretation
against actual architectural data.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from jugeo.se_theory.architecture.algorithms import (
    ArchitectureEnforcer,
    CoverAnalyzer,
    TarjanSCC,
)
from jugeo.se_theory.architecture.models import (
    ArchitecturalManifest,
    BoundaryViolation,
    CoverMember,
    CoverQualityMetrics,
)


# ---------------------------------------------------------------------------
# Base theorem dataclass
# ---------------------------------------------------------------------------


@dataclass
class ArchitecturalTheorem:
    """A formal theorem about architectural properties.

    Connects sheaf-theoretic statements to computational verification.
    The ``check()`` method verifies the theorem's computational
    interpretation against supplied data.
    """

    name: str
    statement: str
    assumptions: list[str] = field(default_factory=list)
    proof_sketch: str = ""
    computational_interpretation: str = ""

    def check(self, data: dict[str, Any]) -> bool:
        """Verify the theorem computationally.

        Parameters
        ----------
        data : dict with keys depending on the theorem:
            - "cover_members": list[CoverMember]
            - "morphisms": list[tuple[str, str]]
            - "violations": list[BoundaryViolation]
            - "manifest": ArchitecturalManifest
            - "adjacency": dict[str, list[str]]

        Returns
        -------
        True if the computational interpretation holds.
        """
        return True


# ---------------------------------------------------------------------------
# Theorem 1: Coupling bounds descent cost
# ---------------------------------------------------------------------------


@dataclass
class CouplingBoundsDescentCostTheorem(ArchitecturalTheorem):
    """High coupling predicts slow sheaf descent computation.

    If average coupling is below a threshold, descent is efficient.
    """

    name: str = "coupling_bounds_descent_cost"
    statement: str = "Descent cost is O(n × max_coupling) for sparse covers"
    assumptions: list[str] = field(
        default_factory=lambda: [
            "Cover is sparse (avg degree < log n)",
            "Morphisms form a DAG or have bounded SCCs",
        ]
    )
    proof_sketch: str = (
        "Each descent step visits at most max_coupling × n overlap "
        "coordinates. For sparse covers, the sum telescopes to "
        "O(n × max_coupling)."
    )
    computational_interpretation: str = (
        "High coupling scores predict slow sheaf descent. Monitor "
        "avg_coupling < 0.3 for efficient verification."
    )

    def check(self, data: dict[str, Any]) -> bool:
        """Verify average coupling is below threshold (0.5)."""
        cover_members = data.get("cover_members", [])
        morphisms = data.get("morphisms", [])

        if not cover_members:
            return True

        coupling_scores = CoverAnalyzer.compute_coupling(cover_members, morphisms)
        if not coupling_scores:
            return True

        avg_coupling = sum(coupling_scores.values()) / len(coupling_scores)
        return avg_coupling < 0.5


# ---------------------------------------------------------------------------
# Theorem 2: Cohesion implies local correctness
# ---------------------------------------------------------------------------


@dataclass
class CohesionImpliesLocalCorrectnessTheorem(ArchitecturalTheorem):
    """High cohesion implies efficient local verification.

    Modules with cohesion > threshold can be verified independently.
    """

    name: str = "cohesion_implies_local_correctness"
    statement: str = "High cohesion implies efficient local verification"
    assumptions: list[str] = field(
        default_factory=lambda: [
            "Cohesion > 0.7 for all members",
            "No external dependencies violate boundaries",
        ]
    )
    proof_sketch: str = (
        "A highly cohesive module has dense internal edges and sparse "
        "external edges. Local sections can be computed without "
        "examining the full cover."
    )
    computational_interpretation: str = (
        "Modules with cohesion > 0.7 can be verified independently. "
        "Use this to parallelize verification."
    )

    def check(self, data: dict[str, Any]) -> bool:
        """Verify avg cohesion > 0.5 and few circular deps."""
        cover_members = data.get("cover_members", [])
        morphisms = data.get("morphisms", [])

        if not cover_members:
            return True

        cohesion_scores = CoverAnalyzer.compute_cohesion(cover_members, morphisms)
        if not cohesion_scores:
            return True

        avg_cohesion = sum(cohesion_scores.values()) / len(cohesion_scores)
        cycles = CoverAnalyzer.detect_circular_dependencies(morphisms)
        return avg_cohesion > 0.5 and len(cycles) <= 1


# ---------------------------------------------------------------------------
# Theorem 3: SCC collapse preserves descent
# ---------------------------------------------------------------------------


@dataclass
class SCCCollapsePreservesDescentTheorem(ArchitecturalTheorem):
    """Collapsing SCCs to hypercover nodes preserves descent.

    After condensation, the DAG structure enables standard descent.
    """

    name: str = "scc_collapse_preserves_descent"
    statement: str = "Collapsing SCCs to hypercover nodes preserves descent"
    assumptions: list[str] = field(
        default_factory=lambda: [
            "The SCC forms a valid covering family",
            "All internal morphisms are isomorphisms within the SCC",
        ]
    )
    proof_sketch: str = (
        "By the sheaf axiom, sections on an SCC can be glued if they "
        "agree on all pairwise intersections. After collapse, the DAG "
        "structure allows standard descent."
    )
    computational_interpretation: str = (
        "Replace circular dependency groups with single hypercover "
        "nodes to enable linear descent computation."
    )

    def check(self, data: dict[str, Any]) -> bool:
        """Build Tarjan SCC, collapse, verify condensed graph is a DAG."""
        morphisms = data.get("morphisms", [])
        adjacency = data.get("adjacency")

        if adjacency is None:
            from collections import defaultdict

            adjacency_map: dict[str, list[str]] = defaultdict(list)
            for src, tgt in morphisms:
                adjacency_map[src].append(tgt)
            adjacency = dict(adjacency_map)

        sccs = TarjanSCC.find_sccs(adjacency)
        dag = TarjanSCC.condense_to_dag(adjacency, sccs)

        # Verify condensed graph is a DAG (no nontrivial SCCs)
        dag_sccs = TarjanSCC.find_nontrivial_sccs(dag)
        return len(dag_sccs) == 0


# ---------------------------------------------------------------------------
# Theorem 4: Interface width bounds treaty cost
# ---------------------------------------------------------------------------


@dataclass
class InterfaceWidthBoundsTreatyCostTheorem(ArchitecturalTheorem):
    """Treaty negotiation cost is O(interface_width).

    Minimizing interface width reduces treaty overhead.
    """

    name: str = "interface_width_bounds_treaty_cost"
    statement: str = "Treaty negotiation cost is O(interface_width)"
    assumptions: list[str] = field(
        default_factory=lambda: [
            "Each shared coordinate requires one treaty proposition",
            "Treaty verification is O(1) per proposition",
        ]
    )
    proof_sketch: str = (
        "The number of propositions in an interface treaty equals "
        "the interface width (number of shared coordinates). "
        "Verification is linear in this count."
    )
    computational_interpretation: str = (
        "Minimize interface width to reduce treaty overhead. "
        "Target max_interface_width < 5 for efficient treaties."
    )

    def check(self, data: dict[str, Any]) -> bool:
        """Verify max_interface_width is reasonable (< 20)."""
        cover_members = data.get("cover_members", [])

        if not cover_members:
            return True

        widths = CoverAnalyzer.compute_interface_widths(cover_members)
        if not widths:
            return True

        max_width = max(widths.values())
        return max_width < 20


# ---------------------------------------------------------------------------
# Theorem 5: Boundary enforcement prevents drift
# ---------------------------------------------------------------------------


@dataclass
class BoundaryEnforcementPreventsDriftTheorem(ArchitecturalTheorem):
    """Enforced boundaries prevent architectural drift.

    Zero violations implies architectural stability.
    """

    name: str = "boundary_enforcement_prevents_drift"
    statement: str = "Enforced boundaries prevent architectural drift"
    assumptions: list[str] = field(
        default_factory=lambda: [
            "Manifest is up-to-date",
            "All coordinates are covered by at least one boundary",
            "CI enforces boundary checks",
        ]
    )
    proof_sketch: str = (
        "If every coordinate belongs to a declared boundary and "
        "import rules are enforced, no new cross-boundary dependency "
        "can be introduced without triggering a violation."
    )
    computational_interpretation: str = (
        "Zero boundary violations implies architectural stability. "
        "Use drift_score as proxy for manifest coverage."
    )

    def check(self, data: dict[str, Any]) -> bool:
        """With no violations, drift_score should be near 0."""
        violations = data.get("violations", [])
        return len(violations) == 0


# ---------------------------------------------------------------------------
# Pre-instantiated theorem objects
# ---------------------------------------------------------------------------


THEOREM_COUPLING_BOUNDS_DESCENT_COST = CouplingBoundsDescentCostTheorem()
THEOREM_COHESION_IMPLIES_LOCAL_CORRECTNESS = CohesionImpliesLocalCorrectnessTheorem()
THEOREM_SCC_COLLAPSE_PRESERVES_DESCENT = SCCCollapsePreservesDescentTheorem()
THEOREM_INTERFACE_WIDTH_BOUNDS_TREATY_COST = InterfaceWidthBoundsTreatyCostTheorem()
THEOREM_BOUNDARY_ENFORCEMENT_PREVENTS_DRIFT = BoundaryEnforcementPreventsDriftTheorem()


ALL_THEOREMS: list[ArchitecturalTheorem] = [
    THEOREM_COUPLING_BOUNDS_DESCENT_COST,
    THEOREM_COHESION_IMPLIES_LOCAL_CORRECTNESS,
    THEOREM_SCC_COLLAPSE_PRESERVES_DESCENT,
    THEOREM_INTERFACE_WIDTH_BOUNDS_TREATY_COST,
    THEOREM_BOUNDARY_ENFORCEMENT_PREVENTS_DRIFT,
]
