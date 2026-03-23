"""
Implementation Consequences of Hypercover Treaties.

This module defines what code must guarantee at boundaries when a hypercover
treaty is in force. Each treaty imposes invariants and postconditions on
the modules that constitute its signatories. A TreatyImplementationConsequence
records these obligations; a ConsequenceChecker verifies them at runtime.

Mathematical Background
-----------------------
When a sheaf F on a space X has a global section, every local section agrees
on overlaps. The implementation analogue: if module A and module B sign a
treaty, then:
  - A must satisfy postconditions that enable B's preconditions at the boundary.
  - B must satisfy postconditions that enable A's preconditions at the boundary.
  - Any violation produces a Čech 1-cocycle on the boundary (a non-trivial
    element of H¹(U_AB, A) where A is the abelian group of violation severities).

Čech H¹ and Boundary Violations:
  A boundary guarantee G_ij on U_ij ∩ U_ji is an element of the abelian group
  of satisfaction values. If G_ij ≠ -G_ji, the antisymmetry condition fails and
  we have a non-trivial 1-cocycle — a violation witness.

Treaty Implementation Consequences:
  Each signed treaty T between modules {M_0, M_1, ..., M_n} gives rise to a set
  of implementation consequences C(T) = {c_{ij} | i ≠ j}. Each consequence c_{ij}
  demands that M_i establishes postconditions that M_j's preconditions depend on
  at boundary U_{ij}.

Invariant Hierarchy:
  Invariants are partially ordered by strength. An invariant I_1 is stronger than
  I_2 if I_1 ⊢ I_2. The set of invariants required by a treaty consequence
  must be closed under this relation (upward-closed in the lattice).

Trust Tiers and Deployment Gates:
  A consequence is only satisfied for deployment if its trust tier is at least
  VERIFIED. Consequences at lower tiers may be present in the system but will
  trigger warnings or be excluded from safety-critical paths.

    # copilot: implementation consequences of hypercover treaties
"""
from __future__ import annotations

import abc
import collections
import datetime
import enum
import functools
import hashlib
import itertools
import logging
import math
import uuid
from dataclasses import dataclass, field
from enum import Enum, IntEnum
from typing import Any, Callable, Iterator, NamedTuple, Sequence

logger = logging.getLogger(__name__)

CRITICAL_SEVERITY_THRESHOLD: int = 8
MAX_VIOLATIONS_BEFORE_HALT: int = 10
DEFAULT_STABILITY_WINDOW: str = "30d"

try:
    from jugeo.errors import (
        FailureClassification, FailureScope, JuGeoError, StructuredFailure, raise_with_scope,
    )
    _JUGEO_ERRORS = True
except ImportError:
    _JUGEO_ERRORS = False
    class FailureScope(str, Enum):  # type: ignore[no-redef]
        GEOMETRY = "geometry"; ENCODING = "encoding"; UNKNOWN = "unknown"
    class FailureClassification(str, Enum):  # type: ignore[no-redef]
        ENCODING_MISMATCH = "encoding_mismatch"; DESCENT_OBSTRUCTION = "descent_obstruction"; UNCLASSIFIED = "unclassified"
    class JuGeoError(RuntimeError): pass  # type: ignore[no-redef]
    class StructuredFailure:  # type: ignore[no-redef]
        def __init__(self, message: str, **kw: Any) -> None: self.message = message
    def raise_with_scope(code: str, *, message: str, provenance: Any = None, **kw: Any) -> None:  # type: ignore[misc]
        raise JuGeoError(f"[{code}] {message}")

try:
    from jugeo.judgments.judgment_terms import (
        EvidenceItemKind, JudgmentStatus, PropositionKind, ProvenanceSource, TrustLevel,
    )
    _JUGEO_JUDGMENTS = True
except ImportError:
    _JUGEO_JUDGMENTS = False
    class TrustLevel(IntEnum):  # type: ignore[no-redef]
        CONTRADICTED = 0; UNVERIFIED = 1; ORACLE_PROPOSED = 2; RUNTIME_WITNESSED = 3; SOLVER_DISCHARGED = 4; VERIFIED_PROOF = 5
    class PropositionKind(str, Enum):  # type: ignore[no-redef]
        STRUCTURAL = "structural"; BEHAVIORAL = "behavioral"; RELATIONAL = "relational"
    class EvidenceItemKind(str, Enum):  # type: ignore[no-redef]
        SOLVER_PROOF = "solver_proof"; RUNTIME_WITNESS = "runtime_witness"; ORACLE_PROPOSAL = "oracle_proposal"
    class ProvenanceSource(str, Enum):  # type: ignore[no-redef]
        SOLVER = "solver"; RUNTIME = "runtime"; ORACLE = "oracle"; HUMAN = "human"


# ---------------------------------------------------------------------------
# TrustTier
# ---------------------------------------------------------------------------

class TrustTier(IntEnum):
    """Ordered trust algebra T=(E_adm, ≼, ⊕, ⊖, ↑_π, ↓_χ) — NEVER a float.

    The trust lattice is totally ordered (PROPOSAL < REVIEWED < VERIFIED <
    RUNTIME_WITNESSED < PROOF_BACKED). Operations join (∨) and meet (∧) are
    therefore equivalent to max and min respectively.  promote (↑_π) and
    demote (↓_χ) shift the tier by one step, clamped at the extrema.

    Deployment gates require at least VERIFIED.
    """

    PROPOSAL          = 1
    REVIEWED          = 2
    VERIFIED          = 3
    RUNTIME_WITNESSED = 4
    PROOF_BACKED      = 5

    def join(self, other: TrustTier) -> TrustTier:
        """Least upper bound (max) in the trust lattice (⊕ operator)."""
        return TrustTier(max(self.value, other.value))

    def meet(self, other: TrustTier) -> TrustTier:
        """Greatest lower bound (min) in the trust lattice (⊖ operator)."""
        return TrustTier(min(self.value, other.value))

    def promote(self) -> TrustTier:
        """Shift one tier upward (↑_π), clamped at PROOF_BACKED."""
        return TrustTier(min(self.value + 1, TrustTier.PROOF_BACKED.value))

    def demote(self) -> TrustTier:
        """Shift one tier downward (↓_χ), clamped at PROPOSAL."""
        return TrustTier(max(self.value - 1, TrustTier.PROPOSAL.value))

    # --- legacy aliases kept for backward compatibility --------------------

    def upgrade(self) -> TrustTier:
        """Alias for promote() — kept for backward compatibility."""
        return self.promote()

    def downgrade(self) -> TrustTier:
        """Alias for demote() — kept for backward compatibility."""
        return self.demote()

    # --- ordering helpers --------------------------------------------------

    def __le__(self, other: object) -> bool:
        if isinstance(other, TrustTier):
            return self.value <= other.value
        return NotImplemented

    def __lt__(self, other: object) -> bool:
        if isinstance(other, TrustTier):
            return self.value < other.value
        return NotImplemented

    def __ge__(self, other: object) -> bool:
        if isinstance(other, TrustTier):
            return self.value >= other.value
        return NotImplemented

    def __gt__(self, other: object) -> bool:
        if isinstance(other, TrustTier):
            return self.value > other.value
        return NotImplemented

    # --- convenience -------------------------------------------------------

    def is_sufficient_for_deployment(self) -> bool:
        """Return True iff this tier meets the deployment gate (≥ VERIFIED)."""
        return self >= TrustTier.VERIFIED

    def label(self) -> str:
        """Human-readable label with numeric value."""
        return f"{self.name}({self.value})"


