r"""Core algorithms for the ``jugeo.se_theory.teams`` package.

Theory (JuGeo — "Teams as Sheaves of Authority", B9):
    All algorithms here operate on the jurisdiction data model:

    * **CodeownersParser**    — parses CODEOWNERS files into a
      ``CodeownersMapping``; resolves which teams own a coordinate by
      selecting the most-specific matching pattern (higher priority wins).
    * **JurisdictionManager** — builds the authority sheaf from teams and
      coordinates; detects gluing conflicts; resolves delegation chains.
    * **EscalationRouter**    — routes unresolved obstructions to the
      responsible team and escalates through the organisational stalk.
    * **TreatyNegotiator**    — negotiates ``CrossTeamTreaty`` objects at
      overlapping coordinates and checks compliance.
    * **JurisdictionReporter**— produces ``JurisdictionReport`` summaries.

    copilot: se-theory-teams-algorithms
"""
from __future__ import annotations

import fnmatch
import re
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from jugeo.se_theory.teams.models import (
    AuthorityGrant,
    AuthorityLevel,
    CodeownersEntry,
    CodeownersMapping,
    CrossTeamTreaty,
    EscalationLevel,
    Jurisdiction,
    JurisdictionReport,
    ObstructionEscalation,
    Team,
    authority_rank,
    weaker_authority,
    _iso_now,
)

__all__ = [
    "CodeownersParser",
    "JurisdictionManager",
    "EscalationRouter",
    "TreatyNegotiator",
    "JurisdictionReporter",
    # trust helpers re-exported for convenience
    "TRUST_ORDER",
    "trust_rank",
]

# ---------------------------------------------------------------------------
# Trust level ordering
# ---------------------------------------------------------------------------

TRUST_ORDER: list[str] = [
    "none",
    "claim",
    "conjecture",
    "heuristic",
    "proof",
    "verified",
]


def trust_rank(level: str) -> int:
    """Return numeric rank of a trust-level string (0 = none, 5 = verified)."""
    try:
        return TRUST_ORDER.index(level.lower())
    except ValueError:
        return 0


def _lower_trust(a: str, b: str) -> str:
    """Return the trust level with the lower rank (more attenuated)."""
    return a if trust_rank(a) <= trust_rank(b) else b


# ---------------------------------------------------------------------------
# CodeownersParser
# ---------------------------------------------------------------------------


