"""Trust as an ordered algebra of admissible support — Theory2.tex formal core.

# copilot: foundations/formal_core §trust-ordered-algebra-admissible
# Chapter: Mathematical interlude — a more explicit formal core
# Reference: Theory2.tex §formal_core.s02 "Trust as an ordered algebra of admissible support"

Mathematical Overview
---------------------
The **trust ordered algebra** T = (E_adm, ≼, ⊕, ⊖, ↑_π, ↓_χ) is the
algebraic backbone of JuGeo's evidence-grading system.  Rather than modelling
trust as a scalar probability, JuGeo treats it as an element of a **bounded
lattice** equipped with:

* A **partial order** ≼ that reflects epistemic strength: stronger evidence
  dominates weaker evidence.
* A **meet** operation ⊓ (greatest lower bound) and a **join** ⊔ (least upper
  bound) making (E_adm, ⊓, ⊔) a lattice.
* A **conservative composition** ⊕ that returns at most the meet of two trust
  elements (no composition can silently increase trust).
* An **attenuation** ⊖ that strictly lowers trust (models transport through a
  lossy channel: trust can only decrease).
* A named **lift** ↑_π that is the only legitimate way to increase trust; it
  requires an explicit policy identifier π and a non-empty justification.
* A **challenge-lower** ↓_χ that records a successful challenge and demotes
  trust by exactly one step.

Trust Tiers
-----------
The ordered algebra is built on five trust tiers forming a **total order**:

    PROPOSAL ≺ REVIEWED ≺ VERIFIED ≺ RUNTIME_WITNESSED ≺ PROOF_BACKED

* **PROPOSAL** — the entry point for any copilot- or oracle-generated suggestion.
  No composition of PROPOSAL elements can exceed PROPOSAL (oracle ceiling axiom).
* **REVIEWED** — independently reviewed but not mechanically checked.
* **VERIFIED** — solver-discharged or statically checked.
* **RUNTIME_WITNESSED** — witnessed during a concrete execution.
* **PROOF_BACKED** — supported by a mechanically-verified proof certificate.

Admissibility
-------------
An evidence configuration e ∈ E_adm is *admissible* iff:
1. Its trust tier is at least REVIEWED.
2. It carries a non-empty justification.
3. It has not been contradicted by a successful challenge.
4. Its provenance chain is non-empty.

Theory2.tex Invariants
-----------------------
- Judgments are ALWAYS tuples (c, φ, A, E, O, B, T, Π) — never booleans.
- ⊕ is conservative (returns meet, never join).
- ↑_π requires a named policy π; there is no silent promotion.
- ↓_χ records residual evidence and strictly lowers tier.

This module provides:

- :class:`TrustTier` — five-level ordered enum (PROPOSAL … PROOF_BACKED).
- :class:`TrustElement` — immutable element of E_adm with tier + provenance.
- :class:`AdmissibleSupport` — an admissibility-validated evidence bundle.
- :class:`TrustBound` — a (floor, ceiling) pair bounding trust in a context.
- :class:`TrustOperation` — a named algebra operation with audit record.
- :class:`TrustAlgebra` — the full algebra T = (E_adm, ≼, ⊕, ⊖, ↑_π, ↓_χ).
- :class:`TrustOrderedAlgebraAdmissibleWitness` — immutable run certificate.
- :class:`TrustOrderedAlgebraAdmissibleCoordinator` — orchestrates algebra runs.
- :class:`TrustOrderedAlgebraAdmissibleAnalyzer` — analyses audit trails.

References
----------
Theory2.tex §formal_core "Trust as an ordered algebra of admissible support" —
Definitions, five operations, oracle ceiling axiom, no-silent-promotion theorem.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
import uuid
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any, Sequence

try:
    from jugeo.evidence.trust import TrustLevel, TrustProfile, TrustTier as _ExtTrustTier
    _HAS_TRUST = True
except ImportError:
    TrustLevel = None  # type: ignore[assignment,misc]
    TrustProfile = None  # type: ignore[assignment,misc]
    _ExtTrustTier = None  # type: ignore[assignment,misc]
    _HAS_TRUST = False

try:
    from jugeo.evidence.channels import EvidenceChannel, EvidenceRequest, EvidenceResponse
    _HAS_CHANNELS = True
except ImportError:
    EvidenceChannel = None  # type: ignore[assignment,misc]
    EvidenceRequest = None  # type: ignore[assignment,misc]
    EvidenceResponse = None  # type: ignore[assignment,misc]
    _HAS_CHANNELS = False

try:
    from jugeo.errors import JuGeoError, StructuredFailure  # type: ignore[import]
    _HAS_ERRORS = True
except ImportError:
    JuGeoError = Exception  # type: ignore[assignment,misc]
    StructuredFailure = None  # type: ignore[assignment,misc]
    _HAS_ERRORS = False

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Trust tier ordering (five-tier total order for this module)
# ---------------------------------------------------------------------------

_TIER_NAMES: list[str] = [
    "PROPOSAL",          # rank 0 — oracle / copilot entry point
    "REVIEWED",          # rank 1 — independently reviewed
    "VERIFIED",          # rank 2 — solver-discharged / statically checked
    "RUNTIME_WITNESSED", # rank 3 — witnessed during execution
    "PROOF_BACKED",      # rank 4 — mechanically-verified proof
]

_TIER_RANK: dict[str, int] = {name: i for i, name in enumerate(_TIER_NAMES)}
_ORACLE_CEILING = "PROPOSAL"  # §formal_core: oracle/copilot proposals never exceed this


def _rank(tier: str) -> int:
    """Return the integer rank of *tier*; defaults to 0 (PROPOSAL) if unknown."""
    return _TIER_RANK.get(tier, 0)


def _tier_at(rank: int) -> str:
    """Return the tier name at *rank*, clamped to valid range."""
    return _TIER_NAMES[max(0, min(rank, len(_TIER_NAMES) - 1))]


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class TrustTier(str, Enum):
    """Five-level trust tier forming a total order for the formal core.

    Theory2.tex §formal_core defines five tiers:

        PROPOSAL ≺ REVIEWED ≺ VERIFIED ≺ RUNTIME_WITNESSED ≺ PROOF_BACKED

    Members
    -------
    PROPOSAL:
        The entry point for all copilot and oracle suggestions.  No amount of
        composition of PROPOSAL elements can exceed PROPOSAL (oracle ceiling).
    REVIEWED:
        Evidence that has been independently reviewed but not mechanically
        checked.  Requires explicit human sign-off or corroborating test.
    VERIFIED:
        Solver-discharged or statically verified evidence.  A Z3 certificate
        or a type-checker approval raises evidence to this tier.
    RUNTIME_WITNESSED:
        Evidence collected during a concrete runtime execution (property-based
        test, fuzzing campaign, or instrumented run).
    PROOF_BACKED:
        Evidence backed by a mechanically-verified formal proof certificate
        (Lean 4, Coq, or equivalent).
    """

    PROPOSAL = "PROPOSAL"
    REVIEWED = "REVIEWED"
    VERIFIED = "VERIFIED"
    RUNTIME_WITNESSED = "RUNTIME_WITNESSED"
    PROOF_BACKED = "PROOF_BACKED"

    @property
    def rank(self) -> int:
        """Return the integer rank of this tier."""
        return _TIER_RANK.get(self.value, 0)

    def dominates(self, other: TrustTier) -> bool:
        """Return True iff ``self`` ≽ ``other`` (self is at least as strong)."""
        return self.rank >= other.rank

    def meet(self, other: TrustTier) -> TrustTier:
        """Return the meet (greatest lower bound) ⊓ of two tiers.

        Parameters
        ----------
        other:
            The other tier.

        Returns
        -------
        TrustTier
            The weaker of the two tiers (the meet in the lattice).
        """
        return TrustTier(_tier_at(min(self.rank, other.rank)))

    def join(self, other: TrustTier) -> TrustTier:
        """Return the join (least upper bound) ⊔ of two tiers.

        Parameters
        ----------
        other:
            The other tier.

        Returns
        -------
        TrustTier
            The stronger of the two tiers (the join in the lattice).
        """
        return TrustTier(_tier_at(max(self.rank, other.rank)))

    def lift_one(self) -> TrustTier:
        """Return the tier one step higher (capped at PROOF_BACKED)."""
        return TrustTier(_tier_at(self.rank + 1))

    def lower_one(self) -> TrustTier:
        """Return the tier one step lower (floored at PROPOSAL)."""
        return TrustTier(_tier_at(self.rank - 1))

    def is_admissible(self) -> bool:
        """Return True iff this tier is at least REVIEWED (admissible)."""
        return self.rank >= _TIER_RANK["REVIEWED"]

    def is_oracle_bounded(self) -> bool:
        """Return True iff this tier is at most the oracle ceiling (PROPOSAL)."""
        return self.rank <= _TIER_RANK[_ORACLE_CEILING]


class OperationKind(str, Enum):
    """Discriminant for the six trust algebra operations.

    Members
    -------
    MEET:
        ⊓ — greatest lower bound (conservative composition for shared evidence).
    JOIN:
        ⊔ — least upper bound (optimistic combination; rarely used in practice).
    COMPOSE:
        ⊕ — conservative composition; always returns at most the meet.
    ATTENUATE:
        ⊖ — strictly lowers trust by one step (models channel attenuation).
    LIFT:
        ↑_π — named promotion; requires explicit policy and justification.
    LOWER:
        ↓_χ — challenge-demotion; strictly lowers trust and records challenger.
    """

    MEET = "meet"
    JOIN = "join"
    COMPOSE = "compose"
    ATTENUATE = "attenuate"
    LIFT = "lift"
    LOWER = "lower"


class AdmissibilityStatus(str, Enum):
    """Result of an admissibility check.

    Members
    -------
    ADMISSIBLE:
        The evidence configuration satisfies all admissibility criteria.
    INADMISSIBLE:
        At least one criterion is violated; the configuration cannot enter E_adm.
    PENDING:
        The check has not yet been completed.
    CHALLENGED:
        The configuration was previously admissible but has been challenged
        and is pending re-evaluation.
    """

    ADMISSIBLE = "admissible"
    INADMISSIBLE = "inadmissible"
    PENDING = "pending"
    CHALLENGED = "challenged"


# ---------------------------------------------------------------------------
# TrustElement — immutable element of E_adm
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TrustElement:
    """An immutable element of the admissible evidence set E_adm.

    ``TrustElement`` is the atomic unit of the trust ordered algebra.  Each
    element carries a :class:`TrustTier`, a unique identifier, a
    justification string, and a provenance chain recording every operation
    that produced or modified this element.

    Elements are immutable; every algebra operation (meet, join, lift, …)
    returns a new element rather than mutating the original.

    Theory2.tex §formal_core: a trust element is *admissible* iff its tier is
    at least REVIEWED and its justification is non-empty.

    Parameters
    ----------
    element_id:
        Stable unique identifier.
    tier:
        The :class:`TrustTier` of this element.
    justification:
        Non-empty string describing the evidence basis.
    policy_id:
        The policy identifier π used for the last lift operation, or ``""``
        if no lift has been applied.
    challenger_id:
        The identifier of the last successful challenger, or ``""`` if
        no challenge has been applied.
    is_contradicted:
        True iff this element has been contradicted and is no longer admissible.
    provenance:
        Ordered tuple of step labels recording the derivation history.
    metadata:
        Auxiliary key-value pairs.
    """

    element_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    tier: TrustTier = TrustTier.PROPOSAL
    justification: str = ""
    policy_id: str = ""
    challenger_id: str = ""
    is_contradicted: bool = False
    provenance: tuple[str, ...] = field(default_factory=tuple)
    metadata: dict[str, Any] = field(default_factory=dict)

    def is_admissible(self) -> bool:
        """Return True iff this element satisfies all admissibility criteria.

        Admissibility requires:
        1. Tier ≥ REVIEWED.
        2. Non-empty justification.
        3. Not contradicted.
        4. Non-empty provenance.

        Returns
        -------
        bool
        """
        return (
            self.tier.is_admissible()
            and bool(self.justification)
            and not self.is_contradicted
            and len(self.provenance) > 0
        )

    def dominates(self, other: TrustElement) -> bool:
        """Return True iff ``self`` ≽ ``other`` in the trust order (≼).

        Parameters
        ----------
        other:
            The other element to compare.
        """
        return self.tier.dominates(other.tier)

    def leq(self, other: TrustElement) -> bool:
        """Return True iff ``self`` ≼ ``other`` (self is at most as strong)."""
        return self.tier.rank <= other.tier.rank

    def meet(self, other: TrustElement, step_label: str = "meet") -> TrustElement:
        """Compute the meet ⊓ of two elements.

        The meet returns the element with the weaker tier (conservative).
        The resulting element's justification concatenates both inputs.

        Parameters
        ----------
        other:
            The other element.
        step_label:
            Provenance label for this operation.

        Returns
        -------
        TrustElement
            A new element at the meet tier.
        """
        meet_tier = self.tier.meet(other.tier)
        combined_justification = f"meet({self.justification[:40]}; {other.justification[:40]})"
        return TrustElement(
            tier=meet_tier,
            justification=combined_justification,
            policy_id=self.policy_id,
            challenger_id=self.challenger_id,
            is_contradicted=self.is_contradicted or other.is_contradicted,
            provenance=self.provenance + other.provenance + (step_label,),
            metadata={"left": self.element_id, "right": other.element_id},
        )

    def join(self, other: TrustElement, step_label: str = "join") -> TrustElement:
        """Compute the join ⊔ of two elements.

        The join returns the element with the stronger tier.  Note: joins
        should be used sparingly — in most contexts the algebra uses meet/⊕.

        Parameters
        ----------
        other:
            The other element.
        step_label:
            Provenance label for this operation.

        Returns
        -------
        TrustElement
            A new element at the join tier.
        """
        join_tier = self.tier.join(other.tier)
        combined = f"join({self.justification[:40]}; {other.justification[:40]})"
        return TrustElement(
            tier=join_tier,
            justification=combined,
            policy_id=self.policy_id,
            challenger_id=self.challenger_id,
            is_contradicted=self.is_contradicted or other.is_contradicted,
            provenance=self.provenance + other.provenance + (step_label,),
            metadata={"left": self.element_id, "right": other.element_id},
        )

    def compose(self, other: TrustElement, step_label: str = "compose") -> TrustElement:
        """Conservative composition ⊕: returns at most the meet.

        Theory2.tex §formal_core axiom: ⊕ is conservative — composing two
        elements never yields more trust than the weaker input.  This is
        equivalent to meet (⊓) for the tier lattice.

        The **oracle ceiling axiom** is enforced here: if both elements are
        at PROPOSAL tier, the result is PROPOSAL (no self-composition escape).

        Parameters
        ----------
        other:
            The other element to compose with.
        step_label:
            Provenance label.

        Returns
        -------
        TrustElement
            The composed element (tier = meet of both tiers).
        """
        if self.tier == TrustTier.PROPOSAL and other.tier == TrustTier.PROPOSAL:
            # Oracle ceiling axiom: PROPOSAL ⊕ PROPOSAL = PROPOSAL
            composed_tier = TrustTier.PROPOSAL
        else:
            composed_tier = self.tier.meet(other.tier)
        combined = f"compose({self.justification[:40]}; {other.justification[:40]})"
        return TrustElement(
            tier=composed_tier,
            justification=combined,
            policy_id=self.policy_id,
            challenger_id=self.challenger_id,
            is_contradicted=self.is_contradicted or other.is_contradicted,
            provenance=self.provenance + other.provenance + (step_label,),
            metadata={"left": self.element_id, "right": other.element_id, "oracle_ceiling_applied": True},
        )

    def attenuate(self, step_label: str = "attenuate") -> TrustElement:
        """Attenuation ⊖: strictly lower the trust tier by one step.

        Models the effect of transmitting evidence through a lossy channel:
        trust can only decrease.  The tier is floored at PROPOSAL.

        Parameters
        ----------
        step_label:
            Provenance label.

        Returns
        -------
        TrustElement
            A new element with tier lowered by one step.
        """
        new_tier = self.tier.lower_one()
        return replace(
            self,
            tier=new_tier,
            provenance=self.provenance + (step_label,),
            metadata={**self.metadata, "attenuated_from": self.tier.value},
        )

    def lift(
        self, policy_id: str, justification: str, step_label: str = "lift"
    ) -> TrustElement:
        """Named lift ↑_π: raise tier by one step, citing a named policy.

        Theory2.tex §formal_core no-silent-promotion theorem: every increase
        in trust must cite a named policy π and a non-empty justification.
        This method raises tier by one step and records the policy in
        ``policy_id``.  If ``justification`` is empty, the lift is refused
        and the element is returned unchanged with a refusal marker in provenance.

        Parameters
        ----------
        policy_id:
            The named policy identifier π.
        justification:
            Non-empty justification for the lift.
        step_label:
            Provenance label.

        Returns
        -------
        TrustElement
            A new element with tier raised by one step, or the original
            element if the lift was refused.
        """
        if not justification.strip():
            log.warning(
                "TrustElement.lift: refused silent promotion (no justification) "
                "element_id=%r policy=%r", self.element_id, policy_id
            )
            return replace(
                self,
                provenance=self.provenance + (f"lift_refused:no_justification:{policy_id}",),
            )
        if not policy_id.strip():
            log.warning(
                "TrustElement.lift: refused unnamed promotion element_id=%r", self.element_id
            )
            return replace(
                self,
                provenance=self.provenance + ("lift_refused:no_policy_id",),
            )
        new_tier = self.tier.lift_one()
        return replace(
            self,
            tier=new_tier,
            justification=justification,
            policy_id=policy_id,
            provenance=self.provenance + (f"{step_label}:{policy_id}",),
            metadata={**self.metadata, "lifted_from": self.tier.value, "policy": policy_id},
        )

    def lower(self, challenger_id: str, residual_evidence: str, step_label: str = "lower") -> TrustElement:
        """Challenge-lower ↓_χ: demote tier by one step on successful challenge.

        Records the challenger identity and a residual evidence pointer so
        that the challenge itself is auditable.

        Theory2.tex §formal_core challenge-conservativity: ↓_χ must strictly
        lower trust and must record residual_evidence.

        Parameters
        ----------
        challenger_id:
            Identifier of the entity issuing the challenge.
        residual_evidence:
            Pointer to the evidence that motivated the challenge.
        step_label:
            Provenance label.

        Returns
        -------
        TrustElement
            A new element with tier lowered by one step.
        """
        if not residual_evidence.strip():
            log.warning(
                "TrustElement.lower: challenge without residual evidence is not conservativity-safe; "
                "applying with warning. element_id=%r", self.element_id
            )
        new_tier = self.tier.lower_one()
        return replace(
            self,
            tier=new_tier,
            challenger_id=challenger_id,
            provenance=self.provenance + (f"{step_label}:{challenger_id}",),
            metadata={
                **self.metadata,
                "lowered_from": self.tier.value,
                "challenger": challenger_id,
                "residual_evidence": residual_evidence,
            },
        )

    def fingerprint(self) -> str:
        """Return a short SHA-256 fingerprint of this element."""
        payload = json.dumps(
            {"element_id": self.element_id, "tier": self.tier.value, "justification": self.justification},
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode()).hexdigest()[:16]

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dict."""
        return {
            "element_id": self.element_id,
            "tier": self.tier.value,
            "justification": self.justification,
            "policy_id": self.policy_id,
            "challenger_id": self.challenger_id,
            "is_contradicted": self.is_contradicted,
            "provenance": list(self.provenance),
            "metadata": dict(self.metadata),
            "is_admissible": self.is_admissible(),
            "fingerprint": self.fingerprint(),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> TrustElement:
        """Deserialise from a plain dict produced by :meth:`to_dict`."""
        return cls(
            element_id=d.get("element_id", str(uuid.uuid4())),
            tier=TrustTier(d.get("tier", TrustTier.PROPOSAL.value)),
            justification=d.get("justification", ""),
            policy_id=d.get("policy_id", ""),
            challenger_id=d.get("challenger_id", ""),
            is_contradicted=bool(d.get("is_contradicted", False)),
            provenance=tuple(d.get("provenance", [])),
            metadata=dict(d.get("metadata", {})),
        )


# ---------------------------------------------------------------------------
# AdmissibleSupport — validated evidence bundle
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AdmissibleSupport:
    """A validated evidence bundle that has passed all admissibility checks.

    An ``AdmissibleSupport`` packages one or more :class:`TrustElement` objects
    that collectively justify a trust claim for a specific context coordinate.
    The aggregate tier is the **meet** of all constituent element tiers (the
    bundle is only as strong as its weakest element).

    Theory2.tex §formal_core: a support bundle enters E_adm iff every constituent
    element is admissible and the aggregate tier is at least REVIEWED.

    Parameters
    ----------
    support_id:
        Stable unique identifier.
    coord_id:
        The context coordinate this support bundle is associated with.
    elements:
        The constituent :class:`TrustElement` objects.
    aggregate_tier:
        Pre-computed aggregate (meet) tier.
    admissibility_status:
        Current :class:`AdmissibilityStatus`.
    channel_tag:
        The discharge channel (e.g. ``"z3"``, ``"lean4"``, ``"human_review"``).
    created_at:
        Unix timestamp of creation.
    """

    support_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    coord_id: str = ""
    elements: tuple[TrustElement, ...] = field(default_factory=tuple)
    aggregate_tier: TrustTier = TrustTier.PROPOSAL
    admissibility_status: AdmissibilityStatus = AdmissibilityStatus.PENDING
    channel_tag: str = ""
    created_at: float = field(default_factory=time.time)

    @classmethod
    def from_elements(
        cls,
        coord_id: str,
        elements: Sequence[TrustElement],
        channel_tag: str = "",
    ) -> AdmissibleSupport:
        """Create an ``AdmissibleSupport`` by computing the aggregate tier.

        Parameters
        ----------
        coord_id:
            Context coordinate.
        elements:
            Constituent trust elements.
        channel_tag:
            Discharge channel.

        Returns
        -------
        AdmissibleSupport
            A new support bundle with aggregate tier = meet of all element tiers.
        """
        if not elements:
            return cls(
                coord_id=coord_id,
                elements=tuple(),
                aggregate_tier=TrustTier.PROPOSAL,
                admissibility_status=AdmissibilityStatus.INADMISSIBLE,
                channel_tag=channel_tag,
            )
        agg = elements[0].tier
        for e in elements[1:]:
            agg = agg.meet(e.tier)
        all_admissible = all(e.is_admissible() for e in elements)
        status = AdmissibilityStatus.ADMISSIBLE if all_admissible else AdmissibilityStatus.INADMISSIBLE
        return cls(
            coord_id=coord_id,
            elements=tuple(elements),
            aggregate_tier=agg,
            admissibility_status=status,
            channel_tag=channel_tag,
        )

    def is_admissible(self) -> bool:
        """Return True iff this support bundle is admissible."""
        return (
            self.admissibility_status == AdmissibilityStatus.ADMISSIBLE
            and self.aggregate_tier.is_admissible()
            and all(e.is_admissible() for e in self.elements)
        )

    def element_count(self) -> int:
        """Return the number of constituent elements."""
        return len(self.elements)

    def fingerprint(self) -> str:
        """Return a short fingerprint of this support bundle."""
        ids = sorted(e.element_id for e in self.elements)
        payload = json.dumps({"support_id": self.support_id, "elements": ids}, sort_keys=True)
        return hashlib.sha256(payload.encode()).hexdigest()[:16]

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dict."""
        return {
            "support_id": self.support_id,
            "coord_id": self.coord_id,
            "elements": [e.to_dict() for e in self.elements],
            "aggregate_tier": self.aggregate_tier.value,
            "admissibility_status": self.admissibility_status.value,
            "channel_tag": self.channel_tag,
            "created_at": self.created_at,
            "is_admissible": self.is_admissible(),
        }


# ---------------------------------------------------------------------------
# TrustBound — floor + ceiling pair
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TrustBound:
    """A (floor, ceiling) pair bounding admissible trust tiers in a context.

    ``TrustBound`` is the B component of the judgment tuple (c, φ, A, E, O, B, T, Π).
    It constrains the range of acceptable trust tiers for a given judgment:
    - Evidence below the floor is inadmissible for this context.
    - Evidence above the ceiling cannot be claimed (oracle ceiling).

    Parameters
    ----------
    bound_id:
        Stable unique identifier.
    floor:
        Minimum required trust tier (inclusive).
    ceiling:
        Maximum permitted trust tier (inclusive).
    scope_key:
        A key identifying the scope to which this bound applies.
    rationale:
        Free-text explanation for why this bound was set.
    """

    bound_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    floor: TrustTier = TrustTier.PROPOSAL
    ceiling: TrustTier = TrustTier.PROOF_BACKED
    scope_key: str = ""
    rationale: str = ""

    def permits(self, element: TrustElement) -> bool:
        """Return True iff *element*'s tier is within [floor, ceiling].

        Parameters
        ----------
        element:
            The trust element to check.

        Returns
        -------
        bool
        """
        return (
            element.tier.rank >= self.floor.rank
            and element.tier.rank <= self.ceiling.rank
        )

    def clamp(self, element: TrustElement) -> TrustElement:
        """Clamp *element* to lie within this bound's [floor, ceiling].

        If the element's tier is below the floor, it is lifted to the floor.
        If it is above the ceiling, it is attenuated to the ceiling.

        Parameters
        ----------
        element:
            The element to clamp.

        Returns
        -------
        TrustElement
            The clamped element (may be the same object if already in range).
        """
        r = element.tier.rank
        if r < self.floor.rank:
            return replace(
                element,
                tier=self.floor,
                provenance=element.provenance + (f"clamp_to_floor:{self.floor.value}",),
                metadata={**element.metadata, "clamped_from": element.tier.value, "bound_id": self.bound_id},
            )
        if r > self.ceiling.rank:
            return replace(
                element,
                tier=self.ceiling,
                provenance=element.provenance + (f"clamp_to_ceiling:{self.ceiling.value}",),
                metadata={**element.metadata, "clamped_from": element.tier.value, "bound_id": self.bound_id},
            )
        return element

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dict."""
        return {
            "bound_id": self.bound_id,
            "floor": self.floor.value,
            "ceiling": self.ceiling.value,
            "scope_key": self.scope_key,
            "rationale": self.rationale,
        }


# ---------------------------------------------------------------------------
# TrustOperation — named algebra operation with audit record
# ---------------------------------------------------------------------------


@dataclass
class TrustOperation:
    """A named algebra operation with a full audit record.

    ``TrustOperation`` wraps a single invocation of one of the six trust
    algebra operations (meet, join, compose, attenuate, lift, lower) and
    records the input elements, output element, and a structured audit dict.

    Parameters
    ----------
    op_id:
        Stable unique identifier for this operation record.
    kind:
        :class:`OperationKind` discriminant.
    input_elements:
        The input :class:`TrustElement` objects (one for unary ops, two for binary).
    output_element:
        The resulting :class:`TrustElement`.
    policy_id:
        The policy identifier π (non-empty only for lift operations).
    justification:
        Free-text justification for the operation.
    timestamp:
        Unix timestamp.
    success:
        Whether the operation succeeded (False for refused lifts, etc.).
    """

    op_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    kind: OperationKind = OperationKind.MEET
    input_elements: list[TrustElement] = field(default_factory=list)
    output_element: TrustElement = field(default_factory=TrustElement)
    policy_id: str = ""
    justification: str = ""
    timestamp: float = field(default_factory=time.time)
    success: bool = True

    def audit_record(self) -> dict[str, Any]:
        """Return a structured audit record for this operation.

        Returns
        -------
        dict[str, Any]
            Structured record suitable for appending to an audit log.
        """
        return {
            "op_id": self.op_id,
            "kind": self.kind.value,
            "input_tiers": [e.tier.value for e in self.input_elements],
            "output_tier": self.output_element.tier.value,
            "policy_id": self.policy_id,
            "justification": self.justification[:80] if self.justification else "",
            "timestamp": self.timestamp,
            "success": self.success,
        }

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dict."""
        return {
            "op_id": self.op_id,
            "kind": self.kind.value,
            "input_elements": [e.to_dict() for e in self.input_elements],
            "output_element": self.output_element.to_dict(),
            "policy_id": self.policy_id,
            "justification": self.justification,
            "timestamp": self.timestamp,
            "success": self.success,
        }


# ---------------------------------------------------------------------------
# TrustAlgebra — T = (E_adm, ≼, ⊕, ⊖, ↑_π, ↓_χ)
# ---------------------------------------------------------------------------


@dataclass
class TrustAlgebra:
    """The full trust ordered algebra T = (E_adm, ≼, ⊕, ⊖, ↑_π, ↓_χ).

    ``TrustAlgebra`` is a stateful object that:
    - Maintains a registry of :class:`AdmissibleSupport` bundles.
    - Applies all six algebra operations and records each in the audit log.
    - Enforces the oracle ceiling axiom, the no-silent-promotion theorem,
      and the challenge-conservativity axiom.
    - Provides methods for computing partial orders, meets, and joins over
      registered elements.

    Parameters
    ----------
    algebra_id:
        Stable unique identifier.
    elements:
        Registered :class:`TrustElement` objects keyed by element_id.
    support_bundles:
        Registered :class:`AdmissibleSupport` bundles keyed by support_id.
    audit_log:
        Append-only log of all :class:`TrustOperation` records.
    registered_policies:
        Set of known policy identifiers π.
    default_bound:
        The :class:`TrustBound` applied when no context-specific bound exists.
    """

    algebra_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    elements: dict[str, TrustElement] = field(default_factory=dict)
    support_bundles: dict[str, AdmissibleSupport] = field(default_factory=dict)
    audit_log: list[TrustOperation] = field(default_factory=list)
    registered_policies: set[str] = field(default_factory=set)
    default_bound: TrustBound = field(
        default_factory=lambda: TrustBound(
            floor=TrustTier.PROPOSAL, ceiling=TrustTier.PROOF_BACKED
        )
    )

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register_element(self, element: TrustElement) -> bool:
        """Register a :class:`TrustElement` in the algebra.

        Returns False and logs a warning if the element is already contradicted.

        Parameters
        ----------
        element:
            The element to register.

        Returns
        -------
        bool
            True if registration succeeded.
        """
        if element.is_contradicted:
            log.warning(
                "TrustAlgebra.register_element: refused contradicted element %r",
                element.element_id,
            )
            return False
        self.elements[element.element_id] = element
        log.debug("TrustAlgebra.register_element: id=%r tier=%r", element.element_id, element.tier.value)
        return True

    def register_support(self, support: AdmissibleSupport) -> bool:
        """Register an :class:`AdmissibleSupport` bundle.

        Returns False if the bundle is not admissible.

        Parameters
        ----------
        support:
            The support bundle to register.

        Returns
        -------
        bool
            True if registration succeeded.
        """
        if not support.is_admissible():
            log.warning(
                "TrustAlgebra.register_support: inadmissible bundle %r rejected",
                support.support_id,
            )
            return False
        self.support_bundles[support.support_id] = support
        return True

    def register_policy(self, policy_id: str) -> None:
        """Register a named lift policy identifier.

        Parameters
        ----------
        policy_id:
            The policy identifier π to register.
        """
        self.registered_policies.add(policy_id)
        log.debug("TrustAlgebra.register_policy: %r", policy_id)

    # ------------------------------------------------------------------
    # Partial order (≼)
    # ------------------------------------------------------------------

    def leq(self, a: TrustElement, b: TrustElement) -> bool:
        """Return True iff a ≼ b (a is at most as trusted as b).

        Parameters
        ----------
        a, b:
            Elements to compare.
        """
        return a.tier.rank <= b.tier.rank

    def geq(self, a: TrustElement, b: TrustElement) -> bool:
        """Return True iff a ≽ b (a is at least as trusted as b)."""
        return a.tier.rank >= b.tier.rank

    # ------------------------------------------------------------------
    # Meet ⊓
    # ------------------------------------------------------------------

    def meet(self, a: TrustElement, b: TrustElement) -> TrustElement:
        """Compute the meet ⊓ of two elements and record the operation.

        Parameters
        ----------
        a, b:
            The input elements.

        Returns
        -------
        TrustElement
            The meet element.
        """
        result = a.meet(b, step_label="⊓")
        op = TrustOperation(
            kind=OperationKind.MEET,
            input_elements=[a, b],
            output_element=result,
        )
        self.audit_log.append(op)
        self.elements[result.element_id] = result
        return result

    # ------------------------------------------------------------------
    # Join ⊔
    # ------------------------------------------------------------------

    def join(self, a: TrustElement, b: TrustElement) -> TrustElement:
        """Compute the join ⊔ of two elements and record the operation.

        Parameters
        ----------
        a, b:
            The input elements.

        Returns
        -------
        TrustElement
            The join element.
        """
        result = a.join(b, step_label="⊔")
        op = TrustOperation(
            kind=OperationKind.JOIN,
            input_elements=[a, b],
            output_element=result,
        )
        self.audit_log.append(op)
        self.elements[result.element_id] = result
        return result

    # ------------------------------------------------------------------
    # Conservative composition ⊕
    # ------------------------------------------------------------------

    def compose(self, a: TrustElement, b: TrustElement) -> TrustElement:
        """Conservative composition ⊕: at most the meet of the two inputs.

        Enforces the oracle ceiling axiom: PROPOSAL ⊕ PROPOSAL = PROPOSAL.

        Parameters
        ----------
        a, b:
            The input elements.

        Returns
        -------
        TrustElement
            The composed element.
        """
        result = a.compose(b, step_label="⊕")
        op = TrustOperation(
            kind=OperationKind.COMPOSE,
            input_elements=[a, b],
            output_element=result,
        )
        self.audit_log.append(op)
        self.elements[result.element_id] = result
        return result

    # ------------------------------------------------------------------
    # Attenuation ⊖
    # ------------------------------------------------------------------

    def attenuate(self, element: TrustElement) -> TrustElement:
        """Attenuation ⊖: strictly lower the tier by one step.

        Parameters
        ----------
        element:
            The element to attenuate.

        Returns
        -------
        TrustElement
            A new element with tier lowered by one step.
        """
        result = element.attenuate(step_label="⊖")
        op = TrustOperation(
            kind=OperationKind.ATTENUATE,
            input_elements=[element],
            output_element=result,
        )
        self.audit_log.append(op)
        self.elements[result.element_id] = result
        return result

    # ------------------------------------------------------------------
    # Lift ↑_π
    # ------------------------------------------------------------------

    def lift(
        self,
        element: TrustElement,
        policy_id: str,
        justification: str,
    ) -> tuple[TrustElement, bool]:
        """Named lift ↑_π: raise tier by one step if policy is registered.

        Enforces the no-silent-promotion theorem: the lift is refused if
        ``policy_id`` is not in ``registered_policies`` or if ``justification``
        is empty.

        Parameters
        ----------
        element:
            The element to lift.
        policy_id:
            The named policy π.
        justification:
            Non-empty justification.

        Returns
        -------
        tuple[TrustElement, bool]
            The resulting element and a success flag.
        """
        if not justification.strip():
            log.warning("TrustAlgebra.lift: refused (no justification) element=%r", element.element_id)
            refused = replace(element, provenance=element.provenance + ("lift_refused:no_justification",))
            op = TrustOperation(kind=OperationKind.LIFT, input_elements=[element], output_element=refused, success=False)
            self.audit_log.append(op)
            return refused, False
        if policy_id not in self.registered_policies:
            log.warning("TrustAlgebra.lift: unregistered policy %r element=%r", policy_id, element.element_id)
            refused = replace(element, provenance=element.provenance + (f"lift_refused:unregistered:{policy_id}",))
            op = TrustOperation(kind=OperationKind.LIFT, input_elements=[element], output_element=refused, policy_id=policy_id, success=False)
            self.audit_log.append(op)
            return refused, False
        result = element.lift(policy_id=policy_id, justification=justification, step_label="↑")
        op = TrustOperation(
            kind=OperationKind.LIFT,
            input_elements=[element],
            output_element=result,
            policy_id=policy_id,
            justification=justification,
            success=True,
        )
        self.audit_log.append(op)
        self.elements[result.element_id] = result
        return result, True

    # ------------------------------------------------------------------
    # Challenge-lower ↓_χ
    # ------------------------------------------------------------------

    def lower(
        self,
        element: TrustElement,
        challenger_id: str,
        residual_evidence: str,
    ) -> TrustElement:
        """Challenge-lower ↓_χ: demote tier by one step, recording the challenge.

        Parameters
        ----------
        element:
            The element to demote.
        challenger_id:
            Identity of the challenger.
        residual_evidence:
            Pointer to the evidence motivating the challenge.

        Returns
        -------
        TrustElement
            The demoted element.
        """
        result = element.lower(
            challenger_id=challenger_id,
            residual_evidence=residual_evidence,
            step_label="↓",
        )
        op = TrustOperation(
            kind=OperationKind.LOWER,
            input_elements=[element],
            output_element=result,
            justification=f"challenge by {challenger_id}: {residual_evidence[:60]}",
        )
        self.audit_log.append(op)
        self.elements[result.element_id] = result
        return result

    # ------------------------------------------------------------------
    # Invariant verification
    # ------------------------------------------------------------------

    def verify_no_silent_promotion(self) -> list[str]:
        """Verify the no-silent-promotion theorem.

        Returns a list of violation descriptions; empty if the invariant holds.

        Returns
        -------
        list[str]
        """
        violations: list[str] = []
        for op in self.audit_log:
            if op.kind == OperationKind.LIFT and op.success:
                if not op.policy_id:
                    violations.append(
                        f"No-silent-promotion violation: op={op.op_id} has no policy_id"
                    )
                if not op.justification:
                    violations.append(
                        f"No-silent-promotion violation: op={op.op_id} has no justification"
                    )
        return violations

    def verify_oracle_ceiling(self) -> list[str]:
        """Verify the oracle ceiling axiom.

        Every composition of PROPOSAL elements must yield a PROPOSAL output.

        Returns
        -------
        list[str]
            Violation descriptions; empty if axiom holds.
        """
        violations: list[str] = []
        for op in self.audit_log:
            if op.kind == OperationKind.COMPOSE:
                inputs_proposal = all(e.tier == TrustTier.PROPOSAL for e in op.input_elements)
                if inputs_proposal and op.output_element.tier != TrustTier.PROPOSAL:
                    violations.append(
                        f"Oracle ceiling violation: op={op.op_id} PROPOSAL⊕PROPOSAL → {op.output_element.tier.value}"
                    )
        return violations

    def verify_challenge_conservativity(self) -> list[str]:
        """Verify the challenge-conservativity axiom.

        Every lower operation must strictly decrease tier and record residual evidence.

        Returns
        -------
        list[str]
        """
        violations: list[str] = []
        for op in self.audit_log:
            if op.kind == OperationKind.LOWER and op.input_elements:
                before = op.input_elements[0].tier.rank
                after = op.output_element.tier.rank
                if after >= before:
                    violations.append(
                        f"Challenge-conservativity violation: op={op.op_id} tier did not decrease "
                        f"({op.input_elements[0].tier.value} → {op.output_element.tier.value})"
                    )
                if not op.output_element.metadata.get("residual_evidence"):
                    violations.append(
                        f"Challenge-conservativity violation: op={op.op_id} missing residual_evidence"
                    )
        return violations

    def verify_all(self) -> dict[str, list[str]]:
        """Run all three invariant checks and return results.

        Returns
        -------
        dict[str, list[str]]
            Mapping check_name → violation list (empty list = passed).
        """
        return {
            "no_silent_promotion": self.verify_no_silent_promotion(),
            "oracle_ceiling": self.verify_oracle_ceiling(),
            "challenge_conservativity": self.verify_challenge_conservativity(),
        }

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    def admissible_elements(self) -> list[TrustElement]:
        """Return the list of admissible elements currently registered."""
        return [e for e in self.elements.values() if e.is_admissible()]

    def highest_tier(self) -> TrustTier:
        """Return the highest tier among all registered admissible elements."""
        adm = self.admissible_elements()
        if not adm:
            return TrustTier.PROPOSAL
        return max(adm, key=lambda e: e.tier.rank).tier

    def audit_summary(self) -> dict[str, Any]:
        """Return a compact summary of the audit log."""
        op_counts: dict[str, int] = {}
        for op in self.audit_log:
            op_counts[op.kind.value] = op_counts.get(op.kind.value, 0) + 1
        return {
            "algebra_id": self.algebra_id,
            "total_operations": len(self.audit_log),
            "operation_counts": op_counts,
            "registered_elements": len(self.elements),
            "admissible_elements": len(self.admissible_elements()),
            "registered_policies": list(self.registered_policies),
        }

    def describe(self) -> str:
        """Return a human-readable summary of this algebra instance."""
        summary = self.audit_summary()
        violations = self.verify_all()
        total_violations = sum(len(v) for v in violations.values())
        return (
            f"TrustAlgebra {self.algebra_id}\n"
            f"  Elements         : {summary['registered_elements']}\n"
            f"  Admissible       : {summary['admissible_elements']}\n"
            f"  Total operations : {summary['total_operations']}\n"
            f"  Op counts        : {summary['operation_counts']}\n"
            f"  Policies         : {summary['registered_policies']}\n"
            f"  Highest tier     : {self.highest_tier().value}\n"
            f"  Invariant violations: {total_violations}\n"
        )


# ---------------------------------------------------------------------------
# TrustOrderedAlgebraAdmissibleWitness — immutable run certificate
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TrustOrderedAlgebraAdmissibleWitness:
    """Immutable certificate produced by a completed trust algebra run.

    A ``TrustOrderedAlgebraAdmissibleWitness`` records the full outcome of an
    algebra computation cycle: which operations were applied, the final highest
    tier achieved, which invariants were verified, and the complete provenance
    chain.

    Parameters
    ----------
    witness_id:
        Stable unique identifier.
    algebra_id:
        The algebra that produced this witness.
    operation_count:
        Total number of operations performed.
    admissible_element_count:
        Number of admissible elements at completion.
    highest_tier:
        The highest trust tier attained.
    invariant_results:
        Mapping invariant_name → list of violations (empty = passed).
    provenance:
        Ordered chain of step labels.
    created_at:
        Unix timestamp.
    metadata:
        Auxiliary key-value pairs.
    """

    witness_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    algebra_id: str = ""
    operation_count: int = 0
    admissible_element_count: int = 0
    highest_tier: str = "PROPOSAL"
    invariant_results: dict[str, list[str]] = field(default_factory=dict)
    provenance: tuple[str, ...] = field(default_factory=tuple)
    created_at: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)

    def all_invariants_pass(self) -> bool:
        """Return True iff every invariant check produced no violations."""
        return all(len(v) == 0 for v in self.invariant_results.values())

    def trust_rank(self) -> int:
        """Return the integer rank of ``highest_tier``."""
        return _rank(self.highest_tier)

    def summary(self) -> str:
        """Return a one-line summary."""
        ok = "✓" if self.all_invariants_pass() else "✗"
        return (
            f"[TrustWitness {self.witness_id[:8]}] algebra={self.algebra_id[:8]} "
            f"ops={self.operation_count} adm={self.admissible_element_count} "
            f"max_tier={self.highest_tier} invariants={ok}"
        )

    def validate(self) -> list[str]:
        """Return validation violations; empty if valid."""
        errors: list[str] = []
        if not self.witness_id:
            errors.append("witness_id must not be empty")
        if not self.algebra_id:
            errors.append("algebra_id must not be empty")
        if self.highest_tier not in _TIER_RANK:
            errors.append(f"Unknown highest_tier: {self.highest_tier!r}")
        return errors

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dict."""
        return {
            "witness_id": self.witness_id,
            "algebra_id": self.algebra_id,
            "operation_count": self.operation_count,
            "admissible_element_count": self.admissible_element_count,
            "highest_tier": self.highest_tier,
            "invariant_results": {k: list(v) for k, v in self.invariant_results.items()},
            "provenance": list(self.provenance),
            "created_at": self.created_at,
            "metadata": dict(self.metadata),
            "all_invariants_pass": self.all_invariants_pass(),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> TrustOrderedAlgebraAdmissibleWitness:
        """Deserialise from a plain dict produced by :meth:`to_dict`."""
        return cls(
            witness_id=d.get("witness_id", str(uuid.uuid4())),
            algebra_id=d.get("algebra_id", ""),
            operation_count=int(d.get("operation_count", 0)),
            admissible_element_count=int(d.get("admissible_element_count", 0)),
            highest_tier=d.get("highest_tier", "PROPOSAL"),
            invariant_results={k: list(v) for k, v in d.get("invariant_results", {}).items()},
            provenance=tuple(d.get("provenance", [])),
            created_at=float(d.get("created_at", time.time())),
            metadata=dict(d.get("metadata", {})),
        )

    def merge(self, other: TrustOrderedAlgebraAdmissibleWitness) -> TrustOrderedAlgebraAdmissibleWitness:
        """Merge two witnesses, keeping the stronger result at each dimension."""
        best_tier = (
            self.highest_tier
            if _rank(self.highest_tier) >= _rank(other.highest_tier)
            else other.highest_tier
        )
        merged_inv: dict[str, list[str]] = {}
        for k in set(self.invariant_results) | set(other.invariant_results):
            merged_inv[k] = list(self.invariant_results.get(k, [])) + list(other.invariant_results.get(k, []))
        return TrustOrderedAlgebraAdmissibleWitness(
            algebra_id=self.algebra_id or other.algebra_id,
            operation_count=self.operation_count + other.operation_count,
            admissible_element_count=self.admissible_element_count + other.admissible_element_count,
            highest_tier=best_tier,
            invariant_results=merged_inv,
            provenance=self.provenance + other.provenance + ("merged",),
            metadata={**other.metadata, **self.metadata},
        )


# ---------------------------------------------------------------------------
# TrustOrderedAlgebraAdmissibleCoordinator
# ---------------------------------------------------------------------------


@dataclass
class TrustOrderedAlgebraAdmissibleCoordinator:
    """Orchestrates trust algebra construction and operation application.

    The ``TrustOrderedAlgebraAdmissibleCoordinator`` manages the lifecycle of
    a :class:`TrustAlgebra` instance: registering elements, policies, and
    support bundles; applying all six operations; and emitting a
    :class:`TrustOrderedAlgebraAdmissibleWitness` at the end of each run.

    Parameters
    ----------
    coordinator_id:
        Stable unique identifier.
    algebra:
        The :class:`TrustAlgebra` being managed.
    witnesses:
        Witnesses emitted by this coordinator.
    run_log:
        Append-only log of coordinator actions.
    """

    coordinator_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    algebra: TrustAlgebra = field(default_factory=TrustAlgebra)
    witnesses: list[TrustOrderedAlgebraAdmissibleWitness] = field(default_factory=list)
    run_log: list[dict[str, Any]] = field(default_factory=list)

    def register_policy(self, policy_id: str) -> None:
        """Register a named lift policy."""
        self.algebra.register_policy(policy_id)
        self.run_log.append({"event": "register_policy", "policy_id": policy_id, "timestamp": time.time()})

    def create_element(
        self,
        tier: str,
        justification: str,
        provenance: tuple[str, ...] = ("initial",),
    ) -> TrustElement:
        """Create and register a :class:`TrustElement`.

        Parameters
        ----------
        tier:
            Trust tier string.
        justification:
            Non-empty justification.
        provenance:
            Initial provenance labels.

        Returns
        -------
        TrustElement
        """
        elem = TrustElement(
            tier=TrustTier(tier),
            justification=justification,
            provenance=provenance,
        )
        self.algebra.register_element(elem)
        self.run_log.append({
            "event": "create_element",
            "element_id": elem.element_id,
            "tier": tier,
            "timestamp": time.time(),
        })
        return elem

    def apply_meet(self, a: TrustElement, b: TrustElement) -> TrustElement:
        """Apply the meet ⊓ operation."""
        result = self.algebra.meet(a, b)
        self.run_log.append({"event": "meet", "result_tier": result.tier.value, "timestamp": time.time()})
        return result

    def apply_join(self, a: TrustElement, b: TrustElement) -> TrustElement:
        """Apply the join ⊔ operation."""
        result = self.algebra.join(a, b)
        self.run_log.append({"event": "join", "result_tier": result.tier.value, "timestamp": time.time()})
        return result

    def apply_compose(self, a: TrustElement, b: TrustElement) -> TrustElement:
        """Apply conservative composition ⊕."""
        result = self.algebra.compose(a, b)
        self.run_log.append({"event": "compose", "result_tier": result.tier.value, "timestamp": time.time()})
        return result

    def apply_attenuate(self, element: TrustElement) -> TrustElement:
        """Apply attenuation ⊖."""
        result = self.algebra.attenuate(element)
        self.run_log.append({"event": "attenuate", "result_tier": result.tier.value, "timestamp": time.time()})
        return result

    def apply_lift(
        self, element: TrustElement, policy_id: str, justification: str
    ) -> tuple[TrustElement, bool]:
        """Apply named lift ↑_π."""
        result, success = self.algebra.lift(element=element, policy_id=policy_id, justification=justification)
        self.run_log.append({
            "event": "lift",
            "policy_id": policy_id,
            "success": success,
            "result_tier": result.tier.value,
            "timestamp": time.time(),
        })
        return result, success

    def apply_lower(
        self, element: TrustElement, challenger_id: str, residual_evidence: str
    ) -> TrustElement:
        """Apply challenge-lower ↓_χ."""
        result = self.algebra.lower(element=element, challenger_id=challenger_id, residual_evidence=residual_evidence)
        self.run_log.append({
            "event": "lower",
            "challenger_id": challenger_id,
            "result_tier": result.tier.value,
            "timestamp": time.time(),
        })
        return result

    def produce_witness(self) -> TrustOrderedAlgebraAdmissibleWitness:
        """Emit an immutable :class:`TrustOrderedAlgebraAdmissibleWitness`."""
        invariants = self.algebra.verify_all()
        highest = self.algebra.highest_tier()
        w = TrustOrderedAlgebraAdmissibleWitness(
            algebra_id=self.algebra.algebra_id,
            operation_count=len(self.algebra.audit_log),
            admissible_element_count=len(self.algebra.admissible_elements()),
            highest_tier=highest.value,
            invariant_results={k: list(v) for k, v in invariants.items()},
            provenance=tuple(e["event"] for e in self.run_log[-10:]),
            metadata={"coordinator_id": self.coordinator_id},
        )
        self.witnesses.append(w)
        log.info("TrustOrderedAlgebraAdmissibleCoordinator.produce_witness: %s", w.summary())
        return w

    def validate(self) -> list[str]:
        """Return validation violations; empty if invariants hold."""
        violations: list[str] = []
        all_inv = self.algebra.verify_all()
        for check_name, viols in all_inv.items():
            violations.extend(viols)
        return violations

    def describe(self) -> str:
        """Return a human-readable summary."""
        return (
            f"TrustOrderedAlgebraAdmissibleCoordinator {self.coordinator_id}\n"
            + self.algebra.describe()
            + f"  Witnesses produced : {len(self.witnesses)}\n"
            + f"  Run log events     : {len(self.run_log)}\n"
        )


# ---------------------------------------------------------------------------
# TrustOrderedAlgebraAdmissibleAnalyzer
# ---------------------------------------------------------------------------


@dataclass
class TrustOrderedAlgebraAdmissibleAnalyzer:
    """Analyses audit trails and trust algebra witnesses.

    ``TrustOrderedAlgebraAdmissibleAnalyzer`` operates on a collection of
    :class:`TrustOrderedAlgebraAdmissibleWitness` objects and provides:

    - Operation-type distribution statistics.
    - Invariant pass rates across witnesses.
    - Detection of no-silent-promotion and oracle ceiling violations.
    - Computation of a composite trust-health score.

    Parameters
    ----------
    analyzer_id:
        Stable unique identifier.
    witnesses:
        The witnesses to analyse.
    algebra:
        Optional live :class:`TrustAlgebra` for deeper inspection.
    """

    analyzer_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    witnesses: list[TrustOrderedAlgebraAdmissibleWitness] = field(default_factory=list)
    algebra: TrustAlgebra | None = None

    def operation_distribution(self) -> dict[str, int]:
        """Return aggregated operation counts across all witnesses."""
        dist: dict[str, int] = {}
        for w in self.witnesses:
            for kind, count in w.metadata.get("operation_counts", {}).items():
                dist[kind] = dist.get(kind, 0) + count
        return dist

    def invariant_pass_rate(self, invariant_name: str) -> float:
        """Return the fraction of witnesses where *invariant_name* has no violations.

        Parameters
        ----------
        invariant_name:
            One of ``"no_silent_promotion"``, ``"oracle_ceiling"``,
            ``"challenge_conservativity"``.

        Returns
        -------
        float
            Value in [0, 1].
        """
        if not self.witnesses:
            return 1.0
        return sum(
            1 for w in self.witnesses
            if len(w.invariant_results.get(invariant_name, [])) == 0
        ) / len(self.witnesses)

    def overall_invariant_pass_rate(self) -> float:
        """Return the fraction of witnesses that pass ALL invariants."""
        if not self.witnesses:
            return 1.0
        return sum(1 for w in self.witnesses if w.all_invariants_pass()) / len(self.witnesses)

    def tier_distribution(self) -> dict[str, int]:
        """Return a count of witnesses by highest trust tier."""
        dist: dict[str, int] = {}
        for w in self.witnesses:
            dist[w.highest_tier] = dist.get(w.highest_tier, 0) + 1
        return dist

    def score(self) -> float:
        """Compute a composite trust-health score in [0, 1].

        Weighted sum:
        - Overall invariant pass rate (weight 0.5)
        - Average highest tier rank normalised to [0,1] (weight 0.3)
        - Average admissible element count normalised to [0,1] (weight 0.2)

        Returns
        -------
        float
        """
        pass_rate = self.overall_invariant_pass_rate()
        max_rank = max(_TIER_RANK.values()) or 1
        avg_rank = (
            sum(_rank(w.highest_tier) for w in self.witnesses) / len(self.witnesses)
            if self.witnesses else 0.0
        )
        normalised_rank = avg_rank / max_rank
        max_adm = max((w.admissible_element_count for w in self.witnesses), default=1) or 1
        avg_adm = (
            sum(w.admissible_element_count for w in self.witnesses) / len(self.witnesses)
            if self.witnesses else 0.0
        )
        normalised_adm = min(avg_adm / max_adm, 1.0)
        return 0.5 * pass_rate + 0.3 * normalised_rank + 0.2 * normalised_adm

    def detect_oracle_ceiling_violations(self) -> list[str]:
        """Return descriptions of oracle ceiling violations across witnesses."""
        violations: list[str] = []
        for w in self.witnesses:
            for v in w.invariant_results.get("oracle_ceiling", []):
                violations.append(f"[{w.witness_id[:8]}] {v}")
        return violations

    def report(self) -> str:
        """Return a rich multi-line analysis report."""
        lines = [
            f"TrustOrderedAlgebraAdmissibleAnalyzer {self.analyzer_id}",
            f"  Witnesses                 : {len(self.witnesses)}",
            f"  Overall invariant pass rate: {self.overall_invariant_pass_rate():.1%}",
            f"  No-silent-promotion rate   : {self.invariant_pass_rate('no_silent_promotion'):.1%}",
            f"  Oracle ceiling rate        : {self.invariant_pass_rate('oracle_ceiling'):.1%}",
            f"  Challenge-conservativity   : {self.invariant_pass_rate('challenge_conservativity'):.1%}",
            f"  Trust-health score         : {self.score():.3f}",
            f"  Tier distribution          : {self.tier_distribution()}",
        ]
        ov = self.detect_oracle_ceiling_violations()
        if ov:
            lines.append(f"  Oracle ceiling violations ({len(ov)}):")
            for v in ov[:3]:
                lines.append(f"    - {v}")
        else:
            lines.append("  Oracle ceiling violations : (none detected)")
        if self.algebra:
            lines.append(f"  Live algebra ops          : {len(self.algebra.audit_log)}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("=== trust_as_an_ordered_algebra_of_adm.py smoke test ===\n")

    # Build coordinator and algebra
    coord_obj = TrustOrderedAlgebraAdmissibleCoordinator()
    coord_obj.register_policy("z3_solver_discharge")
    coord_obj.register_policy("lean4_proof_certificate")

    # Create elements at various tiers
    e_proposal = coord_obj.create_element("PROPOSAL", "copilot-suggested check", ("initial",))
    e_reviewed = coord_obj.create_element("REVIEWED", "human code review passed", ("initial",))
    e_verified = coord_obj.create_element("VERIFIED", "z3 SMT discharge", ("initial",))

    # Meet: VERIFIED ⊓ REVIEWED = REVIEWED
    e_meet = coord_obj.apply_meet(e_verified, e_reviewed)
    assert e_meet.tier == TrustTier.REVIEWED, f"Expected REVIEWED, got {e_meet.tier}"

    # Join: REVIEWED ⊔ VERIFIED = VERIFIED
    e_join = coord_obj.apply_join(e_reviewed, e_verified)
    assert e_join.tier == TrustTier.VERIFIED, f"Expected VERIFIED, got {e_join.tier}"

    # Compose: PROPOSAL ⊕ PROPOSAL = PROPOSAL (oracle ceiling)
    e_comp = coord_obj.apply_compose(e_proposal, e_proposal)
    assert e_comp.tier == TrustTier.PROPOSAL, f"Oracle ceiling violated: {e_comp.tier}"

    # Compose: VERIFIED ⊕ REVIEWED = REVIEWED (conservative)
    e_comp2 = coord_obj.apply_compose(e_verified, e_reviewed)
    assert e_comp2.tier == TrustTier.REVIEWED, f"Expected REVIEWED, got {e_comp2.tier}"

    # Attenuate: VERIFIED ⊖ = REVIEWED
    e_att = coord_obj.apply_attenuate(e_verified)
    assert e_att.tier == TrustTier.REVIEWED, f"Expected REVIEWED, got {e_att.tier}"

    # Lift with policy: REVIEWED ↑ → VERIFIED
    e_lifted, success = coord_obj.apply_lift(e_reviewed, "z3_solver_discharge", "z3 proof found")
    assert success, "Lift should succeed with valid policy"
    assert e_lifted.tier == TrustTier.VERIFIED, f"Expected VERIFIED after lift, got {e_lifted.tier}"

    # Silent promotion refused
    e_refused, success2 = coord_obj.apply_lift(e_reviewed, "unknown_policy", "attempt silent promotion")
    assert not success2, "Lift should be refused for unregistered policy"

    # Lower with challenge
    e_lowered = coord_obj.apply_lower(e_verified, "reviewer_42", "contradicting evidence found")
    assert e_lowered.tier == TrustTier.REVIEWED, f"Expected REVIEWED after lower, got {e_lowered.tier}"

    # Produce witness
    witness = coord_obj.produce_witness()
    errors = witness.validate()
    assert errors == [], f"Witness errors: {errors}"
    print(witness.summary())

    # Verify all invariants
    all_violations = coord_obj.validate()
    assert all_violations == [], f"Invariant violations: {all_violations}"

    # Roundtrip witness
    d = witness.to_dict()
    w2 = TrustOrderedAlgebraAdmissibleWitness.from_dict(d)
    assert w2.witness_id == witness.witness_id

    # Merge
    w3 = witness.merge(w2)
    assert w3.operation_count == witness.operation_count * 2

    # Analyzer
    analyzer = TrustOrderedAlgebraAdmissibleAnalyzer(
        witnesses=[witness], algebra=coord_obj.algebra
    )
    print(analyzer.report())
    score = analyzer.score()
    assert 0.0 <= score <= 1.0, f"Score out of range: {score}"

    # TrustBound tests
    bound = TrustBound(floor=TrustTier.REVIEWED, ceiling=TrustTier.VERIFIED)
    assert bound.permits(e_reviewed)
    assert not bound.permits(e_proposal)
    clamped = bound.clamp(e_proposal)
    assert clamped.tier == TrustTier.REVIEWED

    # AdmissibleSupport
    support = AdmissibleSupport.from_elements(
        coord_id="ctx_001",
        elements=[e_reviewed, e_verified],
        channel_tag="z3",
    )
    assert support.aggregate_tier == TrustTier.REVIEWED  # meet of REVIEWED+VERIFIED
    assert support.is_admissible()

    print("\n[PASS] All smoke tests passed.")
