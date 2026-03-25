"""
Co-evolution site and drift detection engine.

Generalises Comet-H's co-evolution problem to domain-agnostic surfaces.
Drift is measured as the descent failure rate between surfaces — how many
shared coordinates are inconsistent across surfaces.

Theory reference: FM-3 (co-evolution drift) from Comet-H.
"""
from __future__ import annotations

import time
import uuid
from typing import Any, Optional

from .models import (
    CoEvolutionState,
    DriftEdge,
    MoveCategory,
    ObligationKind,
    SemanticMove,
    Surface,
    SurfaceState,
)

__all__ = ["CoEvolutionEngine"]


class CoEvolutionEngine:
    """Domain-agnostic co-evolution site manager and drift detector."""

    def __init__(self, surfaces: list[Surface] | None = None) -> None:
        self._surfaces: dict[Surface, SurfaceState] = {}
        self._drift_edges_config: list[tuple[Surface, Surface, list[str]]] = []
        if surfaces is not None:
            for s in surfaces:
                self._surfaces[s] = SurfaceState(
                    surface=s,
                    last_modified_at=time.time(),
                )

    # ------------------------------------------------------------------
    # Surface management
    # ------------------------------------------------------------------

    def add_surface(self, surface: Surface, coordinate_ids: list[str]) -> None:
        """Register or update a surface with its coordinate ids."""
        if surface in self._surfaces:
            state = self._surfaces[surface]
            state.coordinate_ids = list(coordinate_ids)
            state.version += 1
            state.last_modified_at = time.time()
        else:
            self._surfaces[surface] = SurfaceState(
                surface=surface,
                coordinate_ids=list(coordinate_ids),
                version=1,
                last_modified_at=time.time(),
            )

    def add_drift_edge(
        self,
        surface_a: Surface,
        surface_b: Surface,
        overlap_coords: list[str],
    ) -> None:
        """Declare an overlap between two surfaces that should be monitored."""
        self._drift_edges_config.append((surface_a, surface_b, list(overlap_coords)))

    # ------------------------------------------------------------------
    # Drift computation
    # ------------------------------------------------------------------

    def compute_drift(
        self,
        sections_a: dict[str, Any],
        sections_b: dict[str, Any],
        overlap_coords: list[str],
    ) -> float:
        """Compute drift between two section snapshots over shared coordinates.

        Returns the fraction of overlap coordinates that are present in one
        snapshot but absent or different in the other.  Returns 0.0 if there
        is no overlap.
        """
        if not overlap_coords:
            return 0.0

        mismatches = 0
        for coord in overlap_coords:
            in_a = coord in sections_a
            in_b = coord in sections_b
            if in_a != in_b:
                mismatches += 1
            elif in_a and in_b and sections_a[coord] != sections_b[coord]:
                mismatches += 1

        return mismatches / len(overlap_coords)

    def full_drift_analysis(self) -> CoEvolutionState:
        """Run a full drift analysis across all configured drift edges.

        For each configured edge we use coordinate lists and version
        differences as proxies for section content.  Returns a
        ``CoEvolutionState`` snapshot.
        """
        drift_edges: list[DriftEdge] = []
        total_drift = 0.0
        edge_count = 0

        for surface_a, surface_b, overlap in self._drift_edges_config:
            state_a = self._surfaces.get(surface_a)
            state_b = self._surfaces.get(surface_b)
            if state_a is None or state_b is None:
                continue

            # Build proxy section dicts from coordinate id lists
            sections_a = {cid: state_a.version for cid in state_a.coordinate_ids}
            sections_b = {cid: state_b.version for cid in state_b.coordinate_ids}

            drift_score = self.compute_drift(sections_a, sections_b, overlap)

            drift_edges.append(
                DriftEdge(
                    surface_a=surface_a,
                    surface_b=surface_b,
                    overlap_coordinates=list(overlap),
                    drift_score=drift_score,
                    last_checked_at=time.time(),
                )
            )
            total_drift += drift_score
            edge_count += 1

        overall = total_drift / edge_count if edge_count else 0.0

        return CoEvolutionState(
            surfaces={s.value: st for s, st in self._surfaces.items()},
            drift_edges=drift_edges,
            overall_drift_score=overall,
            is_synchronized=self.is_synchronized(
                CoEvolutionState(
                    drift_edges=drift_edges,
                    overall_drift_score=overall,
                )
            ),
        )

    # ------------------------------------------------------------------
    # Drift detectors
    # ------------------------------------------------------------------

    def detect_specification_drift(
        self, state: CoEvolutionState
    ) -> list[DriftEdge]:
        """Return drift edges with drift_score > 0.3 (FM-3 threshold)."""
        return [e for e in state.drift_edges if e.drift_score > 0.3]

    def detect_stale_surfaces(
        self,
        state: CoEvolutionState,
        max_age_s: float = 86400.0,
    ) -> list[Surface]:
        """Return surfaces whose last modification is older than *max_age_s*."""
        now = time.time()
        stale: list[Surface] = []
        for _key, ss in state.surfaces.items():
            if now - ss.last_modified_at > max_age_s:
                stale.append(ss.surface)
        return stale

    def detect_trust_violations(
        self, state: CoEvolutionState
    ) -> list[dict[str, Any]]:
        """Return dicts describing trust-floor mismatches between surfaces.

        For every drift edge with non-zero drift, check whether the two
        surfaces have differing trust floors.
        """
        violations: list[dict[str, Any]] = []
        for edge in state.drift_edges:
            if edge.drift_score <= 0.0:
                continue
            sa = state.surfaces.get(edge.surface_a.value)
            sb = state.surfaces.get(edge.surface_b.value)
            if sa is None or sb is None:
                continue
            if sa.trust_floor != sb.trust_floor:
                for coord in edge.overlap_coordinates:
                    violations.append(
                        {
                            "surface": edge.surface_a.value,
                            "coordinate_id": coord,
                            "issue": (
                                f"trust floor mismatch: "
                                f"{sa.trust_floor} vs {sb.trust_floor}"
                            ),
                        }
                    )
        return violations

    # ------------------------------------------------------------------
    # Synchronisation
    # ------------------------------------------------------------------

    def synchronization_plan(
        self, state: CoEvolutionState
    ) -> list[SemanticMove]:
        """Generate moves to reduce drift for every edge above threshold."""
        drifted = self.detect_specification_drift(state)
        # Sort by priority (highest first)
        drifted.sort(key=self._drift_priority, reverse=True)

        moves: list[SemanticMove] = []
        for edge in drifted:
            category = self._category_for_edge(edge)
            obligation_kind = self._obligation_for_edge(edge)
            move = SemanticMove(
                id=str(uuid.uuid4()),
                category=category,
                name=f"sync-{edge.surface_a.value}-{edge.surface_b.value}",
                description=(
                    f"Synchronise {edge.surface_a.value} and "
                    f"{edge.surface_b.value} (drift={edge.drift_score:.2f})"
                ),
                target_surfaces=[edge.surface_a, edge.surface_b],
                generates_obligations=[obligation_kind],
                estimated_cost=max(1.0, edge.drift_score * len(edge.overlap_coordinates)),
                priority=self._drift_priority(edge),
            )
            moves.append(move)
        return moves

    def _drift_priority(self, edge: DriftEdge) -> float:
        """Higher drift + more overlap → higher priority."""
        return edge.drift_score * max(1, len(edge.overlap_coordinates))

    def is_synchronized(
        self,
        state: CoEvolutionState,
        threshold: float = 0.1,
    ) -> bool:
        """Return True if all drift edges are below *threshold*."""
        return all(e.drift_score <= threshold for e in state.drift_edges)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _category_for_edge(edge: DriftEdge) -> MoveCategory:
        """Determine the best move category for a drifted edge."""
        pair = {edge.surface_a, edge.surface_b}
        if Surface.SPECIFICATION in pair and Surface.CODE in pair:
            return MoveCategory.GROUNDING
        if Surface.CLAIMS in pair and Surface.EVIDENCE in pair:
            return MoveCategory.AUDIT
        if Surface.CODE in pair and Surface.TESTING in pair:
            return MoveCategory.TESTING
        if Surface.CODE in pair and Surface.DOCUMENTATION in pair:
            return MoveCategory.DOCUMENTATION
        if Surface.CODE in pair and Surface.BENCHMARKS in pair:
            return MoveCategory.BENCHMARKING
        if Surface.CODE in pair and Surface.DEPLOYMENT in pair:
            return MoveCategory.DEPLOYMENT
        return MoveCategory.VERIFICATION

    @staticmethod
    def _obligation_for_edge(edge: DriftEdge) -> ObligationKind:
        """Determine the obligation kind generated by fixing a drifted edge."""
        pair = {edge.surface_a, edge.surface_b}
        if Surface.SPECIFICATION in pair and Surface.CODE in pair:
            return ObligationKind.GROUNDING
        if Surface.CLAIMS in pair and Surface.EVIDENCE in pair:
            return ObligationKind.AUDIT
        if Surface.CODE in pair and Surface.TESTING in pair:
            return ObligationKind.TESTING
        if Surface.CODE in pair and Surface.DOCUMENTATION in pair:
            return ObligationKind.DOCUMENTATION
        if Surface.CODE in pair and Surface.BENCHMARKS in pair:
            return ObligationKind.BENCHMARK
        if Surface.CODE in pair and Surface.DEPLOYMENT in pair:
            return ObligationKind.DEPLOYMENT
        return ObligationKind.VERIFICATION