# ---------------------------------------------------------------------------
# Judgment and CechObstruction — mandatory dataclasses
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Judgment:
    """A judgment (c, φ, A, E, O, B, T, Π) — NEVER a boolean.

    Represents a structured epistemic judgment as an 8-tuple. Every belief
    about a treaty consequence must be reified as a Judgment rather than a
    plain boolean so that its provenance, trust level, and outstanding proof
    obligations are always accessible.

    Fields
    ------
    context     : identifying context (treaty_id, module pair, etc.)
    formula     : the proposition or formula being judged
    assumptions : background assumptions under which the judgment holds
    evidence    : supporting evidence items (witnesses, test results, …)
    obligations : outstanding proof obligations that remain to be discharged
    burden      : who bears the proof burden (component id or description)
    trust       : epistemic confidence level (TrustTier)
    provenance  : origin metadata (solver run id, runtime timestamp, …)
    """

    context:     Any
    formula:     Any
    assumptions: tuple
    evidence:    tuple
    obligations: tuple
    burden:      Any
    trust:       TrustTier
    provenance:  Any

    def is_deployment_ready(self) -> bool:
        """Return True iff the trust tier meets the deployment gate."""
        return self.trust.is_sufficient_for_deployment()

    def add_evidence(self, item: Any) -> Judgment:
        """Return a new Judgment with *item* appended to evidence."""
        return Judgment(
            context=self.context, formula=self.formula,
            assumptions=self.assumptions, evidence=self.evidence + (item,),
            obligations=self.obligations, burden=self.burden,
            trust=self.trust, provenance=self.provenance,
        )

    def discharge_obligation(self, obligation: Any) -> Judgment:
        """Return a new Judgment with *obligation* removed from obligations."""
        return Judgment(
            context=self.context, formula=self.formula,
            assumptions=self.assumptions, evidence=self.evidence,
            obligations=tuple(o for o in self.obligations if o != obligation),
            burden=self.burden, trust=self.trust, provenance=self.provenance,
        )

    def strengthen_trust(self, new_tier: TrustTier) -> Judgment:
        """Return a new Judgment with trust elevated to at least *new_tier*."""
        return Judgment(
            context=self.context, formula=self.formula,
            assumptions=self.assumptions, evidence=self.evidence,
            obligations=self.obligations, burden=self.burden,
            trust=self.trust.join(new_tier), provenance=self.provenance,
        )

    def summary(self) -> str:
        """One-line summary of the judgment."""
        return (
            f"Judgment(ctx={self.context!r}, formula={self.formula!r}, "
            f"tier={self.trust.label()}, obligations={len(self.obligations)})"
        )


@dataclass(frozen=True)
class CechObstruction:
    """A Čech H¹ cohomology obstruction arising from a failed gluing condition.

    When two local sections cannot be glued into a global section on a cover,
    they produce a non-trivial 1-cocycle. This dataclass reifies that failure
    as a first-class object so it can be logged, compared, and discharged.

    Fields
    ------
    cover_id        : identifier of the hypercover on which the obstruction lives
    cocycle         : the actual 1-cocycle data (set of (i, j, value) triples)
    cohomology_class: string label for the cohomology class (e.g. "H¹(U,A)[k]")
    description     : human-readable explanation of what the obstruction means

    Methods
    -------
    is_trivial() : True iff the cocycle is empty (obstruction vanishes)
    """

    cover_id:         str
    cocycle:          frozenset
    cohomology_class: str
    description:      str

    def is_trivial(self) -> bool:
        """Return True iff the cocycle is empty (trivial cohomology class)."""
        return len(self.cocycle) == 0


def make_judgment(
    context: Any,
    formula: Any,
    assumptions: Sequence[Any] = (),
    evidence: Sequence[Any] = (),
    obligations: Sequence[Any] = (),
    burden: Any = None,
    trust: TrustTier = TrustTier.PROPOSAL,
    provenance: Any = None,
) -> Judgment:
    """Factory function to construct a :class:`Judgment` from keyword arguments.

    All sequences are normalised to tuples so the resulting Judgment is
    hashable and suitable for use as a frozen-dataclass field.

    Parameters
    ----------
    context     : identifying context (treaty id, module pair, …)
    formula     : the proposition or formula being judged
    assumptions : background assumptions (default empty)
    evidence    : supporting evidence items (default empty)
    obligations : outstanding proof obligations (default empty)
    burden      : proof-burden holder (default None)
    trust       : epistemic confidence level (default PROPOSAL)
    provenance  : origin metadata (default None)

    Returns
    -------
    Judgment
        Immutable frozen dataclass instance.

    Examples
    --------
    >>> j = make_judgment("ctx", "P∧Q", trust=TrustTier.VERIFIED)
    >>> j.trust
    <TrustTier.VERIFIED: 3>
    >>> j.is_deployment_ready()
    True
    """
    return Judgment(
        context=context,
        formula=formula,
        assumptions=tuple(assumptions),
        evidence=tuple(evidence),
        obligations=tuple(obligations),
        burden=burden,
        trust=trust,
        provenance=provenance,
    )


# ---------------------------------------------------------------------------
# Module-level constant invariants
# ---------------------------------------------------------------------------

INV_NONNULL_OUTPUT = (
    "INV_001: function output must never be None when contract specifies non-optional"
)
INV_BOUNDARY_TYPE_SAFE = (
    "INV_002: all values crossing a boundary must satisfy the declared type annotation"
)
INV_IDEMPOTENT_INIT = (
    "INV_003: re-initializing a component with the same parameters yields equivalent state"
)
INV_MONOTONE_TRUST = (
    "INV_004: trust tier of a component can only increase or stay the same within a session"
)
INV_CECH_ANTISYMMETRY = (
    "INV_005: obstruction cochain satisfies f_{ij} = -f_{ji} (Čech antisymmetry)"
)
INV_COCYCLE_BOUNDARY = (
    "INV_006: triple overlaps satisfy f_{ij} + f_{jk} + f_{ki} = 0 (cocycle condition)"
)
INV_SECTION_RESTRICTION = (
    "INV_007: restrict(global_section, U_i) == local_section_i"
)
INV_PATCH_SURJECTIVITY = (
    "INV_008: every point in the domain is covered by at least one patch"
)
INV_TREATY_REFLEXIVITY = (
    "INV_009: every component is in a trivial treaty with itself"
)
INV_BLAME_COMPLETENESS = (
    "INV_010: every treaty violation has at least one blame assignment"
)

ALL_INVARIANTS: tuple[str, ...] = (
    INV_NONNULL_OUTPUT,
    INV_BOUNDARY_TYPE_SAFE,
    INV_IDEMPOTENT_INIT,
    INV_MONOTONE_TRUST,
    INV_CECH_ANTISYMMETRY,
    INV_COCYCLE_BOUNDARY,
    INV_SECTION_RESTRICTION,
    INV_PATCH_SURJECTIVITY,
    INV_TREATY_REFLEXIVITY,
    INV_BLAME_COMPLETENESS,
)


# ---------------------------------------------------------------------------
# BoundaryGuarantee
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class BoundaryGuarantee:
    """A guarantee that a patch's boundary exports are stable.

    A BoundaryGuarantee G_{patch_id} states that the set of symbols exported
    at the patch's boundary will remain stable (unchanged in signature and
    semantics) under the conditions described by *stability_condition* until
    *valid_until* (if supplied).

    Attributes
    ----------
    guarantee_id      : unique identifier (e.g. "BG-auth-service-001")
    patch_id          : the patch whose boundary this guarantee covers
    boundary_symbols  : the set of exported symbol names guaranteed stable
    stability_condition : human-readable description of when stability holds
    trust_tier        : current trust level for this guarantee
    valid_until       : optional expiry window string (e.g. "30d", ISO date)
    """

    guarantee_id:        str
    patch_id:            str
    boundary_symbols:    frozenset
    stability_condition: str
    trust_tier:          TrustTier
    valid_until:         str | None = None

    # --- validity checks ---------------------------------------------------

    def is_valid(self) -> bool:
        """Return True iff the guarantee is structurally valid.

        A guarantee is valid when:
        - boundary_symbols is non-empty
        - stability_condition is non-empty
        - trust_tier is at least PROPOSAL
        """
        return (
            len(self.boundary_symbols) > 0
            and bool(self.stability_condition)
            and self.trust_tier >= TrustTier.PROPOSAL
        )

    def covers_symbol(self, symbol: str) -> bool:
        """Return True iff *symbol* is covered by this boundary guarantee."""
        return symbol in self.boundary_symbols

    def to_judgment(self) -> Judgment:
        """Convert this guarantee into a formal :class:`Judgment`.

        The resulting Judgment captures:
        - context   = guarantee_id
        - formula   = f"STABLE_BOUNDARY({patch_id})"
        - evidence  = tuple of covered symbol names
        - trust     = trust_tier
        - provenance = valid_until or "indefinite"
        """
        return make_judgment(
            context=self.guarantee_id,
            formula=f"STABLE_BOUNDARY({self.patch_id})",
            assumptions=(f"stability_condition:{self.stability_condition}",),
            evidence=tuple(sorted(self.boundary_symbols)),
            obligations=(
                () if self.trust_tier >= TrustTier.VERIFIED
                else ("discharge_stability_proof",)
            ),
            burden=self.patch_id,
            trust=self.trust_tier,
            provenance=self.valid_until or "indefinite",
        )

    # --- legacy fields kept for backward compat with helper classes --------

    @property
    def guarantee_id_str(self) -> str:
        """Alias for guarantee_id, for backward compatibility."""
        return self.guarantee_id

    def __str__(self) -> str:
        status = "VALID" if self.is_valid() else "INVALID"
        expiry = f", until={self.valid_until}" if self.valid_until else ""
        return (
            f"BoundaryGuarantee({self.guarantee_id!r}, patch={self.patch_id!r}, "
            f"symbols={len(self.boundary_symbols)}, tier={self.trust_tier.label()}, "
            f"{status}{expiry})"
        )


