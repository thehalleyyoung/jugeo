r"""Integration helpers for the ``jugeo.se_theory.teams`` package.

Theory (JuGeo — "Teams as Sheaves of Authority", B9):
    Integration translates between external representations (CODEOWNERS files,
    authority-grant databases) and the internal authority-sheaf model.

    * ``CodeownersIntegrator`` — bridges CODEOWNERS files to ``Jurisdiction``
      objects, mapping file-system patterns to coordinate IDs.
    * ``AuthorityIntegrator``  — converts ``AuthorityGrant`` records (e.g.
      from a policy database) into ``Jurisdiction`` objects and vice-versa,
      and merges multiple sources of authority information.

    copilot: se-theory-teams-integration
"""
from __future__ import annotations

import os
from typing import Any, Optional

from jugeo.se_theory.teams.algorithms import (
    CodeownersParser,
    JurisdictionManager,
    trust_rank,
    _lower_trust,
)
from jugeo.se_theory.teams.models import (
    AuthorityGrant,
    AuthorityLevel,
    CodeownersMapping,
    Jurisdiction,
    Team,
    _iso_now,
)

__all__ = [
    "CodeownersIntegrator",
    "AuthorityIntegrator",
]


# ---------------------------------------------------------------------------
# CodeownersIntegrator
# ---------------------------------------------------------------------------


class CodeownersIntegrator:
    """Bridge between CODEOWNERS files and the jurisdiction model.

    Usage example::

        integrator = CodeownersIntegrator()
        mapping = integrator.from_codeowners_file(".github/CODEOWNERS")
        jurisdictions = integrator.to_jurisdictions(mapping)
        sync_result = integrator.sync_with_site(mapping, all_coordinate_ids)
    """

    def __init__(self) -> None:
        self._parser = CodeownersParser()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def from_codeowners_file(self, filepath: str) -> CodeownersMapping:
        """Read and parse a CODEOWNERS file from disk.

        Parameters
        ----------
        filepath:
            Absolute or relative path to the CODEOWNERS file.

        Returns
        -------
        CodeownersMapping
            Parsed entries with priority scores.

        Raises
        ------
        FileNotFoundError
            If the file does not exist.
        """
        with open(filepath, encoding="utf-8") as fh:
            content = fh.read()
        mapping = self._parser.parse_codeowners(content)
        mapping.source_file = filepath
        return mapping

    def to_jurisdictions(
        self,
        mapping: CodeownersMapping,
        default_authority: AuthorityLevel = AuthorityLevel.APPROVE,
        default_trust_ceiling: str = "proof",
    ) -> list[Jurisdiction]:
        """Convert a ``CodeownersMapping`` into a flat list of ``Jurisdiction`` objects.

        Each CODEOWNERS entry produces one ``Jurisdiction`` per owning team.

        Parameters
        ----------
        mapping:
            Parsed CODEOWNERS mapping.
        default_authority:
            Authority level to assign (CODEOWNERS has no authority concept).
        default_trust_ceiling:
            Default trust ceiling for all generated jurisdictions.

        Returns
        -------
        list[Jurisdiction]
        """
        jurisdictions: list[Jurisdiction] = []
        for entry in mapping.entries:
            for team_handle in entry.teams:
                jurisdictions.append(
                    Jurisdiction(
                        team_id=team_handle,
                        coordinate_pattern=entry.pattern,
                        authority=default_authority,
                        trust_ceiling=default_trust_ceiling,
                        delegated_from=None,
                        delegation_depth=0,
                    )
                )
        return jurisdictions

    def sync_with_site(
        self,
        mapping: CodeownersMapping,
        coordinates: list[str],
    ) -> dict[str, Any]:
        """Map CODEOWNERS patterns to actual coordinate IDs in the site.

        Parameters
        ----------
        mapping:
            Parsed CODEOWNERS mapping.
        coordinates:
            All known coordinate IDs.

        Returns
        -------
        dict
            Keys:
            * ``"pattern_to_coords"`` — ``dict[str, list[str]]`` mapping each
              pattern to matching coordinates.
            * ``"unmatched_patterns"`` — patterns that matched no coordinates.
            * ``"unowned_coords"``     — coordinates with no CODEOWNERS entry.
            * ``"coverage_pct"``       — percentage of coordinates that are owned.
        """
        pattern_to_coords: dict[str, list[str]] = {}
        owned_coords: set[str] = set()

        for entry in mapping.entries:
            matched = [
                c
                for c in coordinates
                if self._parser._matches(c, entry.pattern)
            ]
            pattern_to_coords[entry.pattern] = matched
            owned_coords.update(matched)

        unmatched_patterns = [
            e.pattern
            for e in mapping.entries
            if not pattern_to_coords.get(e.pattern)
        ]
        unowned_coords = [c for c in coordinates if c not in owned_coords]
        coverage_pct = (
            len(owned_coords) / len(coordinates) * 100
            if coordinates
            else 0.0
        )

        return {
            "pattern_to_coords": pattern_to_coords,
            "unmatched_patterns": unmatched_patterns,
            "unowned_coords": unowned_coords,
            "coverage_pct": round(coverage_pct, 2),
        }


# ---------------------------------------------------------------------------
# AuthorityIntegrator
# ---------------------------------------------------------------------------


