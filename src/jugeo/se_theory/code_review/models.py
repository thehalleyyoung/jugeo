"""Code review models: judgment-geometry verification for pull requests.

In the judgment-geometry framework a *code review* is a verification pass
over a proposed morphism of the site.  Each review check corresponds to a
geometric condition:

* Internal consistency — local section well-formedness.
* Overlap compatibility — descent on overlaps is preserved.
* Trust adequacy — trust levels meet thresholds required by dependents.
* Public honesty — publicly exported claims are backed by internal evidence.
* Treaty compliance — cross-boundary agreements remain satisfied.
* Boundary respect — no coordinate crosses its site boundary improperly.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

__all__ = [
    # Enums
    "ReviewCheck",
    # Dataclasses
    "ReviewScope",
    "ReviewFinding",
    "ReviewVerdict",
    "TreatyImpact",
]


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class ReviewCheck(str, Enum):
    """Categories of review checks, each corresponding to a geometric condition."""

    INTERNAL_CONSISTENCY = "internal_consistency"
    OVERLAP_COMPATIBILITY = "overlap_compatibility"
    TRUST_ADEQUACY = "trust_adequacy"
    PUBLIC_HONESTY = "public_honesty"
    TREATY_COMPLIANCE = "treaty_compliance"
    BOUNDARY_RESPECT = "boundary_respect"


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class ReviewScope:
    """Describes the scope of a pull-request review in judgment-geometry terms."""

    pr_id: str = ""
    changed_coordinates: list[str] = field(default_factory=list)
    affected_overlaps: list[str] = field(default_factory=list)
    affected_treaties: list[str] = field(default_factory=list)
    affected_teams: list[str] = field(default_factory=list)
    trust_changes: dict[str, tuple[str, str]] = field(default_factory=dict)
    public_projection_changes: list[str] = field(default_factory=list)

    # -- serialisation -------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "pr_id": self.pr_id,
            "changed_coordinates": list(self.changed_coordinates),
            "affected_overlaps": list(self.affected_overlaps),
            "affected_treaties": list(self.affected_treaties),
            "affected_teams": list(self.affected_teams),
            "trust_changes": {
                k: list(v) for k, v in self.trust_changes.items()
            },
            "public_projection_changes": list(self.public_projection_changes),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ReviewScope:
        raw_trust = data.get("trust_changes", {})
        trust_changes: dict[str, tuple[str, str]] = {
            k: (v[0], v[1]) for k, v in raw_trust.items()
        }
        return cls(
            pr_id=data.get("pr_id", ""),
            changed_coordinates=list(data.get("changed_coordinates", [])),
            affected_overlaps=list(data.get("affected_overlaps", [])),
            affected_treaties=list(data.get("affected_treaties", [])),
            affected_teams=list(data.get("affected_teams", [])),
            trust_changes=trust_changes,
            public_projection_changes=list(
                data.get("public_projection_changes", [])
            ),
        )


@dataclass
class ReviewFinding:
    """A single finding produced by a review check."""

    check: ReviewCheck = ReviewCheck.INTERNAL_CONSISTENCY
    coordinate_id: str = ""
    severity: str = "warning"
    description: str = ""
    suggestion: Optional[str] = None

    # -- serialisation -------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "check": self.check.value,
            "coordinate_id": self.coordinate_id,
            "severity": self.severity,
            "description": self.description,
            "suggestion": self.suggestion,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ReviewFinding:
        return cls(
            check=ReviewCheck(data["check"]),
            coordinate_id=data.get("coordinate_id", ""),
            severity=data.get("severity", "warning"),
            description=data.get("description", ""),
            suggestion=data.get("suggestion"),
        )


@dataclass
class ReviewVerdict:
    """Aggregated outcome of all review checks on a pull request."""

    pr_id: str = ""
    findings: list[ReviewFinding] = field(default_factory=list)
    pass_count: int = 0
    fail_count: int = 0
    warning_count: int = 0
    overall: str = "APPROVE"
    required_reviewers: list[str] = field(default_factory=list)
    trust_adequate: bool = True
    descent_preserved: bool = True

    # -- serialisation -------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "pr_id": self.pr_id,
            "findings": [f.to_dict() for f in self.findings],
            "pass_count": self.pass_count,
            "fail_count": self.fail_count,
            "warning_count": self.warning_count,
            "overall": self.overall,
            "required_reviewers": list(self.required_reviewers),
            "trust_adequate": self.trust_adequate,
            "descent_preserved": self.descent_preserved,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ReviewVerdict:
        return cls(
            pr_id=data.get("pr_id", ""),
            findings=[
                ReviewFinding.from_dict(f) for f in data.get("findings", [])
            ],
            pass_count=data.get("pass_count", 0),
            fail_count=data.get("fail_count", 0),
            warning_count=data.get("warning_count", 0),
            overall=data.get("overall", "APPROVE"),
            required_reviewers=list(data.get("required_reviewers", [])),
            trust_adequate=data.get("trust_adequate", True),
            descent_preserved=data.get("descent_preserved", True),
        )


@dataclass
class TreatyImpact:
    """Describes the impact of a change on a cross-boundary treaty."""

    treaty_id: str = ""
    parties: list[str] = field(default_factory=list)
    change_description: str = ""
    renegotiation_needed: bool = False
    proposed_amendment: Optional[str] = None

    # -- serialisation -------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "treaty_id": self.treaty_id,
            "parties": list(self.parties),
            "change_description": self.change_description,
            "renegotiation_needed": self.renegotiation_needed,
            "proposed_amendment": self.proposed_amendment,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TreatyImpact:
        return cls(
            treaty_id=data.get("treaty_id", ""),
            parties=list(data.get("parties", [])),
            change_description=data.get("change_description", ""),
            renegotiation_needed=data.get("renegotiation_needed", False),
            proposed_amendment=data.get("proposed_amendment"),
        )