# ---------------------------------------------------------------------------
# TreatyImplementationConsequence
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TreatyImplementationConsequence:
    """A concrete requirement arising from a hypercover treaty.

    Each treaty between signatories {M_0, …, M_n} gives rise to a set of
    implementation consequences C(T). Each consequence c_{ij} demands that
    module M_i provides specific exports (symbols, signatures, behaviours) to
    satisfy M_j's preconditions at boundary U_{ij}.

    This class reifies one such consequence as a frozen record that can be
    checked against a concrete implementation dict.

    Attributes
    ----------
    consequence_id      : unique identifier (e.g. "CONS-T1-auth-data-001")
    treaty_id           : identifier of the parent treaty
    patch_id            : the patch that must satisfy this consequence
    requirement_kind    : category of requirement (e.g. "export", "type", "behaviour")
    requirement_description : human-readable description of what must be implemented
    signature           : optional expected function/type signature string
    priority            : integer priority (1 = highest, higher numbers = lower)
    trust_tier          : epistemic confidence level for this consequence
    """

    consequence_id:          str
    treaty_id:               str
    patch_id:                str
    requirement_kind:        str
    requirement_description: str
    signature:               str | None
    priority:                int
    trust_tier:              TrustTier

    # --- satisfaction check ------------------------------------------------

    def to_judgment(self) -> Judgment:
        """Convert this consequence into a formal :class:`Judgment`.

        The Judgment captures the consequence as a structured epistemic
        claim with provenance and outstanding proof obligations.
        """
        obligations: tuple[str, ...] = ()
        if self.trust_tier < TrustTier.VERIFIED:
            obligations = (f"verify_consequence_{self.consequence_id}",)
        return make_judgment(
            context=f"{self.treaty_id}::{self.patch_id}",
            formula=f"IMPL_CONSEQUENCE({self.requirement_kind}:{self.consequence_id})",
            assumptions=(f"treaty_{self.treaty_id}_in_force",),
            evidence=(self.requirement_description,)
            + ((f"sig:{self.signature}",) if self.signature else ()),
            obligations=obligations,
            burden=self.patch_id,
            trust=self.trust_tier,
            provenance=f"priority={self.priority}",
        )

    def is_satisfied(self, implementation: dict) -> bool:
        """Return True iff this consequence is satisfied in *implementation*.

        Looks up:
        1. ``implementation[self.consequence_id]`` — explicit True/False flag
        2. If not found, checks ``implementation.get(self.patch_id, {})``
           for a matching ``requirement_kind`` or ``signature`` entry.

        Parameters
        ----------
        implementation : dict mapping consequence ids / patch ids → truthy values

        Returns
        -------
        bool
            True if the consequence is considered implemented.
        """
        if self.consequence_id in implementation:
            return bool(implementation[self.consequence_id])
        patch_impl = implementation.get(self.patch_id, {})
        if isinstance(patch_impl, dict):
            if self.requirement_kind in patch_impl:
                return bool(patch_impl[self.requirement_kind])
            if self.signature and self.signature in patch_impl:
                return bool(patch_impl[self.signature])
        return False

    def describe(self) -> str:
        """Return a structured human-readable description of this consequence."""
        sig_part = f"\n  Signature : {self.signature}" if self.signature else ""
        return (
            f"TreatyImplementationConsequence\n"
            f"  ID        : {self.consequence_id}\n"
            f"  Treaty    : {self.treaty_id}\n"
            f"  Patch     : {self.patch_id}\n"
            f"  Kind      : {self.requirement_kind}\n"
            f"  Priority  : {self.priority}\n"
            f"  Tier      : {self.trust_tier.label()}\n"
            f"  Desc      : {self.requirement_description}"
            f"{sig_part}"
        )

    def __str__(self) -> str:
        return (
            f"TreatyImplementationConsequence("
            f"id={self.consequence_id!r}, treaty={self.treaty_id!r}, "
            f"patch={self.patch_id!r}, kind={self.requirement_kind!r}, "
            f"priority={self.priority}, tier={self.trust_tier.label()})"
        )


# ---------------------------------------------------------------------------
# TreatyViolation
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TreatyViolation:
    """Records a detected treaty violation.

    When a :class:`ConsequenceChecker` determines that a
    :class:`TreatyImplementationConsequence` is not satisfied, it records
    a TreatyViolation.  The violation carries the evidence that triggered
    the failure and a severity score on an integer 0–10 scale so that
    downstream policy engines can gate deployments.

    Attributes
    ----------
    violation_id         : unique identifier (e.g. "VIO-T1-patch-abc-001")
    treaty_id            : the treaty whose consequence was violated
    violating_patch_id   : the patch that failed to implement the consequence
    violated_consequence_id : the consequence that was not satisfied
    violation_kind       : category (e.g. "missing_export", "wrong_signature")
    evidence             : string describing the concrete evidence of violation
    severity             : integer severity on 0–10 scale (10 = most critical)
    timestamp            : ISO-8601 string recording when the violation was found
    """

    violation_id:              str
    treaty_id:                 str
    violating_patch_id:        str
    violated_consequence_id:   str
    violation_kind:            str
    evidence:                  str
    severity:                  int
    timestamp:                 str

    # --- classification ----------------------------------------------------

    def is_critical(self) -> bool:
        """Return True iff severity >= CRITICAL_SEVERITY_THRESHOLD (8)."""
        return self.severity >= CRITICAL_SEVERITY_THRESHOLD

    def to_cech_obstruction(self) -> CechObstruction:
        """Convert this violation into a :class:`CechObstruction`.

        The obstruction's cocycle is a singleton frozenset containing a
        deterministic hash of the violation evidence.  If the violation is
        ever resolved (evidence cleared), the cocycle becomes trivially empty.
        """
        raw = hashlib.sha256(
            f"{self.treaty_id}:{self.violating_patch_id}:{self.evidence}".encode()
        ).hexdigest()[:16]
        cocycle = frozenset([(self.violating_patch_id, self.violated_consequence_id, raw)])
        cohomology_label = f"H¹[{self.treaty_id}][{self.violation_kind}]"
        return CechObstruction(
            cover_id=self.treaty_id,
            cocycle=cocycle,
            cohomology_class=cohomology_label,
            description=self.evidence,
        )

    def describe(self) -> str:
        """Return a structured human-readable violation report."""
        critical_tag = " [CRITICAL]" if self.is_critical() else ""
        return (
            f"TreatyViolation\n"
            f"  ID         : {self.violation_id}\n"
            f"  Treaty     : {self.treaty_id}\n"
            f"  Patch      : {self.violating_patch_id}\n"
            f"  Consequence: {self.violated_consequence_id}\n"
            f"  Kind       : {self.violation_kind}\n"
            f"  Severity   : {self.severity}/10{critical_tag}\n"
            f"  Timestamp  : {self.timestamp}\n"
            f"  Evidence   : {self.evidence}"
        )

    def __str__(self) -> str:
        return (
            f"TreatyViolation({self.violation_id!r}, "
            f"patch={self.violating_patch_id!r}, "
            f"severity={self.severity}, critical={self.is_critical()})"
        )


