"""Data models for trust boundaries, transports, policies, and reports.

These dataclasses encode the lattice of trust levels used across the
web-application verification pipeline.  Every level in ``TRUST_ORDER``
corresponds to a concrete verification technique – from raw user input
at the bottom to mechanically-verified proofs at the top.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

# ── trust-level lattice (ascending order) ──────────────────────────────
TRUST_ORDER: list[str] = [
    "USER_INPUT",
    "BROWSER_TESTED",
    "CSS_LINTED",
    "CLIENT_VALIDATED",
    "JS_TYPE_CHECKED",
    "TEMPLATE_TYPE_CHECKED",
    "SCHEMA_VALIDATED",
    "API_CONTRACT_TESTED",
    "ORM_TYPE_CHECKED",
    "MIDDLEWARE_ENFORCED",
    "SERVER_VALIDATED",
    "DB_CONSTRAINT_ENFORCED",
    "SOLVER_DISCHARGED",
    "MECHANICALLY_VERIFIED",
]

_TRUST_INDEX: dict[str, int] = {lvl: i for i, lvl in enumerate(TRUST_ORDER)}


def trust_index(level: str) -> int:
    """Return the numeric index for *level* inside ``TRUST_ORDER``.

    Raises ``ValueError`` when the level is unknown.
    """
    if level not in _TRUST_INDEX:
        raise ValueError(
            f"Unknown trust level {level!r}. "
            f"Valid levels: {', '.join(TRUST_ORDER)}"
        )
    return _TRUST_INDEX[level]


def trust_ge(a: str, b: str) -> bool:
    """Return ``True`` when trust level *a* ≥ *b*."""
    return trust_index(a) >= trust_index(b)


# ── dataclasses ────────────────────────────────────────────────────────

@dataclass
class TrustBoundary:
    """A boundary between two layers of the web stack.

    When data crosses a boundary that has ``requires_revalidation`` set,
    trust evidence gathered on the source side is demoted.
    """

    name: str
    source_layers: list[str]
    target_layers: list[str]
    boundary_type: str
    requires_revalidation: bool = True
    description: str = ""

    # -- serialisation ---------------------------------------------------

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "source_layers": list(self.source_layers),
            "target_layers": list(self.target_layers),
            "boundary_type": self.boundary_type,
            "requires_revalidation": self.requires_revalidation,
            "description": self.description,
        }

    @classmethod
    def from_dict(cls, d: dict) -> TrustBoundary:
        return cls(
            name=d["name"],
            source_layers=list(d["source_layers"]),
            target_layers=list(d["target_layers"]),
            boundary_type=d["boundary_type"],
            requires_revalidation=d.get("requires_revalidation", True),
            description=d.get("description", ""),
        )


@dataclass
class TrustTransport:
    """Records how a single morphism changes trust level.

    ``trust_change`` is signed: negative values denote a demotion and
    positive values a promotion.  ``valid`` indicates whether the
    transport respects the trust policy in effect.
    """

    morphism_kind: str
    source_trust: str
    target_trust: str
    trust_change: int
    valid: bool
    reason: str = ""

    # -- serialisation ---------------------------------------------------

    def to_dict(self) -> dict:
        return {
            "morphism_kind": self.morphism_kind,
            "source_trust": self.source_trust,
            "target_trust": self.target_trust,
            "trust_change": self.trust_change,
            "valid": self.valid,
            "reason": self.reason,
        }

    @classmethod
    def from_dict(cls, d: dict) -> TrustTransport:
        return cls(
            morphism_kind=d["morphism_kind"],
            source_trust=d["source_trust"],
            target_trust=d["target_trust"],
            trust_change=d["trust_change"],
            valid=d["valid"],
            reason=d.get("reason", ""),
        )


@dataclass
class TrustRule:
    """A single rule inside a :class:`TrustPolicy`.

    ``condition`` is a human-readable predicate string such as
    ``"crosses_client_server_boundary"`` that the policy engine
    evaluates against each transport step.
    """

    condition: str
    action: str  # "allow" | "deny" | "demote" | "require_revalidation"
    trust_floor: str = "USER_INPUT"
    trust_ceiling: str = "MECHANICALLY_VERIFIED"
    description: str = ""

    # -- serialisation ---------------------------------------------------

    def to_dict(self) -> dict:
        return {
            "condition": self.condition,
            "action": self.action,
            "trust_floor": self.trust_floor,
            "trust_ceiling": self.trust_ceiling,
            "description": self.description,
        }

    @classmethod
    def from_dict(cls, d: dict) -> TrustRule:
        return cls(
            condition=d["condition"],
            action=d["action"],
            trust_floor=d.get("trust_floor", "USER_INPUT"),
            trust_ceiling=d.get("trust_ceiling", "MECHANICALLY_VERIFIED"),
            description=d.get("description", ""),
        )


@dataclass
class TrustPolicy:
    """A named collection of :class:`TrustRule` instances.

    Evaluated top-to-bottom by :class:`TrustPolicyEngine`; the first
    matching rule wins.  ``default_action`` is used when no rule matches.
    """

    name: str
    rules: list[TrustRule] = field(default_factory=list)
    default_action: str = "allow"
    description: str = ""

    # -- serialisation ---------------------------------------------------

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "rules": [r.to_dict() for r in self.rules],
            "default_action": self.default_action,
            "description": self.description,
        }

    @classmethod
    def from_dict(cls, d: dict) -> TrustPolicy:
        return cls(
            name=d["name"],
            rules=[TrustRule.from_dict(r) for r in d.get("rules", [])],
            default_action=d.get("default_action", "allow"),
            description=d.get("description", ""),
        )


@dataclass
class TrustReport:
    """Aggregate result of validating one or more trust chains.

    ``violations`` is a list of dicts each carrying ``location``,
    ``violation``, and ``severity`` keys.
    """

    boundaries: list[TrustBoundary] = field(default_factory=list)
    violations: list[dict] = field(default_factory=list)
    transports: list[TrustTransport] = field(default_factory=list)
    policy_applied: str = ""
    overall_trust: str = "USER_INPUT"
    passed: bool = True

    # -- serialisation ---------------------------------------------------

    def to_dict(self) -> dict:
        return {
            "boundaries": [b.to_dict() for b in self.boundaries],
            "violations": list(self.violations),
            "transports": [t.to_dict() for t in self.transports],
            "policy_applied": self.policy_applied,
            "overall_trust": self.overall_trust,
            "passed": self.passed,
        }

    @classmethod
    def from_dict(cls, d: dict) -> TrustReport:
        return cls(
            boundaries=[
                TrustBoundary.from_dict(b) for b in d.get("boundaries", [])
            ],
            violations=list(d.get("violations", [])),
            transports=[
                TrustTransport.from_dict(t) for t in d.get("transports", [])
            ],
            policy_applied=d.get("policy_applied", ""),
            overall_trust=d.get("overall_trust", "USER_INPUT"),
            passed=d.get("passed", True),
        )
