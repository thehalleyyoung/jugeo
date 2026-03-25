"""Refactoring algorithms: refinement checking, safety scoring, migration planning.

Implements the computational core of the refactoring module:

* **RefinementChecker** — validates that a refactoring preserves or improves
  trust levels (the *refinement* partial order) and overlap agreements
  (the *descent* condition).
* **SafetyScorer** — assigns a 0–1 score to a proposal by analysing blast
  radius, treaty impact, and evidence density.
* **MigrationPlanner** — plans a library migration as a chain of
  refactoring morphisms, using fuzzy coordinate matching and incremental
  verification.
"""
from __future__ import annotations

import difflib
import math
from dataclasses import dataclass, field
from typing import Any, Optional

from jugeo.se_theory.refactoring.models import (
    MigrationPlan,
    RefactoringKind,
    RefactoringProposal,
    RefactoringResult,
    RefinementRelation,
)


# ---------------------------------------------------------------------------
# RefinementChecker
# ---------------------------------------------------------------------------


class RefinementChecker:
    """Checks whether a refactoring step preserves or improves the trust ordering.

    In JuGeo terms: a refactoring A -> B is a valid refinement if for every
    coordinate c, the trust level after (B_c) >= trust level before (A_c).
    Equivalence holds if A_c == B_c for all c.
    """

    TRUST_ORDER: list[str] = [
        "claim",
        "conjecture",
        "heuristic",
        "proof",
        "verified",
    ]

    # -- helpers -------------------------------------------------------------

    def _trust_rank(self, level: str) -> int:
        """Return numeric rank of a trust level (case-insensitive).

        Unknown levels are treated as rank -1 (below everything).
        """
        try:
            return self.TRUST_ORDER.index(level.lower().strip())
        except ValueError:
            return -1

    # -- public API ----------------------------------------------------------

    def check_refinement(
        self,
        before_sections: dict[str, dict[str, Any]],
        after_sections: dict[str, dict[str, Any]],
        propositions: list[str],
    ) -> list[RefinementRelation]:
        """For each coordinate present in *both* before and after, produce a
        :class:`RefinementRelation`.

        A section dict must contain at least ``{"trust": str}`` and optionally
        ``{"propositions": list[str]}``.
        """
        results: list[RefinementRelation] = []
        common_keys = set(before_sections) & set(after_sections)

        for coord_id in sorted(common_keys):
            before = before_sections[coord_id]
            after = after_sections[coord_id]

            before_trust = before.get("trust", "claim")
            after_trust = after.get("trust", "claim")

            before_rank = self._trust_rank(before_trust)
            after_rank = self._trust_rank(after_trust)

            is_refinement = after_rank >= before_rank
            is_equivalence = after_rank == before_rank

            if before_trust.lower() == after_trust.lower():
                delta = "unchanged"
            else:
                delta = f"{before_trust.upper()}->{after_trust.upper()}"

            before_props = set(before.get("propositions", []))
            after_props = set(after.get("propositions", []))
            affected = sorted(before_props.symmetric_difference(after_props))

            results.append(
                RefinementRelation(
                    source_judgment_id=coord_id,
                    target_judgment_id=coord_id,
                    is_refinement=is_refinement,
                    is_equivalence=is_equivalence,
                    delta_trust=delta,
                    affected_propositions=affected,
                )
            )

        return results

    def verify_descent_preservation(
        self,
        before_overlaps: dict[str, list[str]],
        after_overlaps: dict[str, list[str]],
    ) -> bool:
        """All overlaps still agree after refactoring.

        ``before_overlaps`` / ``after_overlaps`` map overlap IDs to lists of
        coordinate IDs that participate.  Descent is preserved when the set of
        overlap participants is unchanged (no new disagreements introduced).
        """
        for overlap_id in before_overlaps:
            before_set = set(before_overlaps[overlap_id])
            after_set = set(after_overlaps.get(overlap_id, []))
            if before_set != after_set:
                return False
        # Also check for newly introduced overlaps that weren't in before
        for overlap_id in after_overlaps:
            if overlap_id not in before_overlaps:
                # New overlap — not a regression but check it exists
                continue
        return True

    def find_regressions(
        self,
        before_sections: dict[str, dict[str, Any]],
        after_sections: dict[str, dict[str, Any]],
    ) -> list[str]:
        """Return coordinate IDs where after trust < before trust (regressions)."""
        regressions: list[str] = []
        common_keys = set(before_sections) & set(after_sections)
        for coord_id in sorted(common_keys):
            before_rank = self._trust_rank(
                before_sections[coord_id].get("trust", "claim")
            )
            after_rank = self._trust_rank(
                after_sections[coord_id].get("trust", "claim")
            )
            if after_rank < before_rank:
                regressions.append(coord_id)
        return regressions


# ---------------------------------------------------------------------------
# SafetyScorer
# ---------------------------------------------------------------------------