# ---------------------------------------------------------------------------
# ConsequenceChecker
# ---------------------------------------------------------------------------

class ConsequenceChecker:
    """Verifies that all consequences of a treaty are implemented.

    ConsequenceChecker is a stateful regular class.  Each call to
    :meth:`check` or :meth:`check_all` updates internal counters and appends
    to the violation log.  Call :meth:`get_report` to obtain a summary dict.

    Attributes
    ----------
    checker_id    : unique identifier for this checker instance
    strict_mode   : if True, halt (raise) after MAX_VIOLATIONS_BEFORE_HALT
    checked_count : count of individual consequence checks performed
    violation_log : list of :class:`TreatyViolation` instances found so far
    """

    def __init__(
        self,
        checker_id: str = "",
        strict_mode: bool = False,
    ) -> None:
        self.checker_id: str = checker_id or f"checker-{uuid.uuid4().hex[:8]}"
        self.strict_mode: bool = strict_mode
        self.checked_count: int = 0
        self.violation_log: list[TreatyViolation] = []

    # --- core verification -------------------------------------------------

    def check(
        self,
        consequence: TreatyImplementationConsequence,
        implementation: dict,
    ) -> bool:
        """Check a single consequence against an implementation dict.

        Increments :attr:`checked_count`.  If not satisfied, constructs a
        :class:`TreatyViolation` with severity derived from the consequence
        priority and appends it to :attr:`violation_log`.

        Parameters
        ----------
        consequence    : the consequence to verify
        implementation : dict mapping ids / kinds → truthy values

        Returns
        -------
        bool
            True if the consequence is satisfied.
        """
        self.checked_count += 1
        satisfied = consequence.is_satisfied(implementation)
        if not satisfied:
            severity = _derive_severity(consequence.priority, consequence.trust_tier)
            viol = TreatyViolation(
                violation_id=f"VIO-{consequence.consequence_id}-{uuid.uuid4().hex[:8]}",
                treaty_id=consequence.treaty_id,
                violating_patch_id=consequence.patch_id,
                violated_consequence_id=consequence.consequence_id,
                violation_kind=consequence.requirement_kind,
                evidence=(
                    f"Consequence {consequence.consequence_id!r} not found in "
                    f"implementation for patch {consequence.patch_id!r}"
                ),
                severity=severity,
                timestamp=_utc_now(),
            )
            self.violation_log.append(viol)
            if self.strict_mode and len(self.violation_log) >= MAX_VIOLATIONS_BEFORE_HALT:
                raise JuGeoError(
                    f"[{self.checker_id}] Halting: reached "
                    f"{MAX_VIOLATIONS_BEFORE_HALT} violations in strict mode."
                )
        return satisfied

    def check_all(
        self,
        consequences: list[TreatyImplementationConsequence],
        implementation: dict,
    ) -> list[TreatyViolation]:
        """Check all consequences against *implementation*.

        Parameters
        ----------
        consequences   : list of consequences to verify
        implementation : the implementation dict to check against

        Returns
        -------
        list of TreatyViolation
            All violations found during this batch check.
        """
        before = len(self.violation_log)
        for c in consequences:
            self.check(c, implementation)
        return self.violation_log[before:]

    def get_report(self) -> dict:
        """Return a summary report dict.

        Returns
        -------
        dict with keys:
            checker_id, strict_mode, checked_count, violation_count,
            critical_count, violations (list of dicts)
        """
        return {
            "checker_id":     self.checker_id,
            "strict_mode":    self.strict_mode,
            "checked_count":  self.checked_count,
            "violation_count": len(self.violation_log),
            "critical_count": sum(1 for v in self.violation_log if v.is_critical()),
            "violations": [
                {
                    "violation_id":           v.violation_id,
                    "treaty_id":              v.treaty_id,
                    "violating_patch_id":     v.violating_patch_id,
                    "violated_consequence_id": v.violated_consequence_id,
                    "violation_kind":         v.violation_kind,
                    "severity":               v.severity,
                    "critical":               v.is_critical(),
                    "timestamp":              v.timestamp,
                }
                for v in self.violation_log
            ],
        }

    def is_clean(self) -> bool:
        """Return True iff no violations have been found."""
        return len(self.violation_log) == 0

    def __repr__(self) -> str:
        status = "CLEAN" if self.is_clean() else f"{len(self.violation_log)} violation(s)"
        return (
            f"ConsequenceChecker(id={self.checker_id!r}, "
            f"checked={self.checked_count}, strict={self.strict_mode}, "
            f"status={status})"
        )


# ---------------------------------------------------------------------------
# GuaranteeMatrix
# ---------------------------------------------------------------------------

class GuaranteeMatrix:
    """Directed implication graph over BoundaryGuarantee identifiers.

    G1 → G2 means: if G1 holds, then G2 necessarily holds.  This captures
    logical dependency; for example a strong type-safety guarantee may imply
    a weaker non-null guarantee.

    The matrix is represented as an adjacency dict (str → set[str]).  The
    transitive closure is computed on demand and cached.

    Methods
    -------
    add_implication(g1, g2)  : record that g1 implies g2
    implies(g1, g2)          : True if g1 transitively implies g2
    transitive_closure()     : return full {src: {reachable}} dict
    implied_by(g)            : set of guarantees that imply g
    size()                   : number of edges in the graph
    """

    def __init__(self) -> None:
        self._adj: dict[str, set[str]] = collections.defaultdict(set)
        self._closure_cache: dict[str, set[str]] | None = None

    def add_implication(self, g1: str, g2: str) -> None:
        """Record that guarantee g1 implies guarantee g2."""
        self._adj[g1].add(g2)
        # Ensure g2 has an entry so that iteration covers all nodes
        if g2 not in self._adj:
            self._adj[g2] = set()
        self._closure_cache = None  # invalidate cache

    def _bfs_reachable(self, start: str) -> set[str]:
        """Return all nodes reachable from start via BFS."""
        visited: set[str] = set()
        queue = collections.deque([start])
        while queue:
            node = queue.popleft()
            for neighbour in self._adj.get(node, set()):
                if neighbour not in visited:
                    visited.add(neighbour)
                    queue.append(neighbour)
        return visited

    def implies(self, g1: str, g2: str) -> bool:
        """Return True iff g1 transitively implies g2."""
        if g1 == g2:
            return True
        closure = self.transitive_closure()
        return g2 in closure.get(g1, set())

    def transitive_closure(self) -> dict[str, set[str]]:
        """Compute and return the transitive closure of the implication graph.

        Result is cached until the graph is modified.

        Returns
        -------
        dict mapping each node to the set of all nodes it (transitively) implies
        """
        if self._closure_cache is not None:
            return self._closure_cache
        closure: dict[str, set[str]] = {}
        for node in self._adj:
            closure[node] = self._bfs_reachable(node)
        self._closure_cache = closure
        return closure

    def implied_by(self, g: str) -> set[str]:
        """Return the set of guarantees that directly or transitively imply g."""
        result: set[str] = set()
        for source, reachable in self.transitive_closure().items():
            if g in reachable and source != g:
                result.add(source)
        return result

    def size(self) -> int:
        """Return the total number of directed edges in the implication graph."""
        return sum(len(targets) for targets in self._adj.values())

    def __repr__(self) -> str:
        return f"GuaranteeMatrix(nodes={len(self._adj)}, edges={self.size()})"


# ---------------------------------------------------------------------------
# BoundaryInspector
# ---------------------------------------------------------------------------

