"""Code review algorithms: scope analysis, compatibility checking, auto-review.

Implements the computational core of the code-review module:

* **ReviewScopeAnalyzer** — determines which overlaps, treaties, teams, and
  trust levels are affected by a set of changed coordinates.
* **SectionCompatibilityChecker** — runs the individual review checks
  (internal consistency, overlap compatibility, trust adequacy, public
  honesty, treaty compliance) and aggregates them into a verdict.
* **AutoReviewer** — end-to-end automated review that combines scope
  analysis, compatibility checking, and reviewer suggestion.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from jugeo.se_theory.code_review.models import (
    ReviewCheck,
    ReviewFinding,
    ReviewScope,
    ReviewVerdict,
    TreatyImpact,
)


# ---------------------------------------------------------------------------
# Trust ordering helper
# ---------------------------------------------------------------------------

_TRUST_ORDER: list[str] = ["claim", "conjecture", "heuristic", "proof", "verified"]


def _trust_rank(level: str) -> int:
    """Numeric rank of a trust level (case-insensitive)."""
    try:
        return _TRUST_ORDER.index(level.lower().strip())
    except ValueError:
        return -1


# ---------------------------------------------------------------------------
# ReviewScopeAnalyzer
# ---------------------------------------------------------------------------


class ReviewScopeAnalyzer:
    """Determines the review scope for a set of changed coordinates."""

    def compute_scope(
        self,
        changed_coords: list[str],
        morphisms: dict[str, list[str]],
        treaties: dict[str, dict[str, Any]],
        team_assignments: dict[str, list[str]],
        before_evidence: Optional[dict[str, dict[str, Any]]] = None,
        after_evidence: Optional[dict[str, dict[str, Any]]] = None,
        before_sections: Optional[dict[str, dict[str, Any]]] = None,
        after_sections: Optional[dict[str, dict[str, Any]]] = None,
    ) -> ReviewScope:
        """Build a complete :class:`ReviewScope` for the given changes."""
        # Expand changed_coords through morphisms to find all affected coords
        affected: set[str] = set()
        queue = list(changed_coords)
        while queue:
            node = queue.pop(0)
            if node in affected:
                continue
            affected.add(node)
            for nbr in morphisms.get(node, []):
                if nbr not in affected:
                    queue.append(nbr)

        affected_list = sorted(affected)

        # Build overlap dict from morphisms: overlaps are pairs of adjacent coords
        all_overlaps: dict[str, list[str]] = {}
        for coord, neighbours in morphisms.items():
            for nbr in neighbours:
                key = f"{min(coord, nbr)}_{max(coord, nbr)}"
                if key not in all_overlaps:
                    all_overlaps[key] = sorted({coord, nbr})

        affected_overlaps = self._find_affected_overlaps(affected_list, all_overlaps)
        affected_treaties = self._find_affected_treaties(affected_overlaps, treaties)
        affected_teams = self._find_affected_teams(affected_list, team_assignments)
        trust_changes = self._detect_trust_changes(
            changed_coords,
            before_evidence or {},
            after_evidence or {},
        )
        public_changes = self._detect_public_changes(
            changed_coords,
            before_sections or {},
            after_sections or {},
        )

        return ReviewScope(
            changed_coordinates=sorted(changed_coords),
            affected_overlaps=affected_overlaps,
            affected_treaties=affected_treaties,
            affected_teams=affected_teams,
            trust_changes=trust_changes,
            public_projection_changes=public_changes,
        )

    # -- private helpers -----------------------------------------------------

    def _find_affected_overlaps(
        self,
        changed_coords: list[str],
        all_overlaps: dict[str, list[str]],
    ) -> list[str]:
        """Return overlap IDs that include any changed coordinate."""
        changed_set = set(changed_coords)
        result: list[str] = []
        for overlap_id, members in sorted(all_overlaps.items()):
            if set(members) & changed_set:
                result.append(overlap_id)
        return result

    def _find_affected_treaties(
        self,
        affected_overlaps: list[str],
        treaties: dict[str, dict[str, Any]],
    ) -> list[str]:
        """Return treaty IDs whose overlaps list intersects *affected_overlaps*."""
        overlap_set = set(affected_overlaps)
        result: list[str] = []
        for treaty_id, info in sorted(treaties.items()):
            treaty_overlaps = set(info.get("overlaps", []))
            if treaty_overlaps & overlap_set:
                result.append(treaty_id)
        return result

    def _find_affected_teams(
        self,
        affected_coords: list[str],
        team_assignments: dict[str, list[str]],
    ) -> list[str]:
        """Return team names that own any affected coordinate."""
        coord_set = set(affected_coords)
        result: list[str] = []
        for team_name, coords in sorted(team_assignments.items()):
            if set(coords) & coord_set:
                result.append(team_name)
        return result

    def _detect_trust_changes(
        self,
        changed_coords: list[str],
        before_evidence: dict[str, dict[str, Any]],
        after_evidence: dict[str, dict[str, Any]],
    ) -> dict[str, tuple[str, str]]:
        """Return ``{coord_id: (old_trust, new_trust)}`` for changed coords
        where trust actually changed.
        """
        result: dict[str, tuple[str, str]] = {}
        for coord in changed_coords:
            old_trust = before_evidence.get(coord, {}).get("trust", "claim")
            new_trust = after_evidence.get(coord, {}).get("trust", "claim")
            if old_trust != new_trust:
                result[coord] = (old_trust, new_trust)
        return result

    def _detect_public_changes(
        self,
        changed_coords: list[str],
        before_sections: dict[str, dict[str, Any]],
        after_sections: dict[str, dict[str, Any]],
    ) -> list[str]:
        """Return coord IDs where the ``public`` status changed."""
        result: list[str] = []
        for coord in changed_coords:
            before_public = before_sections.get(coord, {}).get("public", False)
            after_public = after_sections.get(coord, {}).get("public", False)
            if before_public != after_public:
                result.append(coord)
        return sorted(result)


# ---------------------------------------------------------------------------
# SectionCompatibilityChecker
# ---------------------------------------------------------------------------


class SectionCompatibilityChecker:
    """Runs individual review checks and aggregates them into a verdict."""

    def check_internal_consistency(
        self,
        new_sections: dict[str, dict[str, Any]],
        propositions: list[str],
    ) -> list[ReviewFinding]:
        """Check that every section references only known propositions.

        Also warn if a section has trust ``"claim"`` but lists no propositions.
        """
        known = set(propositions)
        findings: list[ReviewFinding] = []
        for coord_id, section in sorted(new_sections.items()):
            section_props = section.get("propositions", [])
            for prop in section_props:
                if prop not in known:
                    findings.append(
                        ReviewFinding(
                            check=ReviewCheck.INTERNAL_CONSISTENCY,
                            coordinate_id=coord_id,
                            severity="error",
                            description=f"Unknown proposition '{prop}' referenced in section.",
                            suggestion=f"Remove or define proposition '{prop}'.",
                        )
                    )
            trust = section.get("trust", "claim")
            if trust.lower() == "claim" and not section_props:
                findings.append(
                    ReviewFinding(
                        check=ReviewCheck.INTERNAL_CONSISTENCY,
                        coordinate_id=coord_id,
                        severity="warning",
                        description="Section has trust='claim' but no propositions.",
                        suggestion="Add at least one proposition or raise trust level.",
                    )
                )
        return findings

    def check_overlap_compatibility(
        self,
        new_sections: dict[str, dict[str, Any]],
        existing_sections: dict[str, dict[str, Any]],
        overlaps: dict[str, list[str]],
    ) -> list[ReviewFinding]:
        """For each overlap, check that both coords agree on shared propositions."""
        findings: list[ReviewFinding] = []
        # Merge sections for lookup
        all_sections = {**existing_sections, **new_sections}

        for overlap_id, members in sorted(overlaps.items()):
            if len(members) < 2:
                continue
            for i in range(len(members)):
                for j in range(i + 1, len(members)):
                    coord_a = members[i]
                    coord_b = members[j]
                    props_a = set(
                        all_sections.get(coord_a, {}).get("propositions", [])
                    )
                    props_b = set(
                        all_sections.get(coord_b, {}).get("propositions", [])
                    )
                    shared = props_a & props_b
                    if not shared and (props_a or props_b):
                        # Only flag if at least one side has propositions but
                        # they disagree entirely
                        trust_a = all_sections.get(coord_a, {}).get("trust", "claim")
                        trust_b = all_sections.get(coord_b, {}).get("trust", "claim")
                        if trust_a != trust_b:
                            findings.append(
                                ReviewFinding(
                                    check=ReviewCheck.OVERLAP_COMPATIBILITY,
                                    coordinate_id=overlap_id,
                                    severity="error",
                                    description=(
                                        f"Overlap '{overlap_id}': coords {coord_a} "
                                        f"and {coord_b} disagree on propositions "
                                        f"and trust levels ({trust_a} vs {trust_b})."
                                    ),
                                )
                            )
        return findings

    def check_trust_adequacy(
        self,
        new_sections: dict[str, dict[str, Any]],
        trust_requirements: dict[str, str],
    ) -> list[ReviewFinding]:
        """Flag coordinates where actual trust is below the required minimum."""
        findings: list[ReviewFinding] = []
        for coord_id, required in sorted(trust_requirements.items()):
            actual = new_sections.get(coord_id, {}).get("trust", "claim")
            if _trust_rank(actual) < _trust_rank(required):
                findings.append(
                    ReviewFinding(
                        check=ReviewCheck.TRUST_ADEQUACY,
                        coordinate_id=coord_id,
                        severity="error",
                        description=(
                            f"Trust level '{actual}' is below required '{required}'."
                        ),
                        suggestion=f"Raise trust to at least '{required}'.",
                    )
                )
        return findings

    def check_public_honesty(
        self,
        new_sections: dict[str, dict[str, Any]],
        public_claims: dict[str, list[str]],
    ) -> list[ReviewFinding]:
        """Flag coords where public claims are not backed by adequate trust."""
        findings: list[ReviewFinding] = []
        for coord_id, claims in sorted(public_claims.items()):
            section = new_sections.get(coord_id, {})
            trust = section.get("trust", "claim")
            # Public claims need at least "heuristic" level trust
            if claims and _trust_rank(trust) < _trust_rank("heuristic"):
                findings.append(
                    ReviewFinding(
                        check=ReviewCheck.PUBLIC_HONESTY,
                        coordinate_id=coord_id,
                        severity="error",
                        description=(
                            f"Public claims exist but trust is only '{trust}'."
                        ),
                        suggestion="Raise trust or remove public claims.",
                    )
                )
        return findings

    def check_treaty_compliance(
        self,
        changed_coords: list[str],
        treaties: dict[str, dict[str, Any]],
    ) -> list[TreatyImpact]:
        """Return :class:`TreatyImpact` for each treaty affected by changed coords."""
        changed_set = set(changed_coords)
        impacts: list[TreatyImpact] = []
        for treaty_id, info in sorted(treaties.items()):
            parties = info.get("parties", [])
            if set(parties) & changed_set:
                impacts.append(
                    TreatyImpact(
                        treaty_id=treaty_id,
                        parties=list(parties),
                        change_description=f"Coordinates {sorted(set(parties) & changed_set)} changed.",
                        renegotiation_needed=True,
                    )
                )
        return impacts

    def full_review(
        self,
        scope: ReviewScope,
        sections: dict[str, dict[str, Any]],
        propositions: list[str],
        treaties: dict[str, dict[str, Any]],
        trust_reqs: dict[str, str],
        public_claims: Optional[dict[str, list[str]]] = None,
        existing_sections: Optional[dict[str, dict[str, Any]]] = None,
        overlaps: Optional[dict[str, list[str]]] = None,
    ) -> ReviewVerdict:
        """Run all checks, aggregate findings, and compute overall verdict.

        * ``BLOCK`` if any block-severity finding.
        * ``REQUEST_CHANGES`` if any error finding.
        * ``APPROVE`` otherwise.
        """
        all_findings: list[ReviewFinding] = []

        # Internal consistency
        all_findings.extend(
            self.check_internal_consistency(sections, propositions)
        )

        # Overlap compatibility
        all_findings.extend(
            self.check_overlap_compatibility(
                sections,
                existing_sections or {},
                overlaps or {},
            )
        )

        # Trust adequacy
        all_findings.extend(
            self.check_trust_adequacy(sections, trust_reqs)
        )

        # Public honesty
        all_findings.extend(
            self.check_public_honesty(sections, public_claims or {})
        )

        # Treaty compliance -> convert to findings
        treaty_impacts = self.check_treaty_compliance(
            scope.changed_coordinates, treaties
        )
        for impact in treaty_impacts:
            if impact.renegotiation_needed:
                all_findings.append(
                    ReviewFinding(
                        check=ReviewCheck.TREATY_COMPLIANCE,
                        coordinate_id=impact.treaty_id,
                        severity="warning",
                        description=f"Treaty '{impact.treaty_id}' may need renegotiation.",
                    )
                )

        # Aggregate
        pass_count = 0
        fail_count = 0
        warning_count = 0
        has_block = False
        has_error = False

        for f in all_findings:
            if f.severity == "block":
                has_block = True
                fail_count += 1
            elif f.severity == "error":
                has_error = True
                fail_count += 1
            elif f.severity == "warning":
                warning_count += 1
            else:
                pass_count += 1

        # Count checks that produced no findings as passes
        total_checks = 6  # number of ReviewCheck kinds
        checks_with_findings = len({f.check for f in all_findings})
        pass_count += max(total_checks - checks_with_findings, 0)

        if has_block:
            overall = "BLOCK"
        elif has_error:
            overall = "REQUEST_CHANGES"
        else:
            overall = "APPROVE"

        trust_adequate = not any(
            f.check == ReviewCheck.TRUST_ADEQUACY and f.severity == "error"
            for f in all_findings
        )
        descent_preserved = not any(
            f.check == ReviewCheck.OVERLAP_COMPATIBILITY and f.severity == "error"
            for f in all_findings
        )

        return ReviewVerdict(
            pr_id=scope.pr_id,
            findings=all_findings,
            pass_count=pass_count,
            fail_count=fail_count,
            warning_count=warning_count,
            overall=overall,
            required_reviewers=list(scope.affected_teams),
            trust_adequate=trust_adequate,
            descent_preserved=descent_preserved,
        )


# ---------------------------------------------------------------------------
# AutoReviewer
# ---------------------------------------------------------------------------


class AutoReviewer:
    """End-to-end automated review combining scope analysis, compatibility
    checking, and reviewer suggestion.
    """

    def __init__(self) -> None:
        self._scope_analyzer = ReviewScopeAnalyzer()
        self._checker = SectionCompatibilityChecker()

    def auto_review(
        self,
        changed_coords: list[str],
        morphisms: dict[str, list[str]],
        sections: dict[str, dict[str, Any]],
        treaties: dict[str, dict[str, Any]],
        evidence: dict[str, dict[str, Any]],
        team_assignments: Optional[dict[str, list[str]]] = None,
        trust_requirements: Optional[dict[str, str]] = None,
        propositions: Optional[list[str]] = None,
    ) -> ReviewVerdict:
        """Full end-to-end review using scope analysis and compatibility checking."""
        scope = self._scope_analyzer.compute_scope(
            changed_coords=changed_coords,
            morphisms=morphisms,
            treaties=treaties,
            team_assignments=team_assignments or {},
        )

        # Collect all known propositions from sections if not given
        if propositions is None:
            props_set: set[str] = set()
            for section in sections.values():
                props_set.update(section.get("propositions", []))
            propositions = sorted(props_set)

        verdict = self._checker.full_review(
            scope=scope,
            sections=sections,
            propositions=propositions,
            treaties=treaties,
            trust_reqs=trust_requirements or {},
        )
        return verdict

    def suggest_reviewers(self, scope: ReviewScope) -> list[str]:
        """Return affected teams from scope as suggested reviewers."""
        return list(scope.affected_teams)

    def estimate_review_effort(self, scope: ReviewScope) -> float:
        """Estimate review effort in abstract units.

        Formula: ``len(changed_coordinates) * 1.0 + len(affected_treaties) * 3.0
        + len(affected_overlaps) * 0.5``
        """
        return (
            len(scope.changed_coordinates) * 1.0
            + len(scope.affected_treaties) * 3.0
            + len(scope.affected_overlaps) * 0.5
        )