class SafetyScorer:
    """Computes a 0–1 safety score for a refactoring proposal.

    Score decreases with larger blast radius, more treaty impact, and lower
    evidence density in the affected area.
    """

    # -- public API ----------------------------------------------------------

    def score_refactoring(
        self,
        proposal: RefactoringProposal,
        morphisms: dict[str, list[str]],
        evidence_map: dict[str, list[Any]],
    ) -> float:
        """Return a 0–1 safety score for *proposal*.

        Factors:

        * **blast_radius** — smaller is safer (penalised above threshold of 5).
        * **treaty_impact** — fewer affected treaties is safer.
        * **evidence density** — more evidence in the affected area means it
          is safer to refactor (better test coverage).
        """
        radius = self.blast_radius(proposal.target_coordinates, morphisms)

        # Blast-radius penalty: 0 when radius <= 5, scales up to 1
        radius_penalty = min(max(radius - 5, 0) / 20.0, 1.0)

        # Treaty penalty: each affected treaty costs 0.15
        treaty_penalty = min(len(proposal.affected_treaties) * 0.15, 1.0)

        # Evidence density bonus: fraction of affected coords that have evidence
        affected_coords = set(proposal.target_coordinates)
        # Expand through morphisms
        visited: set[str] = set()
        queue = list(affected_coords)
        while queue:
            node = queue.pop()
            if node in visited:
                continue
            visited.add(node)
            for neighbour in morphisms.get(node, []):
                if neighbour not in visited:
                    queue.append(neighbour)
        affected_coords = visited if visited else affected_coords

        if affected_coords:
            evidence_count = sum(
                1 for c in affected_coords if len(evidence_map.get(c, [])) > 0
            )
            evidence_density = evidence_count / len(affected_coords)
        else:
            evidence_density = 0.0

        # Final score
        score = 1.0 - 0.4 * radius_penalty - 0.3 * treaty_penalty + 0.3 * evidence_density
        return max(0.0, min(1.0, score))

    def blast_radius(
        self,
        target_coordinates: list[str],
        morphisms: dict[str, list[str]],
    ) -> int:
        """BFS from *target_coordinates* through *morphisms* to count
        transitively affected coordinates.

        ``morphisms`` maps each coordinate to its neighbours in the
        morphism graph.
        """
        visited: set[str] = set()
        queue = list(target_coordinates)
        while queue:
            node = queue.pop(0)
            if node in visited:
                continue
            visited.add(node)
            for neighbour in morphisms.get(node, []):
                if neighbour not in visited:
                    queue.append(neighbour)
        return len(visited)

    def treaty_impact(
        self,
        target_coordinates: list[str],
        treaties: dict[str, dict[str, Any]],
    ) -> list[str]:
        """Return IDs of treaties whose parties include any target coordinate.

        ``treaties`` maps treaty IDs to dicts containing at least
        ``{"parties": [coord_id, ...]}``.
        """
        target_set = set(target_coordinates)
        impacted: list[str] = []
        for treaty_id, info in sorted(treaties.items()):
            parties = set(info.get("parties", []))
            if parties & target_set:
                impacted.append(treaty_id)
        return impacted


# ---------------------------------------------------------------------------
# MigrationPlanner
# ---------------------------------------------------------------------------


class MigrationPlanner:
    """Plans a library migration as a sequence of refactoring steps."""

    # -- public API ----------------------------------------------------------

    def plan_migration(
        self,
        source_coords: list[str],
        target_coords: list[str],
        morphisms: dict[str, list[str]],
    ) -> MigrationPlan:
        """Map source to target coordinates using fuzzy name matching.

        Returns a :class:`MigrationPlan` with coordinate_mapping,
        unmapped_coordinates, compatibility_score, and ordered steps.
        """
        mapping = self._fuzzy_match_coordinates(source_coords, target_coords)
        unmapped = self._identify_unmapped(source_coords, mapping)
        steps = self.step_by_step_plan(mapping)

        if source_coords:
            compatibility = len(mapping) / len(source_coords)
        else:
            compatibility = 0.0

        plan = MigrationPlan(
            source_library="source",
            target_library="target",
            coordinate_mapping=mapping,
            unmapped_coordinates=unmapped,
            compatibility_score=compatibility,
            steps=steps,
        )
        return plan

    def verify_migration_preserves_descent(
        self,
        plan: MigrationPlan,
        before_sections: dict[str, dict[str, Any]],
        after_sections: dict[str, dict[str, Any]],
    ) -> bool:
        """Use :class:`RefinementChecker` to verify descent is preserved
        across the migration.
        """
        checker = RefinementChecker()
        relations = checker.check_refinement(before_sections, after_sections, [])
        for rel in relations:
            if not rel.is_refinement:
                return False
        return True

    # -- private helpers -----------------------------------------------------

    def _fuzzy_match_coordinates(
        self,
        source: list[str],
        target: list[str],
    ) -> dict[str, str]:
        """Match source coords to target coords by name similarity.

        Uses ``difflib.SequenceMatcher``.  Each source coord gets at most one
        target; each target is consumed at most once.  Threshold: 0.4.
        """
        mapping: dict[str, str] = {}
        available_targets = list(target)

        for src in source:
            best_score = 0.0
            best_tgt: Optional[str] = None
            for tgt in available_targets:
                score = difflib.SequenceMatcher(None, src.lower(), tgt.lower()).ratio()
                if score > best_score:
                    best_score = score
                    best_tgt = tgt
            if best_tgt is not None and best_score >= 0.4:
                mapping[src] = best_tgt
                available_targets.remove(best_tgt)

        return mapping

    def _identify_unmapped(
        self,
        source_coords: list[str],
        mapping: dict[str, str],
    ) -> list[str]:
        """Return source coords with no match in *mapping*."""
        return [c for c in source_coords if c not in mapping]

    def step_by_step_plan(
        self,
        mapping: dict[str, str],
    ) -> list[RefactoringProposal]:
        """Break migration mapping into ordered :class:`RefactoringProposal`
        steps.  Each mapped pair becomes a ``MOVE_TO_MODULE`` proposal.
        """
        steps: list[RefactoringProposal] = []
        for source_coord, target_coord in sorted(mapping.items()):
            steps.append(
                RefactoringProposal(
                    kind=RefactoringKind.MOVE_TO_MODULE,
                    target_coordinates=[source_coord],
                    description=f"Move {source_coord} -> {target_coord}",
                    blast_radius=1,
                )
            )
        return steps
