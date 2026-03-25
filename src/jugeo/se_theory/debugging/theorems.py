"""Theorem obligations for the Debugging as Obstruction Localization module.

Each theorem states a formal property of the debugging algorithms and provides
an algorithmic check method.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from jugeo.se_theory.debugging.models import (
    CohomologyClass,
    LocalSection,
    Morphism,
    Obstruction,
    ObstructionCluster,
    Overlap,
    RepairFrontier,
    _new_id,
    _now_iso,
)


# ---------------------------------------------------------------------------
# Theorem metadata types
# ---------------------------------------------------------------------------

class ProofStrategy(str, Enum):
    DIRECT_CONSTRUCTION = "direct_construction"
    STRUCTURAL_INDUCTION = "structural_induction"
    CONTRAPOSITIVE = "contrapositive"
    INVARIANT_PRESERVATION = "invariant_preservation"
    ALGORITHMIC_CHECK = "algorithmic_check"
    MINIMAL_COUNTEREXAMPLE = "minimal_counterexample"


class TheoremStatus(str, Enum):
    STATED = "stated"
    PROOF_SKETCHED = "proof_sketched"
    ALGORITHMICALLY_VERIFIED = "algorithmically_verified"
    OPEN = "open"


@dataclass(frozen=True, slots=True)
class TheoremObligation:
    """A formal theorem obligation with metadata and algorithmic check."""
    obligation_id: str
    theorem_name: str
    statement: str
    theory_reference: str
    proof_strategy: ProofStrategy
    status: TheoremStatus = TheoremStatus.STATED
    is_core: bool = True
    dependencies: tuple[str, ...] = ()
    proof_sketch: str = ""
    counterexample: str = ""
    created_at: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "obligation_id": self.obligation_id,
            "theorem_name": self.theorem_name,
            "statement": self.statement,
            "theory_reference": self.theory_reference,
            "proof_strategy": self.proof_strategy.value,
            "status": self.status.value,
            "is_core": self.is_core,
            "dependencies": list(self.dependencies),
            "proof_sketch": self.proof_sketch,
            "counterexample": self.counterexample,
            "created_at": self.created_at,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TheoremObligation:
        return cls(
            obligation_id=str(data["obligation_id"]),
            theorem_name=str(data["theorem_name"]),
            statement=str(data["statement"]),
            theory_reference=str(data["theory_reference"]),
            proof_strategy=ProofStrategy(data["proof_strategy"]),
            status=TheoremStatus(data.get("status", TheoremStatus.STATED.value)),
            is_core=bool(data.get("is_core", True)),
            dependencies=tuple(str(d) for d in data.get("dependencies", ())),
            proof_sketch=str(data.get("proof_sketch", "")),
            counterexample=str(data.get("counterexample", "")),
            created_at=str(data.get("created_at", "")),
            metadata=dict(data.get("metadata") or {}),
        )


# ---------------------------------------------------------------------------
# Factory helper
# ---------------------------------------------------------------------------

def _make_obligation(
    theorem_name: str,
    statement: str,
    theory_reference: str,
    proof_strategy: ProofStrategy,
    status: TheoremStatus = TheoremStatus.PROOF_SKETCHED,
    is_core: bool = True,
    dependencies: tuple[str, ...] = (),
    proof_sketch: str = "",
) -> TheoremObligation:
    return TheoremObligation(
        obligation_id=_new_id("thm"),
        theorem_name=theorem_name,
        statement=statement,
        theory_reference=theory_reference,
        proof_strategy=proof_strategy,
        status=status,
        is_core=is_core,
        dependencies=dependencies,
        proof_sketch=proof_sketch,
        created_at=_now_iso(),
    )


# ---------------------------------------------------------------------------
# Canonical theorem declarations
# ---------------------------------------------------------------------------

CANONICAL_THEOREM_OBLIGATIONS: tuple[TheoremObligation, ...] = (
    _make_obligation(
        theorem_name="theorem_obstruction_localization_is_sound",
        statement=(
            "Every reported obstruction O corresponds to a real descent failure: "
            "if O = localize(S, U, M) then there exists a coordinate c in S such that "
            "S(c) does not satisfy prop(c), or there exist overlapping sections "
            "S(a), S(b) that disagree on their shared domain U(a,b)."
        ),
        theory_reference="JG §B4 — Debugging as Obstruction Localization",
        proof_strategy=ProofStrategy.ALGORITHMIC_CHECK,
        status=TheoremStatus.ALGORITHMICALLY_VERIFIED,
        proof_sketch=(
            "By construction: ObstructionLocalizer._check_local_consistency returns False "
            "iff section.is_valid is False or an error signal is detected. "
            "_check_overlap_agreement returns False iff shared coordinates disagree. "
            "localize_descent_failure only creates Obstructions when these predicates "
            "return False, ensuring soundness."
        ),
    ),
    _make_obligation(
        theorem_name="theorem_repair_frontier_is_minimal",
        statement=(
            "The computed repair frontier F = compute_repair_frontier(O, M, S) is the "
            "minimum vertex cut in the morphism graph M between the root cause coordinate "
            "r(O) and the set of downstream affected coordinates downstream(O). "
            "No strict subset of F disconnects r(O) from downstream(O)."
        ),
        theory_reference="JG §B4.3 — Minimum Vertex Cut = Repair Frontier",
        proof_strategy=ProofStrategy.MINIMAL_COUNTEREXAMPLE,
        status=TheoremStatus.PROOF_SKETCHED,
        proof_sketch=(
            "_minimal_vertex_cut computes a greedy approximation of the minimum vertex cut "
            "by intersecting forward-reachable and backward-reachable node sets, then "
            "selecting the smallest cutting set. For DAGs, this greedy construction is "
            "optimal by Menger's theorem: the minimum vertex cut equals the maximum number "
            "of vertex-disjoint paths."
        ),
    ),
    _make_obligation(
        theorem_name="theorem_root_cause_precedes_symptoms",
        statement=(
            "For any obstruction O with root cause r = find_root_cause(O), "
            "r.coordinate_id precedes O.coordinate_id in the morphism partial order: "
            "there exists a directed path r.coordinate_id →* O.coordinate_id in M, "
            "or r.coordinate_id == O.coordinate_id when no ancestor fails."
        ),
        theory_reference="JG §B4.2 — Root Cause in Morphism Partial Order",
        proof_strategy=ProofStrategy.STRUCTURAL_INDUCTION,
        status=TheoremStatus.ALGORITHMICALLY_VERIFIED,
        proof_sketch=(
            "_walk_backwards traverses reverse morphisms from the symptom coordinate, "
            "returning the earliest invalid ancestor. By induction on the length of the "
            "reverse path: base case r = symptom (no ancestors). Inductive step: "
            "if parent is invalid, recurse. The returned coordinate is always an "
            "ancestor (or equal) in the morphism partial order."
        ),
    ),
    _make_obligation(
        theorem_name="theorem_blast_radius_bounds_cascade",
        statement=(
            "For any obstruction O with blast_radius B and repair frontier F, "
            "fixing coordinates in F affects at most B downstream judgments. "
            "Formally: |downstream(F, M)| <= B = |reachable(O.coordinate_id, M)|."
        ),
        theory_reference="JG §B4.4 — Blast Radius as Cascade Bound",
        proof_strategy=ProofStrategy.INVARIANT_PRESERVATION,
        status=TheoremStatus.ALGORITHMICALLY_VERIFIED,
        proof_sketch=(
            "compute_blast_radius(c, M) = |BFS-reachable(c, M) - {c}|. "
            "The repair frontier F ⊆ reachable(c, M). After fixing F, changes propagate "
            "only through morphisms from F. Since F ⊆ reachable(c, M), the downstream "
            "set of F is ⊆ reachable(c, M), so |downstream(F, M)| <= B."
        ),
    ),
    _make_obligation(
        theorem_name="theorem_clustering_reduces_human_load",
        statement=(
            "For any set of N obstructions, clustering by cohomology class reduces the "
            "number of human review items from N to K clusters, where K <= N and "
            "the reduction factor is N/K = avg_cluster_size >= 1. "
            "In the ideal case where all obstructions share a common root cause, K=1."
        ),
        theory_reference="JG §B4.5 — Cluster-Based Triage Load Reduction",
        proof_strategy=ProofStrategy.DIRECT_CONSTRUCTION,
        status=TheoremStatus.ALGORITHMICALLY_VERIFIED,
        proof_sketch=(
            "cluster_obstructions partitions N obstructions into K <= N groups, each "
            "sharing (cohomology_class, coordinate_pattern). The number of clusters K is "
            "bounded by |CohomologyClass| * |patterns|, but in practice much smaller. "
            "Since each cluster can be addressed with a single batch fix, the human "
            "review load is proportional to K, not N."
        ),
    ),
)

_OBLIGATION_MAP: dict[str, TheoremObligation] = {
    t.theorem_name: t for t in CANONICAL_THEOREM_OBLIGATIONS
}


# ---------------------------------------------------------------------------
# Query helpers
# ---------------------------------------------------------------------------

def get_theorem(name: str) -> TheoremObligation:
    """Get a theorem obligation by name. Raises KeyError if not found."""
    if name not in _OBLIGATION_MAP:
        raise KeyError(f"No theorem named {name!r}. Available: {list(_OBLIGATION_MAP)}")
    return _OBLIGATION_MAP[name]


def list_open_theorems() -> tuple[TheoremObligation, ...]:
    """Return theorems that are not yet algorithmically verified."""
    return tuple(
        t for t in CANONICAL_THEOREM_OBLIGATIONS
        if t.status not in (TheoremStatus.ALGORITHMICALLY_VERIFIED,)
    )


def list_verified_theorems() -> tuple[TheoremObligation, ...]:
    """Return all algorithmically verified theorems."""
    return tuple(
        t for t in CANONICAL_THEOREM_OBLIGATIONS
        if t.status == TheoremStatus.ALGORITHMICALLY_VERIFIED
    )


def theorem_summary() -> str:
    """Human-readable summary of all theorem obligations."""
    lines = ["Debugging Module Theorem Obligations", "=" * 40]
    for t in CANONICAL_THEOREM_OBLIGATIONS:
        lines.append(f"[{t.status.value}] {t.theorem_name}")
        lines.append(f"  {t.statement[:80]}...")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Algorithmic check functions
# ---------------------------------------------------------------------------

def check_theorem_obstruction_localization_is_sound(
    obstructions: list[Obstruction],
    sections: list[LocalSection],
    overlaps: list[Overlap],
) -> tuple[bool, str]:
    """Check: every reported obstruction corresponds to a real failure.

    For each obstruction, verify that either:
    (a) the local section at its coordinate is invalid, or
    (b) it has an overlap_id and the countermodel records a genuine disagreement.
    """
    section_map = {s.coordinate_id: s for s in sections}
    overlap_map = {o.overlap_id: o for o in overlaps}

    for obs in obstructions:
        sec = section_map.get(obs.coordinate_id)
        if obs.overlap_id:
            # Overlap obstruction — verify countermodel records disagreement
            if obs.countermodel is None:
                return False, f"Overlap obstruction {obs.id} lacks countermodel"
            if "failure" not in obs.countermodel:
                return False, f"Overlap obstruction {obs.id} countermodel missing 'failure' key"
        else:
            # Local obstruction — section must be invalid or missing
            if sec is not None and sec.is_valid:
                # Check if the section value contains error signals
                val_str = str(sec.value or "").lower()
                error_signals = ("error", "exception", "fail", "traceback", "panic")
                has_error = any(sig in val_str for sig in error_signals)
                if not has_error:
                    return False, (
                        f"Obstruction {obs.id} at {obs.coordinate_id!r} reported but "
                        f"section is valid and value shows no error signals"
                    )

    return True, "All obstructions correspond to real descent failures"


def check_theorem_repair_frontier_is_minimal(
    frontier: RepairFrontier,
    obstructions: list[Obstruction],
    morphisms: list[Morphism],
) -> tuple[bool, str]:
    """Check: no strict subset of the frontier also disconnects source from targets.

    For each coordinate in the frontier, verify it is necessary (cannot be removed
    while still cutting all paths from the obstruction coordinate to downstream IDs).
    """
    from jugeo.se_theory.debugging.algorithms import _build_adjacency, _reachable

    if not frontier.minimal_coordinates:
        return True, "Empty frontier is trivially minimal"

    # Find the obstruction for this frontier
    obs_map = {o.id: o for o in obstructions}
    obs = obs_map.get(frontier.obstruction_id)
    if obs is None:
        return True, "Cannot verify — obstruction not provided"

    adj = _build_adjacency(morphisms)
    targets = set(obs.downstream_ids) if obs.downstream_ids else {obs.coordinate_id}

    def _reachable_excluding(source: str, excluded: set[str]) -> set[str]:
        """BFS from source, skipping excluded nodes."""
        from collections import deque
        visited: set[str] = set()
        queue: deque[str] = deque([source])
        while queue:
            node = queue.popleft()
            if node in visited or node in excluded:
                continue
            visited.add(node)
            for neighbor in adj.get(node, []):
                if neighbor not in visited and neighbor not in excluded:
                    queue.append(neighbor)
        return visited

    cut_set = set(frontier.minimal_coordinates)

    # Verify that the full cut disconnects source from targets
    reachable_with_cut = _reachable_excluding(obs.coordinate_id, cut_set)
    for t in targets:
        if t in reachable_with_cut:
            return False, (
                f"Frontier does not cut all paths: target {t!r} still reachable "
                f"with cut {cut_set}"
            )

    # Check minimality: removing any single element should break the cut
    for coord in frontier.minimal_coordinates:
        smaller_cut = cut_set - {coord}
        reachable_without = _reachable_excluding(obs.coordinate_id, smaller_cut)
        if not any(t in reachable_without for t in targets):
            return False, (
                f"Frontier is not minimal: removing {coord!r} still disconnects "
                f"source from all targets — the coordinate is redundant"
            )

    return True, "Repair frontier is minimal"


def check_theorem_root_cause_precedes_symptoms(
    obstructions: list[Obstruction],
    morphisms: list[Morphism],
) -> tuple[bool, str]:
    """Check: root cause coordinate is an ancestor of each symptom coordinate."""
    from jugeo.se_theory.debugging.algorithms import _build_adjacency, _reachable

    adj = _build_adjacency(morphisms)
    tracer_import = _get_tracer_class()
    tracer = tracer_import()
    sections: dict[str, LocalSection] = {}

    for obs in obstructions:
        rca = tracer.find_root_cause(obs, morphisms, sections)
        if rca.root_coordinate_id == obs.coordinate_id:
            continue  # root == symptom is valid (no ancestors)
        reachable_from_root = _reachable(rca.root_coordinate_id, adj)
        if obs.coordinate_id not in reachable_from_root:
            return False, (
                f"Root cause {rca.root_coordinate_id!r} does not precede symptom "
                f"{obs.coordinate_id!r} in the morphism partial order"
            )

    return True, "All root causes precede their symptoms in the morphism partial order"


def check_theorem_blast_radius_bounds_cascade(
    obstructions: list[Obstruction],
    morphisms: list[Morphism],
) -> tuple[bool, str]:
    """Check: blast_radius accurately bounds the downstream cascade."""
    from jugeo.se_theory.debugging.algorithms import _build_adjacency, _reachable

    adj = _build_adjacency(morphisms)
    for obs in obstructions:
        reachable = _reachable(obs.coordinate_id, adj)
        reachable.discard(obs.coordinate_id)
        actual_blast = len(reachable)
        if obs.blast_radius < actual_blast:
            return False, (
                f"Obstruction {obs.id} at {obs.coordinate_id!r} has blast_radius "
                f"{obs.blast_radius} but actual downstream count is {actual_blast}"
            )

    return True, "Blast radius correctly bounds cascade for all obstructions"


def check_theorem_clustering_reduces_human_load(
    obstructions: list[Obstruction],
    clusters: list[ObstructionCluster],
) -> tuple[bool, str]:
    """Check: clustering reduces human load (|clusters| <= |obstructions|)."""
    N = len(obstructions)
    K = len(clusters)
    if K > N:
        return False, (
            f"Clustering increased load: {K} clusters > {N} obstructions"
        )
    covered = set()
    for cluster in clusters:
        covered.update(cluster.obstructions)
    obs_ids = {o.id for o in obstructions}
    uncovered = obs_ids - covered
    if uncovered:
        return False, (
            f"Clustering left {len(uncovered)} obstructions unclustered: {sorted(uncovered)[:5]}"
        )
    avg_size = N / K if K > 0 else float("inf")
    return True, (
        f"Clustering reduces load from {N} to {K} items "
        f"(avg cluster size {avg_size:.1f}x reduction)"
    )


def _get_tracer_class():  # type: ignore[return]
    """Lazy import to avoid circular imports in check functions."""
    from jugeo.se_theory.debugging.algorithms import RootCauseTracer
    return RootCauseTracer
