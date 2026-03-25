"""Teams / Jurisdiction module for jugeo.se_theory (B9).

Theory (JuGeo — "Teams as Sheaves of Authority", B9):
    Jurisdiction maps to a presheaf of authority over the coordinate site.
    Each team owns a sub-cover of the site; delegations compose via restriction
    maps; escalation follows the sheaf gluing diagram upward through the
    organisational hierarchy.

    * Team             ↔ local section of the authority sheaf
    * Jurisdiction     ↔ restriction of authority to a sub-pattern
    * AuthorityGrant   ↔ explicit restriction morphism between team sheaves
    * CrossTeamTreaty  ↔ agreement on shared sections at overlap coordinates
    * ObstructionEscalation ↔ failed gluing that must be resolved upstream
"""
from __future__ import annotations

from jugeo.se_theory.teams.models import (
    AuthorityGrant,
    AuthorityLevel,
    CodeownersEntry,
    CodeownersMapping,
    CrossTeamTreaty,
    EscalationLevel,
    JurisdictionReport,
    Jurisdiction,
    ObstructionEscalation,
    Team,
    TeamRole,
)
from jugeo.se_theory.teams.algorithms import (
    CodeownersParser,
    EscalationRouter,
    JurisdictionManager,
    JurisdictionReporter,
    TreatyNegotiator,
)
from jugeo.se_theory.teams.integration import (
    AuthorityIntegrator,
    CodeownersIntegrator,
)

__all__ = [
    # models
    "Team",
    "TeamRole",
    "AuthorityLevel",
    "EscalationLevel",
    "Jurisdiction",
    "CodeownersEntry",
    "CodeownersMapping",
    "AuthorityGrant",
    "ObstructionEscalation",
    "CrossTeamTreaty",
    "JurisdictionReport",
    # algorithms
    "CodeownersParser",
    "JurisdictionManager",
    "EscalationRouter",
    "TreatyNegotiator",
    "JurisdictionReporter",
    # integration
    "CodeownersIntegrator",
    "AuthorityIntegrator",
]