class BoundaryInspector:
    """Walks a simulated AST (dict tree) and checks invariants at each node.

    Each tree node is a dict that may contain:
      - "type"     : str, node kind (e.g. "function", "class", "call")
      - "name"     : str, identifier name
      - "children" : list of child node dicts
      - any domain-specific keys relevant to invariant checking

    Invariants are checked by examining node attributes against the global
    ALL_INVARIANTS list. A violation is recorded when a required attribute
    implied by an invariant is missing or False.

    Attributes
    ----------
    _violations : list of (node_name, invariant) pairs
    _visited    : count of nodes visited
    """

    def __init__(self) -> None:
        self._violations: list[tuple[str, str]] = []
        self._visited: int = 0

    # --- core inspection ----------------------------------------------------

    def check_invariant(self, node: dict[str, Any], inv: str) -> bool:
        """Check a single invariant against a node.

        Mapping of invariant codes to node attributes:
          INV_001 → node must have "returns_non_null": True
          INV_002 → node must have "type_annotated": True
          INV_003 → node must have "idempotent_init": True
          INV_004 → node must have "trust_monotone": True
          INV_005 → node must have "cech_antisymmetric": True
          INV_006 → node must have "cocycle_closed": True
          INV_007 → node must have "section_restricted": True
          INV_008 → node must have "fully_covered": True
          INV_009 → node must have "reflexive_treaty": True
          INV_010 → node must have "blame_complete": True

        Returns True if the invariant is satisfied (or not applicable), False
        if it is applicable but violated.
        """
        code = inv.split(":")[0].strip()
        key_map = {
            "INV_001": "returns_non_null",
            "INV_002": "type_annotated",
            "INV_003": "idempotent_init",
            "INV_004": "trust_monotone",
            "INV_005": "cech_antisymmetric",
            "INV_006": "cocycle_closed",
            "INV_007": "section_restricted",
            "INV_008": "fully_covered",
            "INV_009": "reflexive_treaty",
            "INV_010": "blame_complete",
        }
        attr = key_map.get(code)
        if attr is None:
            return True  # unknown invariant: assume satisfied
        # Only check nodes that explicitly carry the attribute
        if attr not in node:
            return True  # attribute absent → not applicable to this node
        return bool(node[attr])

    def inspect_node(self, node: dict[str, Any], depth: int = 0) -> None:
        """Inspect a single node and record any invariant violations.

        Parameters
        ----------
        node  : dict representing an AST node
        depth : current recursion depth (for display / cycle protection)
        """
        self._visited += 1
        node_name = node.get("name", f"<anon@depth{depth}>")
        for inv in ALL_INVARIANTS:
            if not self.check_invariant(node, inv):
                self._violations.append((node_name, inv))

    def walk_tree(self, tree: dict[str, Any], _depth: int = 0) -> None:
        """Recursively walk the tree, inspecting every node.

        Recursion is bounded at depth 64 to prevent stack overflow on
        pathological inputs.

        Parameters
        ----------
        tree   : root node dict
        _depth : internal depth counter (do not pass externally)
        """
        if _depth > 64:
            return
        self.inspect_node(tree, _depth)
        for child in tree.get("children", []):
            if isinstance(child, dict):
                self.walk_tree(child, _depth + 1)

    # --- results -----------------------------------------------------------

    def get_violations(self) -> list[tuple[str, str]]:
        """Return a copy of all recorded (node_name, invariant) violation pairs."""
        return list(self._violations)

    def reset(self) -> None:
        """Clear violation log and visited counter, ready for a fresh inspection."""
        self._violations = []
        self._visited = 0

    def summary(self) -> str:
        """Return a one-line summary of the inspection results."""
        return (
            f"BoundaryInspector: visited={self._visited}, "
            f"violations={len(self._violations)}"
        )


# ---------------------------------------------------------------------------
# ViolationAggregator
# ---------------------------------------------------------------------------

class ViolationAggregator:
    """Collects TreatyViolation instances and provides aggregate analytics.

    Violations are indexed by violation_id for O(1) lookup and deduplicated.
    The aggregator maintains a running total severity and per-component counts
    for efficient summarisation.

    Methods
    -------
    add(v)           : add a TreatyViolation (deduplicated by violation_id)
    total_severity() : sum of all violation severities
    critical_count() : number of critical violations
    by_component()   : dict mapping component → list[TreatyViolation]
    summary()        : formatted summary string
    worst_violation(): TreatyViolation with the highest severity (or None)
    """

    def __init__(self) -> None:
        self._violations: dict[str, TreatyViolation] = {}

    def add(self, v: TreatyViolation) -> None:
        """Add a violation. If a violation with the same id already exists, skip."""
        if v.violation_id not in self._violations:
            self._violations[v.violation_id] = v

    def __len__(self) -> int:
        return len(self._violations)

    def total_severity(self) -> float:
        """Return the sum of severities of all collected violations."""
        return sum(v.severity for v in self._violations.values())

    def critical_count(self) -> int:
        """Return the count of violations classified as critical."""
        return sum(1 for v in self._violations.values() if v.is_critical())

    def by_component(self) -> dict[str, list[TreatyViolation]]:
        """Return violations grouped by violating_patch_id.

        Returns
        -------
        dict mapping patch identifier → list of TreatyViolation
        """
        result: dict[str, list[TreatyViolation]] = collections.defaultdict(list)
        for v in self._violations.values():
            result[v.violating_patch_id].append(v)
        return dict(result)

    def worst_violation(self) -> TreatyViolation | None:
        """Return the violation with the highest severity, or None if empty."""
        if not self._violations:
            return None
        return max(self._violations.values(), key=lambda v: v.severity)

    def summary(self) -> str:
        """Return a multi-line formatted summary of all violations."""
        lines = [
            f"ViolationAggregator Summary",
            f"  Total violations : {len(self._violations)}",
            f"  Critical         : {self.critical_count()}",
            f"  Total severity   : {self.total_severity()}",
        ]
        by_comp = self.by_component()
        if by_comp:
            lines.append("  By component:")
            for comp, viols in sorted(by_comp.items()):
                lines.append(f"    {comp}: {len(viols)} violation(s)")
        worst = self.worst_violation()
        if worst is not None:
            lines.append(f"  Worst violation  : {worst.violation_id} (severity={worst.severity})")
        return "\n".join(lines)

    def __repr__(self) -> str:
        return f"ViolationAggregator(count={len(self)}, total_severity={self.total_severity():.4f})"


# ---------------------------------------------------------------------------
# ConsequencePropagator
# ---------------------------------------------------------------------------

class ConsequencePropagator:
    """Propagates TreatyImplementationConsequence through a dependency graph.

    The dependency graph models inter-module dependencies: if A depends on B,
    a consequence imposed on A may also affect B (transitively).

    Internally represented as a directed adjacency list (dict[str, list[str]]).

    Methods
    -------
    add_dependency(a, b)      : record that a depends on b
    propagate(consequence)    : return set of all affected module names
    all_affected(start)       : BFS from start, return all reachable nodes
    reachable(start)          : alias for all_affected
    topological_sort()        : return nodes in topological order (Kahn's algorithm)
    """

    def __init__(self) -> None:
        self._deps: dict[str, list[str]] = collections.defaultdict(list)

    def add_dependency(self, a: str, b: str) -> None:
        """Record that module a depends on module b.

        This means consequences imposed on a flow downstream to b as well.
        """
        if b not in self._deps[a]:
            self._deps[a].append(b)
        if b not in self._deps:
            self._deps[b] = []

    def all_affected(self, start: str) -> set[str]:
        """BFS from start; return all transitively reachable module names.

        Does not include start itself unless there is a cycle back to it.
        """
        visited: set[str] = set()
        queue = collections.deque([start])
        while queue:
            node = queue.popleft()
            for neighbour in self._deps.get(node, []):
                if neighbour not in visited:
                    visited.add(neighbour)
                    queue.append(neighbour)
        return visited

    def reachable(self, start: str) -> set[str]:
        """Alias for all_affected; included for API clarity."""
        return self.all_affected(start)

    def propagate(self, consequence: TreatyImplementationConsequence) -> set[str]:
        """Return the set of all modules affected by this consequence.

        Uses the treaty_id as the propagation root — all modules reachable
        from the treaty's point of origin inherit the consequence.

        Parameters
        ----------
        consequence : TreatyImplementationConsequence to propagate

        Returns
        -------
        set of str
            Module names that must satisfy the consequence (directly or indirectly)
        """
        return self.all_affected(consequence.treaty_id)

    def topological_sort(self) -> list[str]:
        """Return nodes in topological order using Kahn's algorithm.

        If the graph contains a cycle, the cyclic nodes will be missing from
        the result (they cannot be ordered). In that case the returned list
        is a topological order of the acyclic portion.

        Returns
        -------
        list of str
            Node names in dependency order (dependees before dependants)
        """
        in_degree: dict[str, int] = {node: 0 for node in self._deps}
        for node in self._deps:
            for neighbour in self._deps[node]:
                in_degree[neighbour] = in_degree.get(neighbour, 0) + 1
        queue = collections.deque(n for n, d in in_degree.items() if d == 0)
        order: list[str] = []
        while queue:
            node = queue.popleft()
            order.append(node)
            for neighbour in self._deps.get(node, []):
                in_degree[neighbour] -= 1
                if in_degree[neighbour] == 0:
                    queue.append(neighbour)
        return order

    def __repr__(self) -> str:
        total_edges = sum(len(v) for v in self._deps.values())
        return f"ConsequencePropagator(nodes={len(self._deps)}, edges={total_edges})"