class CodeownersParser:
    """Parse GitHub-style CODEOWNERS files and resolve coordinate owners.

    CODEOWNERS format rules implemented:
    * Lines beginning with ``#`` are comments and are ignored.
    * Blank lines are ignored.
    * Each non-comment line is: ``<pattern> <@team-or-user>...``
    * Later rules *override* earlier rules for the same path (GitHub
      semantics), but we also assign a *priority* score so callers can
      rank by specificity independently.
    """

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def parse_codeowners(self, content: str) -> CodeownersMapping:
        """Parse CODEOWNERS file content into a ``CodeownersMapping``.

        Parameters
        ----------
        content:
            Raw text content of the CODEOWNERS file.

        Returns
        -------
        CodeownersMapping
            Entries sorted from least to most specific (ascending priority).
        """
        entries: list[CodeownersEntry] = []
        for line in content.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            parts = stripped.split()
            if not parts:
                continue
            pattern = parts[0]
            teams = [
                t.lstrip("@") for t in parts[1:] if t.startswith("@")
            ]
            if not teams:
                # Non-team owners (plain usernames) kept as-is
                teams = [t for t in parts[1:]]
            priority = self._priority_score(pattern)
            entries.append(
                CodeownersEntry(
                    pattern=pattern,
                    teams=teams,
                    priority=priority,
                )
            )
        entries.sort(key=lambda e: e.priority)
        return CodeownersMapping(entries=entries)

    def resolve_owner(
        self,
        coordinate_id: str,
        mapping: CodeownersMapping,
    ) -> list[str]:
        """Find the owning teams for ``coordinate_id``.

        Applies GitHub semantics: the *last* (highest priority) matching
        pattern wins.

        Parameters
        ----------
        coordinate_id:
            Coordinate or file path to resolve.
        mapping:
            Parsed CODEOWNERS mapping.

        Returns
        -------
        list[str]
            Team handles of the winning entry, or ``[]`` if no pattern matches.
        """
        winning_teams: list[str] = []
        winning_priority: int = -1
        for entry in sorted(mapping.entries, key=lambda e: e.priority):
            if self._matches(coordinate_id, entry.pattern):
                # GitHub: last matching rule wins — we keep overwriting
                winning_teams = entry.teams
                winning_priority = entry.priority
        _ = winning_priority  # used implicitly via iteration order
        return winning_teams

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _pattern_to_regex(self, pattern: str) -> str:
        """Convert a CODEOWNERS glob pattern to a Python regex string.

        Rules:
        * ``**`` matches any path segment (including ``/``).
        * ``*`` matches within a single segment (not ``/``).
        * A leading ``/`` anchors to the repo root.
        * A trailing ``/`` matches the directory and everything inside.
        """
        anchored = pattern.startswith("/")
        if anchored:
            pattern = pattern[1:]

        # Replace ** before * to avoid double-processing
        # We use a placeholder to avoid re-replacing
        pattern = pattern.replace("**", "\x00DOUBLESTAR\x00")
        pattern = re.escape(pattern)
        pattern = pattern.replace(re.escape("\x00DOUBLESTAR\x00"), ".*")
        pattern = pattern.replace(r"\*", "[^/]*")
        pattern = pattern.replace(r"\?", "[^/]")

        if anchored:
            regex = "^" + pattern
        else:
            regex = "(^|.*?/)" + pattern

        if not pattern.endswith("/"):
            regex = regex + "(/.*)?$"
        else:
            regex = regex + ".*$"

        return regex

    def _priority_score(self, pattern: str) -> int:
        """Compute a specificity score for a CODEOWNERS pattern.

        Higher score = more specific = wins in case of conflict.
        Heuristic:
        * Each literal path segment adds 10 points.
        * Each ``**`` wildcard costs -5 points.
        * Each ``*``  wildcard costs -3 points.
        * A leading ``/`` anchor adds 5 points.
        """
        score = 0
        if pattern.startswith("/"):
            score += 5
            pattern = pattern[1:]

        segments = pattern.rstrip("/").split("/")
        for seg in segments:
            if seg == "**":
                score -= 5
            elif "*" in seg or "?" in seg:
                score -= 3
                score += 2  # still some specificity from the segment
            else:
                score += 10
        return score

    def _matches(self, path: str, pattern: str) -> bool:
        """Return True if ``path`` matches ``pattern``."""
        # Use fnmatch for simple cases, regex for complex
        if "**" in pattern:
            try:
                regex = self._pattern_to_regex(pattern)
                return bool(re.match(regex, path))
            except re.error:
                return False
        # Normalise: remove leading /
        norm_pattern = pattern.lstrip("/")
        # Direct fnmatch
        if fnmatch.fnmatch(path, norm_pattern):
            return True
        # Directory prefix match: pattern "src/" matches "src/foo/bar.py"
        if pattern.endswith("/") and path.startswith(pattern.lstrip("/")):
            return True
        # Suffix match: if pattern has no /, match basename
        if "/" not in norm_pattern:
            basename = path.split("/")[-1]
            return fnmatch.fnmatch(basename, norm_pattern)
        return False


# ---------------------------------------------------------------------------
# JurisdictionManager
# ---------------------------------------------------------------------------