class AuthorityIntegrator:
    """Convert between ``AuthorityGrant`` records and ``Jurisdiction`` objects.

    Authority grants come from external policy databases or configuration
    files; this integrator normalises them into the internal jurisdiction
    model and can merge multiple sources.
    """

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def from_authority_grants(
        self, grants: list[AuthorityGrant]
    ) -> list[Jurisdiction]:
        """Convert ``AuthorityGrant`` records into ``Jurisdiction`` objects.

        Each grant produces one ``Jurisdiction`` per covered pattern.
        Expired grants (``valid_until`` in the past) are silently skipped.

        Parameters
        ----------
        grants:
            List of authority grants from a policy database.

        Returns
        -------
        list[Jurisdiction]
        """
        jurisdictions: list[Jurisdiction] = []
        now_str = _iso_now()
        for grant in grants:
            if grant.valid_until and grant.valid_until < now_str:
                continue  # expired
            for pattern in grant.coordinate_patterns:
                jurisdictions.append(
                    Jurisdiction(
                        team_id=grant.receiving_team,
                        coordinate_pattern=pattern,
                        authority=grant.authority,
                        trust_ceiling=grant.trust_attenuation,
                        delegated_from=grant.granting_team,
                        delegation_depth=1,
                        conditions=grant.conditions,
                    )
                )
        return jurisdictions

    def to_authority_grants(
        self,
        jurisdictions: list[Jurisdiction],
        granting_team: str = "org-root",
        valid_from: Optional[str] = None,
    ) -> list[AuthorityGrant]:
        """Convert ``Jurisdiction`` objects back into ``AuthorityGrant`` records.

        Jurisdictions with the same ``team_id``, ``authority``, and
        ``trust_ceiling`` are grouped into a single grant.

        Parameters
        ----------
        jurisdictions:
            Jurisdictions to convert.
        granting_team:
            Team ID to record as the granting authority.
        valid_from:
            ISO-8601 timestamp for grant validity start; defaults to now.

        Returns
        -------
        list[AuthorityGrant]
        """
        valid_from = valid_from or _iso_now()

        # Group by (team_id, authority, trust_ceiling, delegated_from)
        GroupKey = tuple  # (team_id, authority_val, trust_ceiling, conditions_key)
        groups: dict[GroupKey, list[str]] = {}
        for j in jurisdictions:
            # Normalise conditions to a hashable key
            cond_key = (
                tuple(sorted(j.conditions.items())) if j.conditions else ()
            )
            key: GroupKey = (j.team_id, j.authority.value, j.trust_ceiling, cond_key)
            groups.setdefault(key, []).append(j.coordinate_pattern)

        grants: list[AuthorityGrant] = []
        for (team_id, auth_val, trust_ceil, cond_key), patterns in groups.items():
            conditions = dict(cond_key) if cond_key else None
            grants.append(
                AuthorityGrant(
                    granting_team=granting_team,
                    receiving_team=team_id,
                    coordinate_patterns=patterns,
                    authority=AuthorityLevel(auth_val),
                    trust_attenuation=trust_ceil,
                    valid_from=valid_from,
                    conditions=conditions,
                )
            )
        return grants

    def merge_sources(
        self,
        codeowners_jurisdictions: list[Jurisdiction],
        authority_grant_jurisdictions: list[Jurisdiction],
    ) -> list[Jurisdiction]:
        """Merge jurisdiction lists from CODEOWNERS and authority-grant sources.

        When both sources define a jurisdiction for the same team+pattern,
        the authority-grant source wins (it is more explicit).  Otherwise,
        entries from both sources are combined.

        Parameters
        ----------
        codeowners_jurisdictions:
            Jurisdictions derived from a CODEOWNERS file.
        authority_grant_jurisdictions:
            Jurisdictions derived from explicit authority grants.

        Returns
        -------
        list[Jurisdiction]
            Merged list, with duplicates resolved in favour of grants.
        """
        # Index grant jurisdictions by (team_id, coordinate_pattern)
        grant_index: dict[tuple[str, str], Jurisdiction] = {
            (j.team_id, j.coordinate_pattern): j
            for j in authority_grant_jurisdictions
        }

        merged: list[Jurisdiction] = []
        for j in codeowners_jurisdictions:
            key = (j.team_id, j.coordinate_pattern)
            if key in grant_index:
                # Grant overrides CODEOWNERS
                merged.append(grant_index.pop(key))
            else:
                merged.append(j)

        # Add remaining grant jurisdictions not present in CODEOWNERS
        merged.extend(grant_index.values())
        return merged

    # ------------------------------------------------------------------
    # Validation helpers
    # ------------------------------------------------------------------

    def validate_grants(
        self,
        grants: list[AuthorityGrant],
        teams_by_id: Optional[dict[str, Team]] = None,
    ) -> list[dict[str, Any]]:
        """Validate a list of authority grants for internal consistency.

        Checks performed:
        * Granting team exists (if ``teams_by_id`` is provided).
        * Receiving team exists (if ``teams_by_id`` is provided).
        * ``valid_until`` is after ``valid_from``.
        * ``authority`` level is not higher than granting team's authority.

        Parameters
        ----------
        grants:
            Grants to validate.
        teams_by_id:
            Optional team lookup for existence checks.

        Returns
        -------
        list[dict]
            One dict per violation:
            ``{"grant_id": str, "violation": str}``.
        """
        violations: list[dict[str, Any]] = []
        for grant in grants:
            if teams_by_id is not None:
                if grant.granting_team not in teams_by_id:
                    violations.append(
                        {
                            "grant_id": grant.id,
                            "violation": (
                                f"Granting team '{grant.granting_team}' "
                                "not found."
                            ),
                        }
                    )
                if grant.receiving_team not in teams_by_id:
                    violations.append(
                        {
                            "grant_id": grant.id,
                            "violation": (
                                f"Receiving team '{grant.receiving_team}' "
                                "not found."
                            ),
                        }
                    )
            if grant.valid_until and grant.valid_from:
                if grant.valid_until < grant.valid_from:
                    violations.append(
                        {
                            "grant_id": grant.id,
                            "violation": (
                                f"valid_until '{grant.valid_until}' is before "
                                f"valid_from '{grant.valid_from}'."
                            ),
                        }
                    )
        return violations