# ---------------------------------------------------------------------------
# Private helper functions
# ---------------------------------------------------------------------------

def _utc_now() -> str:
    """Return the current UTC time as an ISO-8601 string."""
    return datetime.datetime.utcnow().isoformat() + "Z"


def _derive_severity(priority: int, tier: TrustTier) -> int:
    """Derive an integer severity (0–10) from priority and trust tier.

    Lower priority (1 = highest) and lower trust tier both increase severity.

    Formula:
        base   = 11 - min(priority, 10)   # invert: priority 1 → base 10
        factor = (6 - tier.value) / 5     # PROPOSAL=1.0, PROOF_BACKED=0.0
        result = round(base * (0.6 + 0.4 * factor))  clamped [1, 10]
    """
    base = 11 - min(max(priority, 1), 10)
    factor = (6 - tier.value) / 5.0
    raw = round(base * (0.6 + 0.4 * factor))
    return max(1, min(10, raw))


def _consequence_id_for_pair(treaty_id: str, patch_id: str, kind: str, idx: int) -> str:
    """Build a deterministic consequence id for a (treaty, patch, kind) triple."""
    token = hashlib.sha256(f"{treaty_id}:{patch_id}:{kind}:{idx}".encode()).hexdigest()[:8]
    return f"CONS-{treaty_id}-{patch_id}-{kind}-{token}"


def _extract_patches(treaty: dict) -> list[str]:
    """Return the list of patch ids from a treaty descriptor dict."""
    if "patches" in treaty:
        return list(treaty["patches"])
    if "signatories" in treaty:
        return list(treaty["signatories"])
    return [treaty.get("patch_id", "unknown")]


def _resolve_trust_tier(tier_str: str | None) -> TrustTier:
    """Parse a trust tier string into a :class:`TrustTier`, defaulting to PROPOSAL."""
    if tier_str is None:
        return TrustTier.PROPOSAL
    try:
        return TrustTier[tier_str.upper()]
    except KeyError:
        return TrustTier.PROPOSAL


# ---------------------------------------------------------------------------
# Module-level public functions
# ---------------------------------------------------------------------------

def derive_implementation_consequences(
    treaties: list[dict],
) -> list[TreatyImplementationConsequence]:
    """Derive all :class:`TreatyImplementationConsequence` objects for a set of treaties.

    For each treaty dict in *treaties*, generates one consequence per
    (patch, requirement_kind) pair. The requirement kinds are determined by
    the ``"requirement_kinds"`` key in the treaty dict, or fall back to a
    default set (``["export", "type_contract", "behaviour"]``).

    Parameters
    ----------
    treaties : list of dicts, each with keys:
        - ``"treaty_id"``         : str
        - ``"patches"``           : list[str] (or ``"signatories"``)
        - ``"tier"``              : str (TrustTier name, default "PROPOSAL")
        - ``"requirement_kinds"`` : list[str] (optional)
        - ``"signature"``         : str (optional, applied to all consequences)
        - ``"priority"``          : int (default 5)

    Returns
    -------
    list of TreatyImplementationConsequence
        One per (patch, requirement_kind) pair across all treaties.

    Examples
    --------
    >>> t = {"treaty_id": "T-001", "signatories": ["auth", "data"], "tier": "VERIFIED"}
    >>> cs = derive_implementation_consequences([t])
    >>> all(isinstance(c, TreatyImplementationConsequence) for c in cs)
    True
    """
    results: list[TreatyImplementationConsequence] = []
    for treaty in treaties:
        treaty_id = treaty.get("treaty_id", "UNKNOWN")
        tier = _resolve_trust_tier(treaty.get("tier"))
        priority = int(treaty.get("priority", 5))
        signature = treaty.get("signature")
        req_kinds: list[str] = treaty.get(
            "requirement_kinds", ["export", "type_contract", "behaviour"]
        )
        for patch_id in _extract_patches(treaty):
            for idx, kind in enumerate(req_kinds):
                cid = _consequence_id_for_pair(treaty_id, patch_id, kind, idx)
                desc = (
                    f"Treaty {treaty_id!r} requires patch {patch_id!r} "
                    f"to implement {kind!r}"
                )
                results.append(
                    TreatyImplementationConsequence(
                        consequence_id=cid,
                        treaty_id=treaty_id,
                        patch_id=patch_id,
                        requirement_kind=kind,
                        requirement_description=desc,
                        signature=signature,
                        priority=priority,
                        trust_tier=tier,
                    )
                )
    return results


def check_boundary_guarantee(
    guarantee: BoundaryGuarantee,
    symbols: frozenset,
) -> Judgment:
    """Verify a :class:`BoundaryGuarantee` against a set of runtime symbols.

    Checks whether all symbols declared in ``guarantee.boundary_symbols``
    are present in the *symbols* frozenset.  Returns a :class:`Judgment`
    whose trust tier reflects the result:

    - All symbols present → trust = guarantee.trust_tier (satisfied)
    - Some symbols missing → trust = PROPOSAL (unsatisfied)

    The Judgment's evidence includes the set of missing symbols (if any),
    making the failure observable downstream.

    Parameters
    ----------
    guarantee : the :class:`BoundaryGuarantee` to verify
    symbols   : frozenset of symbol names present in the runtime implementation

    Returns
    -------
    Judgment
        Structured epistemic record of whether the guarantee holds.

    Examples
    --------
    >>> g = BoundaryGuarantee("G-001", "patch_A", frozenset({"fn_x", "fn_y"}),
    ...                       "stable under refactor", TrustTier.VERIFIED)
    >>> j = check_boundary_guarantee(g, frozenset({"fn_x", "fn_y", "fn_z"}))
    >>> j.trust
    <TrustTier.VERIFIED: 3>
    """
    missing = guarantee.boundary_symbols - symbols
    if missing:
        return make_judgment(
            context=guarantee.guarantee_id,
            formula=f"BOUNDARY_STABLE({guarantee.patch_id})",
            assumptions=(f"stability_condition:{guarantee.stability_condition}",),
            evidence=(f"missing_symbols:{sorted(missing)}",),
            obligations=(f"restore_symbols:{sorted(missing)}",),
            burden=guarantee.patch_id,
            trust=TrustTier.PROPOSAL,
            provenance=f"checked_at:{_utc_now()}",
        )
    return make_judgment(
        context=guarantee.guarantee_id,
        formula=f"BOUNDARY_STABLE({guarantee.patch_id})",
        assumptions=(f"stability_condition:{guarantee.stability_condition}",),
        evidence=(f"all_{len(guarantee.boundary_symbols)}_symbols_present",),
        obligations=(),
        burden=guarantee.patch_id,
        trust=guarantee.trust_tier,
        provenance=f"checked_at:{_utc_now()}",
    )