class JurisdictionManager:
    """Manage the authority sheaf: assign, resolve, delegate, conflict-detect.

    All public methods are pure — they return new data structures and do not
    mutate their inputs.
    """

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def assign_jurisdictions(
        self,
        teams: list[Team],
        coordinates: list[str],
    ) -> dict[str, list[Jurisdiction]]:
        """Assign coordinates to teams based on their ``coordinate_patterns``.

        Parameters
        ----------
        teams:
            All known teams.
        coordinates:
            All coordinate IDs in the site.

        Returns
        -------
        dict[str, list[Jurisdiction]]
            Mapping from coordinate ID to the list of ``Jurisdiction`` objects
            that cover it (one per matching team).
        """
        parser = CodeownersParser()
        result: dict[str, list[Jurisdiction]] = {c: [] for c in coordinates}
        for team in teams:
            for coord in coordinates:
                for pattern in team.coordinate_patterns:
                    if parser._matches(coord, pattern):
                        result[coord].append(
                            Jurisdiction(
                                team_id=team.id,
                                coordinate_pattern=pattern,
                                authority=team.authority_level,
                                trust_ceiling=team.trust_ceiling,
                                delegated_from=None,
                                delegation_depth=0,
                            )
                        )
                        break  # one jurisdiction per team per coord
        return result

    def resolve_authority(
        self,
        coordinate_id: str,
        jurisdictions: dict[str, list[Jurisdiction]],
    ) -> Optional[Jurisdiction]:
        """Return the most authoritative ``Jurisdiction`` for a coordinate.

        When multiple teams cover a coordinate, the one with the highest
        ``authority_level`` rank wins.  Ties are broken by lower
        ``delegation_depth`` (primary owners preferred over delegatees).

        Parameters
        ----------
        coordinate_id:
            The coordinate to resolve.
        jurisdictions:
            Output of :meth:`assign_jurisdictions`.

        Returns
        -------
        Jurisdiction or None
            The winning jurisdiction, or ``None`` if the coordinate is
            uncovered.
        """
        candidates = jurisdictions.get(coordinate_id, [])
        if not candidates:
            return None
        return max(
            candidates,
            key=lambda j: (
                authority_rank(j.authority),
                -j.delegation_depth,
            ),
        )

    def check_delegation_chain(
        self,
        grant: AuthorityGrant,
        jurisdictions: dict[str, list[Jurisdiction]],
    ) -> bool:
        """Verify that a delegation grant is consistent with existing jurisdictions.

        A grant is valid if the ``granting_team`` already holds at least
        the granted authority level over all stated patterns.

        Parameters
        ----------
        grant:
            The ``AuthorityGrant`` to validate.
        jurisdictions:
            Existing jurisdiction assignments.

        Returns
        -------
        bool
            ``True`` if the grant is valid; ``False`` otherwise.
        """
        parser = CodeownersParser()
        for pattern in grant.coordinate_patterns:
            # Find any coordinate in jurisdictions that matches the pattern
            granted_ok = False
            for coord, jlist in jurisdictions.items():
                if not parser._matches(coord, pattern):
                    continue
                for j in jlist:
                    if j.team_id == grant.granting_team:
                        if authority_rank(j.authority) >= authority_rank(
                            grant.authority
                        ):
                            granted_ok = True
                            break
                if granted_ok:
                    break
            if not granted_ok:
                return False
        return True

    def compute_trust_ceiling(
        self,
        coordinate_id: str,
        delegation_chain: list[Jurisdiction],
    ) -> str:
        """Compute effective trust ceiling after attenuation through a chain.

        The effective ceiling is the minimum (weakest) trust ceiling across
        all jurisdictions in the delegation chain.

        Parameters
        ----------
        coordinate_id:
            Coordinate being assessed (used only for documentation purposes).
        delegation_chain:
            Ordered list of jurisdictions from primary owner down to delegatee.

        Returns
        -------
        str
            Effective trust ceiling string (e.g. ``"proof"``).
        """
        _ = coordinate_id
        if not delegation_chain:
            return "none"
        ceiling = delegation_chain[0].trust_ceiling
        for j in delegation_chain[1:]:
            ceiling = _lower_trust(ceiling, j.trust_ceiling)
        return ceiling

    def detect_conflicts(
        self,
        jurisdictions: dict[str, list[Jurisdiction]],
    ) -> list[tuple[str, list[Jurisdiction]]]:
        """Find coordinates claimed by multiple teams at the same authority level.

        Parameters
        ----------
        jurisdictions:
            Output of :meth:`assign_jurisdictions`.

        Returns
        -------
        list[tuple[str, list[Jurisdiction]]]
            Each tuple is ``(coordinate_id, conflicting_jurisdictions)``.
        """
        conflicts: list[tuple[str, list[Jurisdiction]]] = []
        for coord, jlist in jurisdictions.items():
            if len(jlist) <= 1:
                continue
            # Conflict when two different teams have the same authority rank
            by_rank: dict[int, list[Jurisdiction]] = {}
            for j in jlist:
                r = authority_rank(j.authority)
                by_rank.setdefault(r, []).append(j)
            for r, group in by_rank.items():
                if len(group) > 1:
                    conflicts.append((coord, group))
                    break
        return conflicts

    def suggest_resolutions(
        self,
        conflicts: list[tuple[str, list[Jurisdiction]]],
    ) -> list[dict[str, Any]]:
        """Suggest a resolution strategy for each jurisdiction conflict.

        Parameters
        ----------
        conflicts:
            Output of :meth:`detect_conflicts`.

        Returns
        -------
        list[dict]
            One resolution dict per conflict, with keys:
            ``coordinate_id``, ``conflicting_teams``, ``suggestion``,
            ``preferred_team`` (the first listed team as a tiebreaker).
        """
        resolutions: list[dict[str, Any]] = []
        for coord, jlist in conflicts:
            teams = [j.team_id for j in jlist]
            # Simple heuristic: prefer lower delegation_depth (primary owner)
            preferred = min(jlist, key=lambda j: j.delegation_depth)
            resolutions.append(
                {
                    "coordinate_id": coord,
                    "conflicting_teams": teams,
                    "preferred_team": preferred.team_id,
                    "suggestion": (
                        f"Designate @{preferred.team_id} as primary owner "
                        f"and downgrade others to REVIEW_ONLY."
                    ),
                }
            )
        return resolutions