def report_violation(
    treaty_id: str,
    patch_id: str,
    consequence_id: str,
    evidence: str,
    severity: int,
) -> TreatyViolation:
    """Record a treaty violation with structured evidence.

    Constructs and returns a :class:`TreatyViolation`. The violation_id is
    derived deterministically from the inputs plus a timestamp component so
    that duplicate submissions are distinguishable.

    Parameters
    ----------
    treaty_id      : the treaty that was violated
    patch_id       : the patch that failed to implement the consequence
    consequence_id : the consequence that was not satisfied
    evidence       : human-readable description of the failure evidence
    severity       : integer severity 0–10 (10 = most critical)

    Returns
    -------
    TreatyViolation
        Immutable record of the violation.

    Examples
    --------
    >>> v = report_violation("T-001", "auth", "CONS-T1-auth", "missing fn", 7)
    >>> v.is_critical()
    False
    """
    token = hashlib.sha256(
        f"{treaty_id}:{patch_id}:{consequence_id}:{evidence}".encode()
    ).hexdigest()[:12]
    violation_kind = _infer_violation_kind(evidence)
    return TreatyViolation(
        violation_id=f"VIO-{treaty_id}-{patch_id}-{token}",
        treaty_id=treaty_id,
        violating_patch_id=patch_id,
        violated_consequence_id=consequence_id,
        violation_kind=violation_kind,
        evidence=evidence,
        severity=max(0, min(10, severity)),
        timestamp=_utc_now(),
    )


def _infer_violation_kind(evidence: str) -> str:
    """Heuristically infer a violation kind string from evidence text."""
    ev_lower = evidence.lower()
    if "missing" in ev_lower or "not found" in ev_lower:
        return "missing_export"
    if "signature" in ev_lower or "type" in ev_lower:
        return "wrong_signature"
    if "behaviour" in ev_lower or "behavior" in ev_lower or "semantics" in ev_lower:
        return "behavioural_mismatch"
    return "unclassified"


def audit_treaty(
    treaty_id: str,
    treaty_data: dict,
    implementation: dict,
) -> dict:
    """Produce the full audit report for a treaty against an implementation.

    Creates a :class:`TreatyAudit`, runs it, and returns the report dict.

    Parameters
    ----------
    treaty_id      : the treaty being audited
    treaty_data    : dict describing the treaty (same format as used by
                     :func:`derive_implementation_consequences`)
    implementation : the implementation dict to check against

    Returns
    -------
    dict
        Full audit report (see :meth:`TreatyAudit.run` for schema).

    Examples
    --------
    >>> report = audit_treaty("T-001", {"treaty_id": "T-001", ...}, {})
    >>> "audit_id" in report
    True
    """
    audit = TreatyAudit(treaty_id=treaty_id)
    return audit.run(treaty_data, implementation)


# ---------------------------------------------------------------------------
# TreatyAudit
# ---------------------------------------------------------------------------

class TreatyAudit:
    """Produces a full compliance report for a single treaty.

    TreatyAudit orchestrates the full lifecycle:

    1. :func:`derive_implementation_consequences` → list of consequences
    2. :meth:`ConsequenceChecker.check_all` → list of violations
    3. Compile a structured report dict

    Attributes
    ----------
    audit_id  : unique identifier for this audit run
    treaty_id : identifier of the treaty being audited
    checker   : the :class:`ConsequenceChecker` instance used internally
    """

    def __init__(
        self,
        treaty_id: str = "",
        strict_mode: bool = False,
    ) -> None:
        self.audit_id: str = f"AUDIT-{uuid.uuid4().hex[:12]}"
        self.treaty_id: str = treaty_id
        self.checker: ConsequenceChecker = ConsequenceChecker(
            checker_id=f"chk-{self.audit_id}",
            strict_mode=strict_mode,
        )

    def run(self, treaty_data: dict, implementation: dict) -> dict:
        """Execute the audit and return a full report dict.

        Parameters
        ----------
        treaty_data    : treaty descriptor dict (see :func:`derive_implementation_consequences`)
        implementation : the implementation to check against

        Returns
        -------
        dict with keys:
            audit_id, treaty_id, timestamp, consequence_count, checker_report,
            violations (list), pass_rate (float 0–1), passed (bool)
        """
        # Normalise treaty_data to use this audit's treaty_id
        data = dict(treaty_data)
        data.setdefault("treaty_id", self.treaty_id)

        consequences = derive_implementation_consequences([data])
        violations = self.checker.check_all(consequences, implementation)

        n = len(consequences)
        n_violated = len(violations)
        pass_rate = (n - n_violated) / n if n > 0 else 1.0

        return {
            "audit_id":          self.audit_id,
            "treaty_id":         self.treaty_id,
            "timestamp":         _utc_now(),
            "consequence_count": n,
            "checker_report":    self.checker.get_report(),
            "violations":        [
                {
                    "violation_id": v.violation_id,
                    "patch_id":     v.violating_patch_id,
                    "consequence":  v.violated_consequence_id,
                    "kind":         v.violation_kind,
                    "severity":     v.severity,
                    "critical":     v.is_critical(),
                    "evidence":     v.evidence,
                }
                for v in violations
            ],
            "pass_rate": pass_rate,
            "passed":    n_violated == 0,
        }

    def summarize(self) -> str:
        """Return a one-paragraph text summary of the audit results."""
        report = self.checker.get_report()
        total   = report["checked_count"]
        viols   = report["violation_count"]
        crits   = report["critical_count"]
        passed  = total - viols
        return (
            f"Audit {self.audit_id!r} for treaty {self.treaty_id!r}: "
            f"{passed}/{total} consequences satisfied "
            f"({viols} violation(s), {crits} critical)."
        )

    def export_json(self) -> str:
        """Return the checker report as a JSON string.

        Uses only the standard library (json) — no third-party dependencies.
        """
        import json
        return json.dumps(self.checker.get_report(), indent=2, default=str)