# ---------------------------------------------------------------------------
# EscalationRouter
# ---------------------------------------------------------------------------


class EscalationRouter:
    """Route and escalate obstruction events through the authority hierarchy.

    An obstruction is a failed gluing datum in the evidence sheaf.  When
    a local team cannot resolve it within their sub-cover, the escalation
    router walks up the organisational stalk until a responsible party is
    found.
    """

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def route_obstruction(
        self,
        obstruction_id: str,
        coordinate_id: str,
        blast_radius: int,
        jurisdictions: dict[str, list[Jurisdiction]],
        is_critical_path: bool = False,
    ) -> ObstructionEscalation:
        """Create and route an ``ObstructionEscalation`` for a failed gluing.

        Parameters
        ----------
        obstruction_id:
            Unique ID for the obstruction being reported.
        coordinate_id:
            Coordinate at which the gluing failed.
        blast_radius:
            Number of coordinates transitively affected.
        jurisdictions:
            Current jurisdiction assignments.
        is_critical_path:
            Whether the affected coordinate is on the critical dependency path.

        Returns
        -------
        ObstructionEscalation
            Newly created escalation record with ``status="OPEN"``.
        """
        level = self.compute_escalation_level(blast_radius, is_critical_path)
        chain = self.build_escalation_chain(coordinate_id, jurisdictions)
        responsible = chain[0] if chain else "unassigned"
        return ObstructionEscalation(
            obstruction_id=obstruction_id,
            coordinate_id=coordinate_id,
            blast_radius=blast_radius,
            escalation_level=level,
            responsible_team=responsible,
            escalation_chain=chain,
            status="OPEN",
            created_at=_iso_now(),
        )

    def compute_escalation_level(
        self,
        blast_radius: int,
        is_critical_path: bool = False,
    ) -> EscalationLevel:
        """Determine escalation scope from blast radius and criticality.

        Parameters
        ----------
        blast_radius:
            Number of transitively affected coordinates.
        is_critical_path:
            True if the obstruction blocks the critical dependency path.

        Returns
        -------
        EscalationLevel
            Appropriate escalation scope.
        """
        if is_critical_path and blast_radius >= 10:
            return EscalationLevel.EMERGENCY
        if is_critical_path or blast_radius >= 20:
            return EscalationLevel.ORGANIZATION
        if blast_radius >= 5:
            return EscalationLevel.DEPARTMENT
        return EscalationLevel.TEAM

    def build_escalation_chain(
        self,
        coordinate_id: str,
        jurisdictions: dict[str, list[Jurisdiction]],
        teams_by_id: Optional[dict[str, Team]] = None,
    ) -> list[str]:
        """Build the ordered escalation chain for a coordinate.

        The chain starts with the most local team and walks up the
        organisational hierarchy via ``parent_team_id``.

        Parameters
        ----------
        coordinate_id:
            The coordinate whose chain to build.
        jurisdictions:
            Current jurisdiction assignments.
        teams_by_id:
            Optional lookup of Team objects for hierarchy traversal.

        Returns
        -------
        list[str]
            Team IDs in escalation order (most local first).
        """
        jlist = jurisdictions.get(coordinate_id, [])
        if not jlist:
            return []

        # Start with the most authoritative team
        primary = max(
            jlist,
            key=lambda j: (
                authority_rank(j.authority),
                -j.delegation_depth,
            ),
        )
        chain = [primary.team_id]

        # Walk parent chain if teams_by_id is provided
        if teams_by_id:
            current_id = primary.team_id
            seen: set[str] = {current_id}
            for _ in range(10):  # guard against cycles
                team = teams_by_id.get(current_id)
                if team is None or team.parent_team_id is None:
                    break
                parent_id = team.parent_team_id
                if parent_id in seen:
                    break
                chain.append(parent_id)
                seen.add(parent_id)
                current_id = parent_id

        return chain

    def escalate(
        self,
        escalation: ObstructionEscalation,
        to_level: EscalationLevel,
    ) -> ObstructionEscalation:
        """Escalate an obstruction to a higher organisational level.

        Parameters
        ----------
        escalation:
            Existing escalation record.
        to_level:
            Target escalation level.

        Returns
        -------
        ObstructionEscalation
            Updated escalation with new level and status ``"ACKNOWLEDGED"``.
        """
        # Walk chain to find a team at the new level
        level_rank = {
            EscalationLevel.TEAM: 0,
            EscalationLevel.DEPARTMENT: 1,
            EscalationLevel.ORGANIZATION: 2,
            EscalationLevel.EMERGENCY: 3,
        }
        target_rank = level_rank[to_level]
        chain = escalation.escalation_chain
        # Pick the team at position matching the level rank (or last in chain)
        idx = min(target_rank, len(chain) - 1) if chain else 0
        new_responsible = chain[idx] if chain else escalation.responsible_team

        return ObstructionEscalation(
            obstruction_id=escalation.obstruction_id,
            coordinate_id=escalation.coordinate_id,
            blast_radius=escalation.blast_radius,
            escalation_level=to_level,
            responsible_team=new_responsible,
            escalation_chain=list(escalation.escalation_chain),
            status="ACKNOWLEDGED",
            created_at=escalation.created_at,
            acknowledged_at=_iso_now(),
            resolved_at=escalation.resolved_at,
        )

    def auto_assign(
        self,
        escalation: ObstructionEscalation,
        team_workload: dict[str, int],
    ) -> str:
        """Assign the escalation to the least-loaded team in the chain.

        Parameters
        ----------
        escalation:
            Escalation record containing the candidate chain.
        team_workload:
            Mapping from team ID to current open obstruction count.

        Returns
        -------
        str
            ID of the team assigned to handle the obstruction.
        """
        if not escalation.escalation_chain:
            return escalation.responsible_team
        best = min(
            escalation.escalation_chain,
            key=lambda t: team_workload.get(t, 0),
        )
        return best