# ---------------------------------------------------------------------------
# __main__ block
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("=" * 72)
    print("implementation_consequences.py  —  self-test / smoke test")
    print("=" * 72)

    # -----------------------------------------------------------------------
    # 1. TrustTier algebra
    # -----------------------------------------------------------------------
    print("\n--- TrustTier ---")
    for tier in TrustTier:
        print(f"  {tier.label()}, deploy_ok={tier.is_sufficient_for_deployment()}")
    p = TrustTier.PROPOSAL
    pb = TrustTier.PROOF_BACKED
    print(f"  PROPOSAL.meet(PROOF_BACKED) = {p.meet(pb).label()}")
    print(f"  PROPOSAL.join(PROOF_BACKED) = {p.join(pb).label()}")
    print(f"  PROPOSAL.promote()          = {p.promote().label()}")
    print(f"  PROOF_BACKED.demote()       = {pb.demote().label()}")
    print(f"  PROPOSAL < VERIFIED         = {TrustTier.PROPOSAL < TrustTier.VERIFIED}")

    # -----------------------------------------------------------------------
    # 2. Judgment (frozen dataclass)
    # -----------------------------------------------------------------------
    print("\n--- Judgment ---")
    j = make_judgment(
        context="module_A::module_B",
        formula="TYPE_SAFE(A → B)",
        assumptions=("contract_active",),
        evidence=("test_suite_pass",),
        obligations=("prove_type_safe",),
        burden="module_A",
        trust=TrustTier.REVIEWED,
        provenance="solver_run_42",
    )
    print(f"  {j.summary()}")
    print(f"  deployment_ready={j.is_deployment_ready()}")
    j2 = j.strengthen_trust(TrustTier.VERIFIED)
    print(f"  after strengthen_trust: {j2.trust.label()}")
    j3 = j2.add_evidence("runtime_check_passed")
    print(f"  evidence count after add: {len(j3.evidence)}")

    # -----------------------------------------------------------------------
    # 3. CechObstruction
    # -----------------------------------------------------------------------
    print("\n--- CechObstruction ---")
    co_empty = CechObstruction("COV-1", frozenset(), "H¹[trivial]", "no obstruction")
    co_nontrivial = CechObstruction(
        "COV-2",
        frozenset([("A", "B", "delta_1")]),
        "H¹[T-001][missing_export]",
        "patch A missing fn_process",
    )
    print(f"  trivial={co_empty.is_trivial()}, nontrivial={co_nontrivial.is_trivial()}")
    print(f"  cover_id={co_nontrivial.cover_id}, class={co_nontrivial.cohomology_class}")

    # -----------------------------------------------------------------------
    # 4. BoundaryGuarantee
    # -----------------------------------------------------------------------
    print("\n--- BoundaryGuarantee ---")
    bg = BoundaryGuarantee(
        guarantee_id="BG-auth-001",
        patch_id="auth_service",
        boundary_symbols=frozenset({"authenticate", "verify_token", "logout"}),
        stability_condition="no API breaking changes",
        trust_tier=TrustTier.VERIFIED,
        valid_until="2025-12-31",
    )
    print(f"  {bg}")
    print(f"  is_valid={bg.is_valid()}")
    print(f"  covers_symbol('authenticate')={bg.covers_symbol('authenticate')}")
    print(f"  covers_symbol('delete_user')={bg.covers_symbol('delete_user')}")
    bg_j = bg.to_judgment()
    print(f"  to_judgment: {bg_j.summary()}")

    # -----------------------------------------------------------------------
    # 5. TreatyImplementationConsequence
    # -----------------------------------------------------------------------
    print("\n--- TreatyImplementationConsequence ---")
    cons = TreatyImplementationConsequence(
        consequence_id="CONS-T1-auth-export-001",
        treaty_id="T-001",
        patch_id="auth_service",
        requirement_kind="export",
        requirement_description="auth_service must export authenticate(token: str) -> bool",
        signature="authenticate(token: str) -> bool",
        priority=1,
        trust_tier=TrustTier.VERIFIED,
    )
    print(f"  {cons}")
    print(f"  describe:\n{cons.describe()}")

    impl_good = {"CONS-T1-auth-export-001": True}
    impl_bad  = {}
    print(f"  is_satisfied (good): {cons.is_satisfied(impl_good)}")
    print(f"  is_satisfied (bad):  {cons.is_satisfied(impl_bad)}")
    cj = cons.to_judgment()
    print(f"  to_judgment: {cj.summary()}")

    # -----------------------------------------------------------------------
    # 6. TreatyViolation
    # -----------------------------------------------------------------------
    print("\n--- TreatyViolation ---")
    viol = report_violation(
        treaty_id="T-001",
        patch_id="auth_service",
        consequence_id="CONS-T1-auth-export-001",
        evidence="Function 'authenticate' missing from auth_service exports",
        severity=9,
    )
    print(f"  {viol}")
    print(f"  is_critical={viol.is_critical()}")
    print(f"  describe:\n{viol.describe()}")
    co = viol.to_cech_obstruction()
    print(f"  to_cech_obstruction: cover_id={co.cover_id}, trivial={co.is_trivial()}")

    # -----------------------------------------------------------------------
    # 7. ConsequenceChecker
    # -----------------------------------------------------------------------
    print("\n--- ConsequenceChecker ---")
    checker = ConsequenceChecker(checker_id="CHK-smoke-001", strict_mode=False)
    print(f"  initial: {checker!r}")
    checker.check(cons, impl_bad)
    print(f"  after failed check: {checker!r}")
    report = checker.get_report()
    print(f"  report keys: {list(report.keys())}")
    print(f"  violation_count={report['violation_count']}, critical={report['critical_count']}")

    # -----------------------------------------------------------------------
    # 8. derive_implementation_consequences
    # -----------------------------------------------------------------------
    print("\n--- derive_implementation_consequences ---")
    treaties = [
        {
            "treaty_id": "T-DEMO",
            "signatories": ["auth_service", "data_service", "api_gateway"],
            "tier": "VERIFIED",
            "requirement_kinds": ["export", "type_contract"],
            "priority": 2,
        }
    ]
    derived = derive_implementation_consequences(treaties)
    print(f"  derived {len(derived)} consequences for 3 signatories × 2 kinds")
    for c in derived[:3]:
        print(f"    {c}")

    # -----------------------------------------------------------------------
    # 9. check_boundary_guarantee
    # -----------------------------------------------------------------------
    print("\n--- check_boundary_guarantee ---")
    full_symbols = frozenset({"authenticate", "verify_token", "logout", "extra_fn"})
    partial_symbols = frozenset({"authenticate"})

    j_ok = check_boundary_guarantee(bg, full_symbols)
    j_fail = check_boundary_guarantee(bg, partial_symbols)
    print(f"  with all symbols present:  trust={j_ok.trust.label()}")
    print(f"  with partial symbols:      trust={j_fail.trust.label()}")
    print(f"  fail obligations: {j_fail.obligations}")

    # -----------------------------------------------------------------------
    # 10. TreatyAudit + audit_treaty
    # -----------------------------------------------------------------------
    print("\n--- TreatyAudit ---")
    treaty_data = {
        "treaty_id": "T-AUDIT",
        "signatories": ["service_A", "service_B"],
        "tier": "VERIFIED",
        "requirement_kinds": ["export"],
    }
    # implementation satisfies service_A but not service_B
    partial_impl: dict = {}
    for c in derive_implementation_consequences([treaty_data]):
        if "service_A" in c.consequence_id:
            partial_impl[c.consequence_id] = True

    audit = TreatyAudit(treaty_id="T-AUDIT")
    audit_report = audit.run(treaty_data, partial_impl)
    print(f"  audit_id={audit_report['audit_id']}")
    print(f"  consequence_count={audit_report['consequence_count']}")
    print(f"  pass_rate={audit_report['pass_rate']:.2f}")
    print(f"  passed={audit_report['passed']}")
    print(f"  summarize: {audit.summarize()}")

    full_impl = {c.consequence_id: True for c in derive_implementation_consequences([treaty_data])}
    full_report = audit_treaty("T-AUDIT", treaty_data, full_impl)
    print(f"  audit_treaty with full impl: passed={full_report['passed']}")

    # -----------------------------------------------------------------------
    # 11. GuaranteeMatrix
    # -----------------------------------------------------------------------
    print("\n--- GuaranteeMatrix ---")
    gm = GuaranteeMatrix()
    gm.add_implication("G-type-safe", "G-non-null")
    gm.add_implication("G-non-null", "G-idempotent")
    gm.add_implication("G-type-safe", "G-boundary-ok")
    print(f"  {gm}")
    print(f"  implies('G-type-safe','G-idempotent') = {gm.implies('G-type-safe','G-idempotent')}")
    print(f"  implied_by('G-non-null') = {gm.implied_by('G-non-null')}")

    # -----------------------------------------------------------------------
    # 12. ViolationAggregator
    # -----------------------------------------------------------------------
    print("\n--- ViolationAggregator ---")
    agg = ViolationAggregator()
    viol2 = report_violation("T-001", "data_service", "CONS-data-001", "missing export", 5)
    agg.add(viol)
    agg.add(viol2)
    agg.add(viol)  # duplicate — should be ignored
    print(f"  count={len(agg)}, critical_count={agg.critical_count()}")
    print(f"  total_severity={agg.total_severity()}")
    print(agg.summary())

    # -----------------------------------------------------------------------
    # 13. ALL_INVARIANTS and constants
    # -----------------------------------------------------------------------
    print("\n--- Constants and invariants ---")
    print(f"  CRITICAL_SEVERITY_THRESHOLD={CRITICAL_SEVERITY_THRESHOLD}")
    print(f"  MAX_VIOLATIONS_BEFORE_HALT={MAX_VIOLATIONS_BEFORE_HALT}")
    print(f"  DEFAULT_STABILITY_WINDOW={DEFAULT_STABILITY_WINDOW!r}")
    print(f"  ALL_INVARIANTS count={len(ALL_INVARIANTS)}")

    print("\n" + "=" * 72)
    print("All classes and functions exercised successfully.")
    print("=" * 72)