# ---------------------------------------------------------------------------
# TreatyNegotiator
# ---------------------------------------------------------------------------


class TreatyNegotiator:
    """Negotiate and maintain ``CrossTeamTreaty`` objects at overlapping coords.

    Treaties formalise the shared propositions that must hold at coordinates
    owned by more than one team — the gluing data for the authority sheaf.
    """

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def propose_treaty(
        self,
        team_a: Team,
        team_b: Team,
        overlap_coords: list[str],
        morphisms: Optional[list[dict[str, Any]]] = None,
        sections: Optional[list[dict[str, Any]]] = None,
    ) -> CrossTeamTreaty:
        """Propose a new ``CrossTeamTreaty`` between two teams.

        Parameters
        ----------
        team_a:
            First team.
        team_b:
            Second team.
        overlap_coords:
            Coordinate IDs in the shared sub-cover.
        morphisms:
            Optional list of morphism dicts for proposition extraction.
        sections:
            Optional list of section dicts used for trust floor suggestion.

        Returns
        -------
        CrossTeamTreaty
            A new treaty in ``PENDING`` status.
        """
        morphisms = morphisms or []
        sections = sections or []
        propositions = self._extract_interface_propositions(
            overlap_coords, sections
        )
        trust_floor = self._suggest_trust_floor(overlap_coords, sections)
        # Determine review policy based on authority levels
        if (
            team_a.authority_level == AuthorityLevel.FULL
            and team_b.authority_level == AuthorityLevel.FULL
        ):
            policy = "dual approval"
        elif team_a.authority_level == AuthorityLevel.FULL:
            policy = f"{team_a.id} primary, {team_b.id} review"
        else:
            policy = f"{team_b.id} primary, {team_a.id} review"

        return CrossTeamTreaty(
            treaty_id=uuid.uuid4().hex[:16],
            team_a=team_a.id,
            team_b=team_b.id,
            overlap_coordinates=list(overlap_coords),
            agreed_propositions=propositions,
            trust_floor=trust_floor,
            review_policy=policy,
            last_negotiated=_iso_now(),
            status="PENDING",
        )

    def _extract_interface_propositions(
        self,
        overlap_coords: list[str],
        sections: list[dict[str, Any]],
    ) -> list[str]:
        """Extract propositions that must hold at the overlap coordinates.

        For each overlap coordinate, we look for sections whose
        ``coordinate_id`` matches and collect their ``proposition`` fields.
        If no sections are provided, we generate default propositions.

        Parameters
        ----------
        overlap_coords:
            Coordinate IDs at the overlap.
        sections:
            Evidence section dicts (may have ``coordinate_id`` and
            ``proposition`` keys).

        Returns
        -------
        list[str]
            Deduplicated list of propositions.
        """
        seen: set[str] = set()
        propositions: list[str] = []
        coord_set = set(overlap_coords)

        for section in sections:
            coord = section.get("coordinate_id", "")
            prop = section.get("proposition", "")
            if coord in coord_set and prop and prop not in seen:
                propositions.append(prop)
                seen.add(prop)

        # Default propositions for any coord not covered by sections
        covered = {s.get("coordinate_id") for s in sections}
        for coord in overlap_coords:
            if coord not in covered:
                default = f"interface compatibility holds at {coord}"
                if default not in seen:
                    propositions.append(default)
                    seen.add(default)

        return propositions

    def _suggest_trust_floor(
        self,
        overlap_coords: list[str],
        evidence: list[dict[str, Any]],
    ) -> str:
        """Suggest a minimum acceptable trust level for the overlap.

        Uses the minimum trust level found in existing evidence at the
        overlap coordinates.  Defaults to ``"heuristic"`` when no evidence
        is present.

        Parameters
        ----------
        overlap_coords:
            Coordinate IDs at the overlap.
        evidence:
            Evidence dicts (may have ``coordinate_id`` and ``trust_level``).

        Returns
        -------
        str
            Suggested trust floor string.
        """
        coord_set = set(overlap_coords)
        levels: list[str] = []
        for ev in evidence:
            if ev.get("coordinate_id") in coord_set:
                lvl = ev.get("trust_level", "none")
                levels.append(lvl)
        if not levels:
            return "heuristic"

        from jugeo.se_theory.teams.algorithms import TRUST_ORDER, trust_rank  # noqa: PLC0415

        min_level = min(levels, key=trust_rank)
        # Suggest one level above the minimum
        rank = trust_rank(min_level)
        suggested_rank = min(rank + 1, len(TRUST_ORDER) - 1)
        return TRUST_ORDER[suggested_rank]

    def check_treaty_compliance(
        self,
        treaty: CrossTeamTreaty,
        sections: list[dict[str, Any]],
    ) -> list[str]:
        """Return a list of violation descriptions for a treaty.

        A violation occurs when an agreed proposition is not supported by
        any section at the relevant overlap coordinates.

        Parameters
        ----------
        treaty:
            The treaty to check.
        sections:
            Current evidence sections at the site.

        Returns
        -------
        list[str]
            Human-readable violation descriptions.  Empty list = compliant.
        """
        coord_set = set(treaty.overlap_coordinates)
        # Index propositions that are evidenced at overlap coords
        evidenced_props: set[str] = set()
        for section in sections:
            if section.get("coordinate_id") in coord_set:
                prop = section.get("proposition", "")
                if prop:
                    evidenced_props.add(prop)

        violations: list[str] = []
        for prop in treaty.agreed_propositions:
            if prop not in evidenced_props:
                violations.append(
                    f"Agreed proposition not evidenced: '{prop}'"
                )

        # Check trust floor
        for section in sections:
            if section.get("coordinate_id") not in coord_set:
                continue
            lvl = section.get("trust_level", "none")
            if trust_rank(lvl) < trust_rank(treaty.trust_floor):
                violations.append(
                    f"Evidence at {section['coordinate_id']} has trust "
                    f"'{lvl}' below treaty floor '{treaty.trust_floor}'"
                )

        return violations

    def renegotiate(
        self,
        treaty: CrossTeamTreaty,
        changed_coords: list[str],
        sections: Optional[list[dict[str, Any]]] = None,
    ) -> CrossTeamTreaty:
        """Update a treaty after coordinates have changed.

        Parameters
        ----------
        treaty:
            Existing treaty to renegotiate.
        changed_coords:
            Coordinate IDs that have changed.
        sections:
            Updated evidence sections.

        Returns
        -------
        CrossTeamTreaty
            Updated treaty (same IDs, fresh propositions and timestamp).
        """
        sections = sections or []
        # Update overlap to add new coords if not already present
        new_overlap = list(
            dict.fromkeys(treaty.overlap_coordinates + changed_coords)
        )
        new_props = self._extract_interface_propositions(new_overlap, sections)
        new_floor = self._suggest_trust_floor(new_overlap, sections)
        return CrossTeamTreaty(
            treaty_id=treaty.treaty_id,
            team_a=treaty.team_a,
            team_b=treaty.team_b,
            overlap_coordinates=new_overlap,
            agreed_propositions=new_props,
            trust_floor=new_floor,
            review_policy=treaty.review_policy,
            last_negotiated=_iso_now(),
            status="PENDING",
        )

    def expire_stale_treaties(
        self,
        treaties: list[CrossTeamTreaty],
        threshold_days: int = 90,
    ) -> list[str]:
        """Return treaty IDs that have not been renegotiated recently.

        Parameters
        ----------
        treaties:
            List of all known treaties.
        threshold_days:
            A treaty is considered stale if ``last_negotiated`` is older than
            this many days.

        Returns
        -------
        list[str]
            IDs of stale treaties that need renewal.
        """
        stale: list[str] = []
        now = time.time()
        threshold_s = threshold_days * 86400
        for treaty in treaties:
            try:
                ts = datetime.strptime(
                    treaty.last_negotiated, "%Y-%m-%dT%H:%M:%SZ"
                ).replace(tzinfo=timezone.utc)
                age_s = now - ts.timestamp()
                if age_s > threshold_s:
                    stale.append(treaty.treaty_id)
            except (ValueError, AttributeError):
                stale.append(treaty.treaty_id)
        return stale


# ---------------------------------------------------------------------------
# JurisdictionReporter
# ---------------------------------------------------------------------------


class JurisdictionReporter:
    """Generate high-level reports on the state of the authority sheaf."""

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def generate_report(
        self,
        teams: list[Team],
        jurisdictions: dict[str, list[Jurisdiction]],
        obstructions: Optional[list[ObstructionEscalation]] = None,
        treaties: Optional[list[CrossTeamTreaty]] = None,
    ) -> JurisdictionReport:
        """Produce a ``JurisdictionReport`` summarising the jurisdiction state.

        Parameters
        ----------
        teams:
            All known teams.
        jurisdictions:
            Output of :meth:`JurisdictionManager.assign_jurisdictions`.
        obstructions:
            Optional list of open obstruction escalations.
        treaties:
            Optional list of all treaties.

        Returns
        -------
        JurisdictionReport
        """
        obstructions = obstructions or []
        treaties = treaties or []

        total = len(jurisdictions)
        covered = sum(1 for jlist in jurisdictions.values() if jlist)
        uncovered = total - covered

        mgr = JurisdictionManager()
        raw_conflicts = mgr.detect_conflicts(jurisdictions)
        conflict_coords = [c for c, _ in raw_conflicts]

        open_escalations = [
            e for e in obstructions if e.status not in ("RESOLVED",)
        ]
        pending_treaties = [
            t for t in treaties if t.status in ("PENDING", "DISPUTED")
        ]

        return JurisdictionReport(
            total_coordinates=total,
            covered_by_team=covered,
            uncovered=uncovered,
            teams=list(teams),
            overlap_conflicts=conflict_coords,
            escalation_queue=open_escalations,
            pending_treaties=pending_treaties,
        )

    def coverage_stats(
        self,
        jurisdictions: dict[str, list[Jurisdiction]],
        total_coords: Optional[int] = None,
    ) -> dict[str, Any]:
        """Compute coverage statistics for the jurisdiction map.

        Parameters
        ----------
        jurisdictions:
            Output of :meth:`JurisdictionManager.assign_jurisdictions`.
        total_coords:
            Total coordinate count; defaults to ``len(jurisdictions)``.

        Returns
        -------
        dict
            Keys: ``total``, ``covered``, ``uncovered``,
            ``coverage_pct``, ``contested``.
        """
        total = total_coords if total_coords is not None else len(jurisdictions)
        covered = sum(1 for jlist in jurisdictions.values() if jlist)
        uncovered = total - covered
        contested = sum(
            1 for jlist in jurisdictions.values() if len(jlist) > 1
        )
        coverage_pct = (covered / total * 100) if total else 0.0
        return {
            "total": total,
            "covered": covered,
            "uncovered": uncovered,
            "coverage_pct": round(coverage_pct, 2),
            "contested": contested,
        }

    def team_workload(
        self,
        teams: list[Team],
        obstructions: list[ObstructionEscalation],
    ) -> dict[str, int]:
        """Count open obstructions assigned to each team.

        Parameters
        ----------
        teams:
            All known teams.
        obstructions:
            Open obstruction escalations.

        Returns
        -------
        dict[str, int]
            Mapping from team ID to open obstruction count.
        """
        workload: dict[str, int] = {t.id: 0 for t in teams}
        for obs in obstructions:
            if obs.status != "RESOLVED":
                tid = obs.responsible_team
                if tid in workload:
                    workload[tid] += 1
                else:
                    workload[tid] = 1
        return workload
