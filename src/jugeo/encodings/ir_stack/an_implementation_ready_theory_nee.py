# copilot: Implementation-ready theory – concretizing Judgment Geometry for code generation
"""
an_implementation_ready_theory_nee.py
==========================================
Implementation-ready theory: concretizing Judgment Geometry for code generation.

Judgment Geometry (theory2.tex) defines judgments as 8-tuples::

    J = (c, φ, A, E, O, B, T, Π)

where:
  c  – context morphism in the site C
  φ  – formula / proposition being judged
  A  – ambient type / domain of discourse
  E  – evidence term (proof-object or witness)
  O  – obstruction class in Čech cohomology H¹(C, 𝒜)
  B  – boundary / presheaf section on ∂U
  T  – trust tier, element of the ordered algebra (TrustTier, ≤, ∧, ∨)
  Π  – proof-obligation set, a finite collection of discharge conditions

The *implementation-ready* layer of the IR stack answers the question:
"What concrete computational objects must exist before a code generator can
 emit correct, verifiable code from a theory specification?"

Three invariants govern this layer:

1. **Judgment completeness** – every slot of J must be inhabited by a
   computable term; existential quantifiers must have explicit witnesses.

2. **TrustTier ordered algebra** – the trust levels form a bounded lattice
   (⊥ = UNTRUSTED, ⊤ = VERIFIED) with meet (∧) and join (∨) operations used
   to propagate trust through obligation graphs.

3. **Obstruction grounding** – each cohomology class O ∈ H¹(C, 𝒜) must be
   realized as a concrete Čech 1-cocycle in a finite chain complex so that the
   code generator can decide obstruction triviality algorithmically.

Bridging an abstraction gap means:
  (a) supplying computable witnesses for each ∃-quantifier in the theory,
  (b) grounding every Čech H¹ obstruction class in a concrete chain complex
      (i.e., exhibiting a cover {Uᵢ} and transition data gᵢⱼ : Uᵢ∩Uⱼ → G),
  (c) assigning a TrustTier to every proof obligation so the lattice ordering
      can be checked mechanically by a type-checker or SMT solver.
"""

from __future__ import annotations

import enum
import itertools
import math
import textwrap
import uuid
from dataclasses import dataclass, field, replace
from typing import Any, Dict, FrozenSet, List, Optional, Sequence, Tuple

# ---------------------------------------------------------------------------
# Optional jugeo imports – graceful fallback stubs so the module can be
# imported even when the surrounding jugeo package is not yet installed.
# ---------------------------------------------------------------------------
try:
    from jugeo.core.judgment import Judgment  # type: ignore[import]
    from jugeo.core.trust import TrustTier as _CoreTrustTier  # type: ignore[import]
    from jugeo.core.obstruction import ObstructionClass  # type: ignore[import]
    _JUGEO_AVAILABLE = True
except ImportError:  # pragma: no cover
    _JUGEO_AVAILABLE = False

    class Judgment:  # type: ignore[no-redef]
        """Stub: 8-tuple (c, φ, A, E, O, B, T, Π)."""
        __slots__ = ("c", "phi", "A", "E", "O", "B", "T", "Pi")

        def __init__(self, c, phi, A, E, O, B, T, Pi):
            for slot, val in zip(self.__slots__, (c, phi, A, E, O, B, T, Pi)):
                object.__setattr__(self, slot, val)

    class ObstructionClass:  # type: ignore[no-redef]
        """Stub for a Čech H¹ obstruction class."""
        def __init__(self, name: str = "trivial"):
            self.name = name

        def is_trivial(self) -> bool:
            return self.name == "trivial"


# ---------------------------------------------------------------------------
# TrustTier ordered algebra
# ---------------------------------------------------------------------------

class TrustTierEnum(enum.IntEnum):
    """
    Ordered algebra of trust tiers used by Judgment Geometry.

    The lattice ordering is::

        UNTRUSTED < ASSUMED < CHECKED < VERIFIED

    with meet (∧) = min and join (∨) = max (isomorphic to the usual integer
    ordering on {0,1,2,3}).  The bounded lattice has:
      ⊥ = UNTRUSTED  (bottom element)
      ⊤ = VERIFIED   (top element)
    """
    UNTRUSTED = 0
    ASSUMED   = 1
    CHECKED   = 2
    VERIFIED  = 3

    # lattice operations -------------------------------------------------------

    def meet(self, other: "TrustTierEnum") -> "TrustTierEnum":
        """Lattice meet (∧): greatest lower bound."""
        return TrustTierEnum(min(self.value, other.value))

    def join(self, other: "TrustTierEnum") -> "TrustTierEnum":
        """Lattice join (∨): least upper bound."""
        return TrustTierEnum(max(self.value, other.value))

    def __le__(self, other: object) -> bool:  # type: ignore[override]
        if isinstance(other, TrustTierEnum):
            return self.value <= other.value
        return NotImplemented

    def __ge__(self, other: object) -> bool:  # type: ignore[override]
        if isinstance(other, TrustTierEnum):
            return self.value >= other.value
        return NotImplemented


# Alias so the mandatory name TrustTier(IntEnum) is also present
TrustTier = TrustTierEnum


# ---------------------------------------------------------------------------
# Judgment — (c, φ, A, E, O, B, T, Π) — NEVER a boolean
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Judgment:
    """A judgment (c, φ, A, E, O, B, T, Π) — NEVER a boolean."""

    context: Any
    formula: Any
    assumptions: tuple
    evidence: tuple
    obligations: tuple
    burden: Any
    trust: TrustTierEnum
    provenance: Any

    def promote(self) -> "Judgment":
        new_trust_val = min(int(self.trust) + 1, max(e.value for e in TrustTierEnum))
        return Judgment(
            context=self.context, formula=self.formula,
            assumptions=self.assumptions, evidence=self.evidence,
            obligations=self.obligations, burden=self.burden,
            trust=TrustTierEnum(new_trust_val), provenance=self.provenance,
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "formula": str(self.formula), "trust": self.trust.name,
            "obligations": list(self.obligations), "burden": str(self.burden),
        }


# ---------------------------------------------------------------------------
# CechObstruction — Čech H¹ cohomology class witnessing descent failure
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CechObstruction:
    """A Čech H¹ cohomology class witnessing descent failure."""

    cover_id: str
    cocycle: FrozenSet[str]
    cohomology_class: str
    description: str

    def is_trivial(self) -> bool:
        """A trivial obstruction has an empty cocycle."""
        return len(self.cocycle) == 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "cover_id": self.cover_id,
            "cocycle": sorted(self.cocycle),
            "cohomology_class": self.cohomology_class,
            "description": self.description,
            "is_trivial": self.is_trivial(),
        }


# ---------------------------------------------------------------------------
# Frozen dataclasses
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ConcretizationStep:
    """
    A single step in the concretization of an abstract theory construct.

    In Judgment Geometry a concretization step replaces one abstract form
    (e.g., an existential type ∃α.P(α)) with a concrete form (e.g., a
    specific Python class ``ConcreteWitness`` together with an instance
    showing P holds).  The *trust_delta* records how the TrustTier changes
    at this step: positive means the concretization *increases* trust
    (e.g., by supplying a machine-checked proof), negative means it
    *decreases* trust (e.g., by substituting an axiom for a derivation).
    """

    step_id: str
    """Unique identifier for this concretization step."""

    abstract_form: str
    """Human-readable description of the abstract construct being replaced."""

    concrete_form: str
    """Human-readable description of the concrete replacement."""

    justification: str
    """
    Explanation of why this replacement is theory-sound, referencing
    lemmas or axioms from theory2.tex where applicable.
    """

    trust_delta: int
    """
    Change in trust level applied at this step.  Positive = trust increase;
    negative = trust decrease.  Clamped to [-3, 3] to stay within the
    four-level TrustTier lattice.
    """

    def clamped_delta(self) -> int:
        """Return trust_delta clamped to the lattice range [-3, 3]."""
        return max(-3, min(3, self.trust_delta))


@dataclass(frozen=True)
class ConcretizationTrace:
    """
    A complete record of how an abstract theory specification was made
    concrete, as a sequence of :class:`ConcretizationStep` objects.

    The trace constitutes a *proof certificate* that the concretization
    process was sound: each step is individually justified, and the
    composition of steps must preserve the theory invariants from
    theory2.tex (Judgment completeness, TrustTier monotonicity, and
    Obstruction grounding).

    Attributes
    ----------
    trace_id:
        Unique identifier for this trace (typically a UUID).
    steps:
        Ordered tuple of concretization steps, applied front-to-back.
    source_spec:
        Identifier of the abstract specification that was concretized.
    target_spec:
        Identifier of the concrete specification that was produced.
    """

    trace_id: str
    steps: Tuple[ConcretizationStep, ...]
    source_spec: str
    target_spec: str

    def net_trust_delta(self) -> int:
        """Sum of all trust deltas across steps, clamped to [-3, 3]."""
        raw = sum(s.clamped_delta() for s in self.steps)
        return max(-3, min(3, raw))

    def step_count(self) -> int:
        """Number of concretization steps in the trace."""
        return len(self.steps)

    def abstract_forms(self) -> Tuple[str, ...]:
        """Return all abstract forms that were concretized."""
        return tuple(s.abstract_form for s in self.steps)

    def concrete_forms(self) -> Tuple[str, ...]:
        """Return all concrete forms produced."""
        return tuple(s.concrete_form for s in self.steps)


@dataclass(frozen=True)
class AbstractionGap:
    """
    A gap between what the theory specification requires and what the current
    implementation concretely provides.

    In Judgment Geometry an abstraction gap arises whenever:
      * an existential quantifier ∃x.P(x) has no computable witness,
      * a Čech H¹ obstruction class has not been grounded in a concrete
        chain complex, or
      * a proof obligation has no assigned TrustTier (or is assigned
        the bottom tier UNTRUSTED without further evidence).

    Closing a gap requires supplying the missing witness, chain complex, or
    trust evidence — see :func:`close_abstraction_gap`.

    Attributes
    ----------
    gap_id:
        Unique identifier for this gap.
    abstract_concept:
        The abstract concept from theory2.tex that is not yet concrete.
    concrete_approach:
        The proposed concrete replacement or implementation strategy.
        May be empty (``""``) if no approach has been decided yet.
    gap_kind:
        Category of gap.  One of:
        ``"EXISTENTIAL_WITNESS"`` | ``"COHOMOLOGY_GROUNDING"`` |
        ``"TRUST_ASSIGNMENT"`` | ``"TYPE_ERASURE"`` | ``"PROOF_TERM"`` |
        ``"COMPUTATIONAL_CONTENT"`` | ``"OTHER"``.
    severity:
        Impact on code generation: ``"CRITICAL"`` | ``"MAJOR"`` | ``"MINOR"``.
        CRITICAL gaps block code generation entirely.
    is_bridged:
        True once a valid bridging strategy has been applied.
    """

    gap_id: str
    abstract_concept: str
    concrete_approach: str
    gap_kind: str
    severity: str  # "CRITICAL" | "MAJOR" | "MINOR"
    is_bridged: bool

    _VALID_SEVERITIES: FrozenSet[str] = frozenset({"CRITICAL", "MAJOR", "MINOR"})
    _VALID_KINDS: FrozenSet[str] = frozenset({
        "EXISTENTIAL_WITNESS", "COHOMOLOGY_GROUNDING", "TRUST_ASSIGNMENT",
        "TYPE_ERASURE", "PROOF_TERM", "COMPUTATIONAL_CONTENT", "OTHER",
    })

    def __post_init__(self) -> None:
        if self.severity not in self._VALID_SEVERITIES:
            raise ValueError(
                f"AbstractionGap.severity must be one of {self._VALID_SEVERITIES}; "
                f"got {self.severity!r}"
            )
        if self.gap_kind not in self._VALID_KINDS:
            raise ValueError(
                f"AbstractionGap.gap_kind must be one of {self._VALID_KINDS}; "
                f"got {self.gap_kind!r}"
            )

    def is_blocking(self) -> bool:
        """Return True if this gap blocks code generation (severity CRITICAL and not bridged)."""
        return self.severity == "CRITICAL" and not self.is_bridged


@dataclass(frozen=True)
class GapBridgingStrategy:
    """
    A strategy for closing an :class:`AbstractionGap`.

    Each strategy is associated with exactly one gap (via ``gap_id``) and
    describes an *approach* for supplying the missing witness, chain complex,
    or trust evidence.  The ``estimated_cost`` is a dimensionless effort score
    (0.0 = trivial, 1.0 = very expensive).

    Attributes
    ----------
    strategy_id:
        Unique identifier for this strategy.
    gap_id:
        The gap this strategy targets.
    approach:
        Human-readable description of the bridging approach.
    estimated_cost:
        Effort score in [0.0, 1.0].
    risk_level:
        ``"LOW"`` | ``"MEDIUM"`` | ``"HIGH"`` — risk of introducing unsoundness.
    """

    strategy_id: str
    gap_id: str
    approach: str
    estimated_cost: float
    risk_level: str  # "LOW" | "MEDIUM" | "HIGH"

    _VALID_RISKS: FrozenSet[str] = frozenset({"LOW", "MEDIUM", "HIGH"})

    def __post_init__(self) -> None:
        if not (0.0 <= self.estimated_cost <= 1.0):
            raise ValueError(
                f"GapBridgingStrategy.estimated_cost must be in [0.0, 1.0]; "
                f"got {self.estimated_cost}"
            )
        if self.risk_level not in self._VALID_RISKS:
            raise ValueError(
                f"GapBridgingStrategy.risk_level must be one of {self._VALID_RISKS}; "
                f"got {self.risk_level!r}"
            )

    def is_acceptable(self, cost_threshold: float = 0.7, allow_high_risk: bool = False) -> bool:
        """
        Return True if this strategy is acceptable under the given thresholds.

        Parameters
        ----------
        cost_threshold:
            Maximum acceptable estimated cost (inclusive).
        allow_high_risk:
            If False (default), HIGH-risk strategies are rejected.
        """
        if self.estimated_cost > cost_threshold:
            return False
        if not allow_high_risk and self.risk_level == "HIGH":
            return False
        return True


@dataclass(frozen=True)
class ConcreteObligation:
    """
    A single proof obligation that must be discharged before code generation.

    In Judgment Geometry an obligation corresponds to an element of the
    obligation set Π in the judgment tuple J = (c, φ, A, E, O, B, T, Π).
    Each obligation has a *kind* (the category of thing that must be
    implemented or proved) and a *trust_required* level (the minimum
    TrustTier that must be reached before the obligation is considered
    discharged).

    Attributes
    ----------
    obligation_id:
        Unique identifier for this obligation.
    description:
        Human-readable description of what must be done.
    theory_reference:
        Reference to the relevant definition/lemma/theorem in theory2.tex.
    kind:
        ``"FUNCTION"`` | ``"TYPE"`` | ``"INVARIANT"`` | ``"PROOF"``.
    trust_required:
        Minimum TrustTierEnum value (as int) required for discharge.
    dependencies:
        frozenset of obligation_ids that must be discharged first.
    is_discharged:
        True once the obligation has been formally discharged.
    """

    obligation_id: str
    description: str
    theory_reference: str
    kind: str  # "FUNCTION" | "TYPE" | "INVARIANT" | "PROOF"
    trust_required: int
    dependencies: FrozenSet[str]
    is_discharged: bool

    _VALID_KINDS: FrozenSet[str] = frozenset({"FUNCTION", "TYPE", "INVARIANT", "PROOF"})

    def __post_init__(self) -> None:
        if self.kind not in self._VALID_KINDS:
            raise ValueError(
                f"ConcreteObligation.kind must be one of {self._VALID_KINDS}; "
                f"got {self.kind!r}"
            )
        if not (0 <= self.trust_required <= 3):
            raise ValueError(
                f"ConcreteObligation.trust_required must be in [0, 3]; "
                f"got {self.trust_required}"
            )

    def required_tier(self) -> TrustTierEnum:
        """Return the trust_required as a TrustTierEnum value."""
        return TrustTierEnum(self.trust_required)

    def is_ready_to_discharge(self, discharged_ids: FrozenSet[str]) -> bool:
        """
        Return True if all dependencies have been discharged.

        Parameters
        ----------
        discharged_ids:
            Set of obligation_ids that have already been discharged.
        """
        return self.dependencies.issubset(discharged_ids)


@dataclass(frozen=True)
class ReadinessChecker:
    """
    Checks whether an :class:`ImplementationReadySpec` is actually ready for
    code generation, according to a configurable policy.

    Attributes
    ----------
    checker_id:
        Unique identifier for this checker instance.
    required_trust:
        Minimum overall trust level (as int, mapped to TrustTierEnum) required
        for the spec to pass the readiness check.
    known_gaps:
        Tuple of gaps that this checker is aware of (may be a superset of the
        gaps in the spec being checked — used for cross-spec gap tracking).
    policy:
        Named policy governing the check.  One of:
        ``"STRICT"``   – all obligations discharged, no unbridged gaps,
        ``"LENIENT"``  – only CRITICAL obligations must be discharged,
        ``"ADVISORY"`` – check runs but never returns False (informational only).
    """

    checker_id: str
    required_trust: int
    known_gaps: Tuple[AbstractionGap, ...]
    policy: str  # "STRICT" | "LENIENT" | "ADVISORY"

    _VALID_POLICIES: FrozenSet[str] = frozenset({"STRICT", "LENIENT", "ADVISORY"})

    def __post_init__(self) -> None:
        if self.policy not in self._VALID_POLICIES:
            raise ValueError(
                f"ReadinessChecker.policy must be one of {self._VALID_POLICIES}; "
                f"got {self.policy!r}"
            )

    def required_tier(self) -> TrustTierEnum:
        """Return required_trust as a TrustTierEnum value."""
        return TrustTierEnum(self.required_trust)

    def unresolved_critical_gaps(self) -> List[AbstractionGap]:
        """Return all CRITICAL gaps known to this checker that are not yet bridged."""
        return [g for g in self.known_gaps if g.severity == "CRITICAL" and not g.is_bridged]

    def check(self, spec: "ImplementationReadySpec") -> Tuple[bool, List[str]]:
        """
        Run the readiness check against *spec* according to *policy*.

        Returns
        -------
        (passed, messages)
            ``passed`` is True if the spec passes the check.
            ``messages`` is a list of human-readable diagnostic strings.
        """
        messages: List[str] = []
        passed = True

        # Trust level check
        if spec.trust_level < self.required_tier():
            msg = (
                f"Spec trust level {spec.trust_level.name} is below required "
                f"{self.required_tier().name}."
            )
            messages.append(msg)
            if self.policy != "ADVISORY":
                passed = False

        # Obligation discharge check
        undischarged = [o for o in spec.obligations if not o.is_discharged]
        if undischarged:
            if self.policy == "STRICT":
                messages.append(
                    f"{len(undischarged)} obligation(s) not discharged: "
                    + ", ".join(o.obligation_id for o in undischarged)
                )
                passed = False
            elif self.policy == "LENIENT":
                critical_undischarged = [
                    o for o in undischarged if o.trust_required >= TrustTierEnum.VERIFIED
                ]
                if critical_undischarged:
                    messages.append(
                        f"{len(critical_undischarged)} high-trust obligation(s) not discharged."
                    )
                    passed = False

        # Gap check
        unbridged = [g for g in spec.gaps if not g.is_bridged]
        critical_unb = [g for g in unbridged if g.severity == "CRITICAL"]
        if critical_unb:
            messages.append(
                f"{len(critical_unb)} CRITICAL gap(s) not bridged: "
                + ", ".join(g.gap_id for g in critical_unb)
            )
            if self.policy != "ADVISORY":
                passed = False

        if self.policy == "ADVISORY":
            passed = True

        return passed, messages


@dataclass(frozen=True)
class ImplementationReadySpec:
    """
    A fully elaborated specification, ready for submission to a code generator.

    This is the terminal object of the IR stack for a given theory fragment.
    It collects all :class:`ConcreteObligation` objects (the Π component of
    each judgment), all :class:`AbstractionGap` objects that have been
    identified and (ideally) bridged, the overall TrustTier of the spec, a
    completeness flag, and the target programming language.

    A spec is *complete* iff:
      * ``is_complete`` is True,
      * all obligations are discharged,
      * no CRITICAL gaps remain unbrided,
      * ``trust_level`` is at least CHECKED.

    Attributes
    ----------
    spec_id:
        Unique identifier for this spec.
    obligations:
        Tuple of :class:`ConcreteObligation` objects in dependency order.
    gaps:
        Tuple of :class:`AbstractionGap` objects identified during elaboration.
    trust_level:
        Overall TrustTier of the spec, computed as the meet (∧) of all
        obligation trust levels.
    is_complete:
        Caller-asserted completeness flag.  Use :func:`check_implementation_readiness`
        to verify programmatically.
    target_language:
        Target programming language (e.g., ``"python"``, ``"rust"``, ``"coq"``).
    """

    spec_id: str
    obligations: Tuple[ConcreteObligation, ...]
    gaps: Tuple[AbstractionGap, ...]
    trust_level: TrustTierEnum
    is_complete: bool
    target_language: str

    def discharged_obligations(self) -> Tuple[ConcreteObligation, ...]:
        """Return only the discharged obligations."""
        return tuple(o for o in self.obligations if o.is_discharged)

    def undischarged_obligations(self) -> Tuple[ConcreteObligation, ...]:
        """Return only the undischarged obligations."""
        return tuple(o for o in self.obligations if not o.is_discharged)

    def bridged_gaps(self) -> Tuple[AbstractionGap, ...]:
        """Return only the bridged gaps."""
        return tuple(g for g in self.gaps if g.is_bridged)

    def unridged_gaps(self) -> Tuple[AbstractionGap, ...]:
        """Return only the un-bridged gaps."""
        return tuple(g for g in self.gaps if not g.is_bridged)

    def obligation_by_id(self, obligation_id: str) -> Optional[ConcreteObligation]:
        """Look up an obligation by its id, returning None if not found."""
        for o in self.obligations:
            if o.obligation_id == obligation_id:
                return o
        return None

    def gap_by_id(self, gap_id: str) -> Optional[AbstractionGap]:
        """Look up a gap by its id, returning None if not found."""
        for g in self.gaps:
            if g.gap_id == gap_id:
                return g
        return None

    def computed_trust(self) -> TrustTierEnum:
        """
        Compute actual trust as the lattice meet of all obligation trust levels.

        If there are no obligations, returns TrustTierEnum.VERIFIED by
        convention (vacuous truth).
        """
        if not self.obligations:
            return TrustTierEnum.VERIFIED
        result = TrustTierEnum.VERIFIED
        for o in self.obligations:
            result = result.meet(TrustTierEnum(o.trust_required))
        return result


# ---------------------------------------------------------------------------
# Module-level functions
# ---------------------------------------------------------------------------

def check_implementation_readiness(
    spec: ImplementationReadySpec,
) -> Tuple[bool, List[AbstractionGap]]:
    """
    Determine whether *spec* is ready for code generation.

    Readiness is defined as the conjunction of three conditions from the
    Judgment Geometry theory:

    1. **Obligation completeness** – every :class:`ConcreteObligation` in the
       spec must be discharged.  An undischarged obligation corresponds to an
       unfilled slot in the judgment tuple J = (c, φ, A, E, O, B, T, Π),
       meaning the code generator would encounter an unresolved metavariable.

    2. **Gap freedom** – no :class:`AbstractionGap` with severity ``"CRITICAL"``
       may remain unbrided.  CRITICAL gaps indicate missing computable witnesses
       for existential quantifiers or un-grounded Čech H¹ cohomology classes;
       either would result in code that cannot be type-checked.

    3. **Trust sufficiency** – the spec's ``trust_level`` must be at least
       ``TrustTierEnum.CHECKED``.  Specs at UNTRUSTED or ASSUMED trust may
       have unverified axioms that would produce unsound generated code.

    Parameters
    ----------
    spec:
        The :class:`ImplementationReadySpec` to evaluate.

    Returns
    -------
    (ready, blocking_gaps)
        ``ready`` is True iff all three conditions hold.
        ``blocking_gaps`` is the list of CRITICAL unbrided gaps (empty iff ready).
    """
    # Condition 1: all obligations discharged
    undischarged = list(spec.undischarged_obligations())
    obligations_ok = len(undischarged) == 0

    # Condition 2: no unbrided CRITICAL gaps
    blocking_gaps: List[AbstractionGap] = [
        g for g in spec.gaps
        if g.severity == "CRITICAL" and not g.is_bridged
    ]
    gaps_ok = len(blocking_gaps) == 0

    # Condition 3: trust level sufficient
    trust_ok = spec.trust_level >= TrustTierEnum.CHECKED

    ready = obligations_ok and gaps_ok and trust_ok

    # Augment blocking_gaps with info from undischarged obligations
    # by synthesizing synthetic gaps for each undischarged obligation
    # (informational; not added to the spec itself).
    if not obligations_ok:
        for o in undischarged:
            synthetic = AbstractionGap(
                gap_id=f"__synthetic__{o.obligation_id}",
                abstract_concept=f"Undischarged obligation: {o.obligation_id}",
                concrete_approach="Discharge obligation before code generation.",
                gap_kind="PROOF_TERM",
                severity="CRITICAL",
                is_bridged=False,
            )
            blocking_gaps.append(synthetic)

    return ready, blocking_gaps


def close_abstraction_gap(
    gap: AbstractionGap,
    strategy: GapBridgingStrategy,
) -> AbstractionGap:
    """
    Apply *strategy* to *gap*, returning an updated (bridged) gap.

    In Judgment Geometry, closing an abstraction gap means one of:
      * Providing a computable witness ``w : A`` such that ``P(w)`` holds,
        for a gap of kind ``EXISTENTIAL_WITNESS``.
      * Exhibiting a Čech cover ``{Uᵢ}`` and cocycle data ``{gᵢⱼ}`` that
        resolves the H¹ obstruction class, for a gap of kind
        ``COHOMOLOGY_GROUNDING``.
      * Assigning an explicit TrustTier supported by evidence (proof script,
        test suite, SMT certificate) for a gap of kind ``TRUST_ASSIGNMENT``.
      * Supplying a concrete type, proof term, or computational encoding for
        other gap kinds.

    The returned gap is a frozen copy of *gap* with:
      * ``is_bridged = True``
      * ``concrete_approach`` updated to include the strategy's approach text.

    Parameters
    ----------
    gap:
        The :class:`AbstractionGap` to close.
    strategy:
        The :class:`GapBridgingStrategy` to apply.  Must target this gap
        (``strategy.gap_id == gap.gap_id``).

    Returns
    -------
    AbstractionGap
        A new frozen gap with ``is_bridged = True``.

    Raises
    ------
    ValueError
        If ``strategy.gap_id != gap.gap_id``.
    """
    if strategy.gap_id != gap.gap_id:
        raise ValueError(
            f"Strategy {strategy.strategy_id!r} targets gap {strategy.gap_id!r}, "
            f"but gap id is {gap.gap_id!r}."
        )

    if gap.is_bridged:
        # Already bridged — return unchanged to preserve idempotency.
        return gap

    updated_approach = (
        gap.concrete_approach.rstrip()
        + (" " if gap.concrete_approach else "")
        + f"[Strategy {strategy.strategy_id}: {strategy.approach}]"
    )

    # Use object.__setattr__ trick via replace (dataclass replace utility).
    return AbstractionGap(
        gap_id=gap.gap_id,
        abstract_concept=gap.abstract_concept,
        concrete_approach=updated_approach,
        gap_kind=gap.gap_kind,
        severity=gap.severity,
        is_bridged=True,
    )


def generate_concrete_spec(
    abstract_spec: Dict[str, Any],
    language: str,
) -> ImplementationReadySpec:
    """
    Generate an :class:`ImplementationReadySpec` from a plain-dict abstract
    specification and a target language name.

    This function simulates the elaboration pipeline of Judgment Geometry:
    it reads the abstract description, infers a set of obligations and gaps,
    assigns trust tiers, and packages the result as an
    :class:`ImplementationReadySpec`.

    The *abstract_spec* dict is expected to have the following keys
    (all optional — missing keys are defaulted):

    ``"spec_id"`` : str
        Identifier for the spec.  Defaults to a fresh UUID.
    ``"obligations"`` : list[dict]
        List of obligation dicts.  Each dict may have keys:
        ``id``, ``description``, ``theory_ref``, ``kind``, ``trust``,
        ``deps`` (list[str]).
    ``"gaps"`` : list[dict]
        List of gap dicts.  Each dict may have keys:
        ``id``, ``abstract``, ``concrete``, ``kind``, ``severity``.
    ``"trust"`` : int
        Overall trust level (0–3).  Defaults to 1 (ASSUMED).

    Parameters
    ----------
    abstract_spec:
        A plain Python dict describing the abstract specification.
    language:
        Target programming language (e.g., ``"python"``, ``"rust"``).

    Returns
    -------
    ImplementationReadySpec
        A freshly constructed spec with all fields populated.
    """
    spec_id = abstract_spec.get("spec_id") or str(uuid.uuid4())
    raw_obligations = abstract_spec.get("obligations") or []
    raw_gaps = abstract_spec.get("gaps") or []
    trust_int = int(abstract_spec.get("trust", 1))
    trust_int = max(0, min(3, trust_int))

    # Build ConcreteObligation objects
    obligations: List[ConcreteObligation] = []
    for i, raw in enumerate(raw_obligations):
        obl = ConcreteObligation(
            obligation_id=str(raw.get("id", f"obl_{i:04d}")),
            description=str(raw.get("description", "(no description)")),
            theory_reference=str(raw.get("theory_ref", "theory2.tex §?")),
            kind=str(raw.get("kind", "FUNCTION")).upper(),
            trust_required=int(raw.get("trust", trust_int)),
            dependencies=frozenset(str(d) for d in raw.get("deps", [])),
            is_discharged=bool(raw.get("is_discharged", False)),
        )
        obligations.append(obl)

    # Build AbstractionGap objects
    gaps: List[AbstractionGap] = []
    for j, raw in enumerate(raw_gaps):
        sev = str(raw.get("severity", "MINOR")).upper()
        if sev not in ("CRITICAL", "MAJOR", "MINOR"):
            sev = "MINOR"
        kind = str(raw.get("kind", "OTHER")).upper()
        if kind not in AbstractionGap._VALID_KINDS:
            kind = "OTHER"
        gap = AbstractionGap(
            gap_id=str(raw.get("id", f"gap_{j:04d}")),
            abstract_concept=str(raw.get("abstract", "(unknown)")),
            concrete_approach=str(raw.get("concrete", "")),
            gap_kind=kind,
            severity=sev,
            is_bridged=bool(raw.get("is_bridged", False)),
        )
        gaps.append(gap)

    # Compute overall trust as meet of all obligation requirements
    overall_trust = TrustTierEnum(trust_int)
    for o in obligations:
        overall_trust = overall_trust.meet(TrustTierEnum(o.trust_required))

    # Determine is_complete
    all_discharged = all(o.is_discharged for o in obligations)
    no_critical_gaps = all(
        g.is_bridged for g in gaps if g.severity == "CRITICAL"
    )
    is_complete = all_discharged and no_critical_gaps and overall_trust >= TrustTierEnum.CHECKED

    return ImplementationReadySpec(
        spec_id=spec_id,
        obligations=tuple(obligations),
        gaps=tuple(gaps),
        trust_level=overall_trust,
        is_complete=is_complete,
        target_language=language,
    )


def score_readiness(spec: ImplementationReadySpec) -> float:
    """
    Compute a scalar readiness score in [0.0, 1.0] for *spec*.

    The score aggregates three sub-scores (each in [0, 1]) with weights:

    * **Obligation score** (weight 0.4): fraction of obligations discharged.
    * **Gap score** (weight 0.4): weighted fraction of gaps bridged, where
      CRITICAL gaps count 3×, MAJOR count 2×, and MINOR count 1×.
    * **Trust score** (weight 0.2): ``trust_level.value / 3``.

    A score of 1.0 means the spec is fully ready; 0.0 means nothing has been
    done.

    Parameters
    ----------
    spec:
        The :class:`ImplementationReadySpec` to score.

    Returns
    -------
    float
        A readiness score in [0.0, 1.0].
    """
    # Obligation sub-score
    n_obl = len(spec.obligations)
    if n_obl == 0:
        obl_score = 1.0
    else:
        n_discharged = sum(1 for o in spec.obligations if o.is_discharged)
        obl_score = n_discharged / n_obl

    # Gap sub-score (severity-weighted)
    _weight = {"CRITICAL": 3, "MAJOR": 2, "MINOR": 1}
    total_weight = sum(_weight[g.severity] for g in spec.gaps)
    if total_weight == 0:
        gap_score = 1.0
    else:
        bridged_weight = sum(
            _weight[g.severity] for g in spec.gaps if g.is_bridged
        )
        gap_score = bridged_weight / total_weight

    # Trust sub-score
    trust_score = spec.trust_level.value / 3.0

    score = 0.4 * obl_score + 0.4 * gap_score + 0.2 * trust_score
    return round(min(1.0, max(0.0, score)), 6)


def find_critical_gaps(spec: ImplementationReadySpec) -> List[AbstractionGap]:
    """
    Return all CRITICAL :class:`AbstractionGap` objects in *spec*, bridged or not.

    CRITICAL gaps are those whose presence (when unbrided) blocks code
    generation entirely.  This function is useful for prioritizing work:
    callers should attempt to bridge all returned gaps (via
    :func:`close_abstraction_gap`) before invoking the code generator.

    In Judgment Geometry terms, a CRITICAL gap corresponds to a judgment slot
    that is completely absent — e.g., no evidence term E has been provided for
    a judgment whose formula φ is a Π₂-formula (requiring a computable function
    as witness).

    Parameters
    ----------
    spec:
        The spec to search.

    Returns
    -------
    list[AbstractionGap]
        All gaps with severity ``"CRITICAL"``, in the order they appear in
        ``spec.gaps``.
    """
    return [g for g in spec.gaps if g.severity == "CRITICAL"]


def obligation_dependency_graph(
    spec: ImplementationReadySpec,
) -> Dict[str, FrozenSet[str]]:
    """
    Build an adjacency map of the obligation dependency graph.

    Returns a dict mapping each ``obligation_id`` to the frozenset of
    obligation ids it *directly depends on* (its prerequisites).  This is
    the transitive closure is NOT computed — callers needing the full
    reachability graph should apply a topological-sort or DFS themselves.

    The graph can be used to determine a safe discharge order: an obligation
    is safe to discharge once all its dependencies have been discharged.

    In Judgment Geometry, the dependency graph mirrors the *proof graph* of
    the obligation set Π: if obligation π₂ uses the evidence term produced
    by π₁, then π₂ depends on π₁.

    Parameters
    ----------
    spec:
        The spec whose obligations form the graph.

    Returns
    -------
    dict[str, frozenset[str]]
        Adjacency map (id → direct dependencies).

    Raises
    ------
    ValueError
        If a dependency references an obligation id not present in the spec.
    """
    known_ids = frozenset(o.obligation_id for o in spec.obligations)
    graph: Dict[str, FrozenSet[str]] = {}

    for o in spec.obligations:
        unknown_deps = o.dependencies - known_ids
        if unknown_deps:
            raise ValueError(
                f"Obligation {o.obligation_id!r} has unknown dependencies: "
                + ", ".join(sorted(unknown_deps))
            )
        graph[o.obligation_id] = o.dependencies

    return graph


def discharge_obligation(
    obligation: ConcreteObligation,
    evidence: str,
) -> ConcreteObligation:
    """
    Mark *obligation* as discharged, attaching *evidence* to the description.

    In Judgment Geometry, discharging an obligation Π means providing an
    evidence term E such that:
      * E inhabits the type required by the obligation's formula φ,
      * the TrustTier T of E is ≥ ``obligation.trust_required``, and
      * all dependencies have already been discharged (checked separately
        via :func:`obligation_dependency_graph`).

    This function does NOT check dependencies or trust levels — it is the
    caller's responsibility to ensure those pre-conditions hold.  It simply
    records the discharge by setting ``is_discharged = True`` and appending
    the evidence string to the description.

    Parameters
    ----------
    obligation:
        The :class:`ConcreteObligation` to discharge.
    evidence:
        A string description of the evidence (proof script path, test-suite
        result, SMT certificate, etc.).

    Returns
    -------
    ConcreteObligation
        A new frozen obligation with ``is_discharged = True``.
    """
    if obligation.is_discharged:
        # Idempotent: already discharged.
        return obligation

    updated_description = (
        obligation.description.rstrip()
        + f"\n[DISCHARGED] Evidence: {evidence}"
    )

    return ConcreteObligation(
        obligation_id=obligation.obligation_id,
        description=updated_description,
        theory_reference=obligation.theory_reference,
        kind=obligation.kind,
        trust_required=obligation.trust_required,
        dependencies=obligation.dependencies,
        is_discharged=True,
    )


def validate_concretization_trace(
    trace: ConcretizationTrace,
) -> Tuple[bool, List[str]]:
    """
    Validate a :class:`ConcretizationTrace` for internal consistency.

    Validation checks:
    1. The trace must have at least one step.
    2. All step_ids within the trace must be unique.
    3. No step may have both ``abstract_form`` and ``concrete_form`` identical
       (that would be a no-op step, indicating a bookkeeping error).
    4. The net trust delta must not push the combined trust below 0 or above 3
       (i.e., out of the TrustTierEnum range).
    5. All ``trust_delta`` values must individually be in [-3, 3].
    6. ``source_spec`` and ``target_spec`` must be non-empty strings.
    7. ``trace_id`` must be a non-empty string.

    Parameters
    ----------
    trace:
        The :class:`ConcretizationTrace` to validate.

    Returns
    -------
    (valid, errors)
        ``valid`` is True iff no errors were found.
        ``errors`` is a (possibly empty) list of human-readable error messages.
    """
    errors: List[str] = []

    # Check 7: trace_id non-empty
    if not trace.trace_id.strip():
        errors.append("trace_id must be a non-empty string.")

    # Check 6: source and target non-empty
    if not trace.source_spec.strip():
        errors.append("source_spec must be a non-empty string.")
    if not trace.target_spec.strip():
        errors.append("target_spec must be a non-empty string.")

    # Check 1: at least one step
    if len(trace.steps) == 0:
        errors.append("ConcretizationTrace must contain at least one step.")
        # Cannot continue step-level checks
        return len(errors) == 0, errors

    # Check 2: unique step_ids
    seen_ids: set = set()
    for step in trace.steps:
        if step.step_id in seen_ids:
            errors.append(f"Duplicate step_id: {step.step_id!r}.")
        seen_ids.add(step.step_id)

    # Check 3: no trivial (no-op) steps
    for step in trace.steps:
        if step.abstract_form == step.concrete_form:
            errors.append(
                f"Step {step.step_id!r} is a no-op: "
                f"abstract_form == concrete_form == {step.abstract_form!r}."
            )

    # Check 5: individual trust_delta in range
    for step in trace.steps:
        if not (-3 <= step.trust_delta <= 3):
            errors.append(
                f"Step {step.step_id!r} has trust_delta={step.trust_delta} "
                f"outside allowed range [-3, 3]."
            )

    # Check 4: net trust delta in [-3, 3]
    net = trace.net_trust_delta()
    if not (-3 <= net <= 3):
        errors.append(
            f"Net trust delta {net} is outside the TrustTierEnum range [-3, 3]."
        )

    return len(errors) == 0, errors


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _topological_sort(
    graph: Dict[str, FrozenSet[str]]
) -> List[str]:
    """
    Return a topological ordering of the obligation ids in *graph*.

    Uses Kahn's algorithm.  Raises ``ValueError`` on cycles.
    """
    in_degree: Dict[str, int] = {node: 0 for node in graph}
    for node, deps in graph.items():
        for dep in deps:
            if dep in in_degree:
                in_degree[dep] = in_degree.get(dep, 0)  # ensure present
    # Count in-degrees (edges go dep → node, so node has in_degree len(deps))
    in_degree = {node: len(deps) for node, deps in graph.items()}

    queue = [node for node, deg in in_degree.items() if deg == 0]
    result: List[str] = []

    while queue:
        node = queue.pop(0)
        result.append(node)
        # Find all nodes that depend on this one
        for other, deps in graph.items():
            if node in deps:
                in_degree[other] -= 1
                if in_degree[other] == 0:
                    queue.append(other)

    if len(result) != len(graph):
        raise ValueError("Obligation dependency graph contains a cycle.")
    return result


def _describe_judgment_tuple(
    c: str = "id_C",
    phi: str = "⊤",
    A: str = "Unit",
    E: str = "tt",
    O: str = "0 ∈ H¹",
    B: str = "∅",
    T: TrustTierEnum = TrustTierEnum.ASSUMED,
    Pi: str = "∅",
) -> str:
    """Format a judgment 8-tuple as a human-readable string."""
    return (
        f"J = (c={c}, φ={phi}, A={A}, E={E}, "
        f"O={O}, B={B}, T={T.name}, Π={Pi})"
    )


def _make_sample_obligations() -> Tuple[ConcreteObligation, ...]:
    """
    Return a canonical set of sample obligations covering the four kinds.

    Used in the smoke test and in ``generate_concrete_spec`` default output.
    """
    return (
        ConcreteObligation(
            obligation_id="obl_context_morphism",
            description=(
                "Implement context morphism c : C → D as a Python callable "
                "that maps context objects preserving composition."
            ),
            theory_reference="theory2.tex Definition 2.1 (Context Morphism)",
            kind="FUNCTION",
            trust_required=TrustTierEnum.CHECKED.value,
            dependencies=frozenset(),
            is_discharged=False,
        ),
        ConcreteObligation(
            obligation_id="obl_judgment_type",
            description=(
                "Define the Judgment dataclass with all 8 slots populated "
                "and validated at construction time."
            ),
            theory_reference="theory2.tex Definition 3.4 (Judgment Tuple)",
            kind="TYPE",
            trust_required=TrustTierEnum.CHECKED.value,
            dependencies=frozenset({"obl_context_morphism"}),
            is_discharged=False,
        ),
        ConcreteObligation(
            obligation_id="obl_trust_lattice_invariant",
            description=(
                "Verify that TrustTierEnum satisfies the bounded-lattice "
                "axioms: meet/join associativity, commutativity, absorption, "
                "and identity laws."
            ),
            theory_reference="theory2.tex Proposition 4.2 (TrustTier Lattice)",
            kind="INVARIANT",
            trust_required=TrustTierEnum.VERIFIED.value,
            dependencies=frozenset({"obl_judgment_type"}),
            is_discharged=False,
        ),
        ConcreteObligation(
            obligation_id="obl_cech_obstruction_proof",
            description=(
                "Prove that for any finite cover {Uᵢ} the Čech 1-cocycle "
                "condition δ(g)=0 is decidable in the concrete chain complex."
            ),
            theory_reference="theory2.tex Theorem 5.7 (Obstruction Decidability)",
            kind="PROOF",
            trust_required=TrustTierEnum.VERIFIED.value,
            dependencies=frozenset({"obl_trust_lattice_invariant"}),
            is_discharged=False,
        ),
    )


def _make_sample_gaps() -> Tuple[AbstractionGap, ...]:
    """
    Return a canonical set of sample abstraction gaps.

    Used in the smoke test and in ``generate_concrete_spec`` default output.
    """
    return (
        AbstractionGap(
            gap_id="gap_existential_cover",
            abstract_concept=(
                "∃ finite cover {Uᵢ} of the site C such that H¹({Uᵢ}, 𝒜) = 0"
            ),
            concrete_approach="",
            gap_kind="EXISTENTIAL_WITNESS",
            severity="CRITICAL",
            is_bridged=False,
        ),
        AbstractionGap(
            gap_id="gap_cocycle_encoding",
            abstract_concept=(
                "Čech 1-cocycle gᵢⱼ : Uᵢ ∩ Uⱼ → G as abstract sheaf morphism"
            ),
            concrete_approach=(
                "Encode as Python dict mapping (i,j) pairs to group elements."
            ),
            gap_kind="COHOMOLOGY_GROUNDING",
            severity="MAJOR",
            is_bridged=False,
        ),
        AbstractionGap(
            gap_id="gap_trust_assignment_proof_term",
            abstract_concept=(
                "Assignment of TrustTier T to proof term E without explicit "
                "trust evidence in the abstract spec."
            ),
            concrete_approach=(
                "Use type-checker output as CHECKED evidence; SMT certificate "
                "as VERIFIED evidence."
            ),
            gap_kind="TRUST_ASSIGNMENT",
            severity="MAJOR",
            is_bridged=False,
        ),
        AbstractionGap(
            gap_id="gap_type_erasure_evidence",
            abstract_concept=(
                "Evidence term E : A is a dependent type; Python has no "
                "dependent types at runtime."
            ),
            concrete_approach=(
                "Erase to runtime assertion + optional Beartype/mypy annotation."
            ),
            gap_kind="TYPE_ERASURE",
            severity="MINOR",
            is_bridged=False,
        ),
    )


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import pprint

    print("=" * 70)
    print("Judgment Geometry – Implementation-Ready Theory Smoke Test")
    print("=" * 70)

    # -- 1. Create sample obligations ------------------------------------------
    print("\n[1] Creating sample ConcreteObligation instances...")
    obligations = list(_make_sample_obligations())
    for o in obligations:
        print(f"    {o.obligation_id:40s}  kind={o.kind:10s}  discharged={o.is_discharged}")

    # -- 2. Create sample abstraction gaps ------------------------------------
    print("\n[2] Creating sample AbstractionGap instances...")
    gaps = list(_make_sample_gaps())
    for g in gaps:
        print(f"    {g.gap_id:35s}  severity={g.severity:8s}  bridged={g.is_bridged}")

    # -- 3. Create an ImplementationReadySpec ---------------------------------
    print("\n[3] Creating ImplementationReadySpec...")
    spec = ImplementationReadySpec(
        spec_id="spec_judgment_geometry_v1",
        obligations=tuple(obligations),
        gaps=tuple(gaps),
        trust_level=TrustTierEnum.ASSUMED,
        is_complete=False,
        target_language="python",
    )
    print(f"    spec_id        = {spec.spec_id}")
    print(f"    trust_level    = {spec.trust_level.name}")
    print(f"    is_complete    = {spec.is_complete}")
    print(f"    target_language= {spec.target_language}")

    # -- 4. check_implementation_readiness ------------------------------------
    print("\n[4] Calling check_implementation_readiness...")
    ready, blocking = check_implementation_readiness(spec)
    print(f"    ready          = {ready}")
    print(f"    blocking gaps  = {len(blocking)}")
    for bg in blocking:
        print(f"      - {bg.gap_id}")

    # -- 5. score_readiness ---------------------------------------------------
    print("\n[5] Calling score_readiness...")
    score = score_readiness(spec)
    print(f"    readiness score = {score:.4f}")

    # -- 6. find_critical_gaps ------------------------------------------------
    print("\n[6] Calling find_critical_gaps...")
    critical = find_critical_gaps(spec)
    print(f"    critical gaps: {[g.gap_id for g in critical]}")

    # -- 7. obligation_dependency_graph ---------------------------------------
    print("\n[7] Calling obligation_dependency_graph...")
    dep_graph = obligation_dependency_graph(spec)
    print("    dependency graph:")
    for oid, deps in dep_graph.items():
        print(f"      {oid} → {set(deps)}")

    # -- 8. close_abstraction_gap with a GapBridgingStrategy ------------------
    print("\n[8] Closing gap_existential_cover with a bridging strategy...")
    strat = GapBridgingStrategy(
        strategy_id="strat_cover_python_list",
        gap_id="gap_existential_cover",
        approach=(
            "Represent the finite cover as a Python list of strings; "
            "verify cover axioms via unit tests."
        ),
        estimated_cost=0.3,
        risk_level="LOW",
    )
    original_gap = spec.gap_by_id("gap_existential_cover")
    assert original_gap is not None
    bridged_gap = close_abstraction_gap(original_gap, strat)
    print(f"    is_bridged (before) = {original_gap.is_bridged}")
    print(f"    is_bridged (after)  = {bridged_gap.is_bridged}")
    print(f"    concrete_approach   = {bridged_gap.concrete_approach[:80]}...")

    # Update spec with bridged gap
    updated_gaps = tuple(
        bridged_gap if g.gap_id == bridged_gap.gap_id else g
        for g in spec.gaps
    )
    spec = ImplementationReadySpec(
        spec_id=spec.spec_id,
        obligations=spec.obligations,
        gaps=updated_gaps,
        trust_level=spec.trust_level,
        is_complete=spec.is_complete,
        target_language=spec.target_language,
    )

    # -- 9. discharge_obligation with evidence --------------------------------
    print("\n[9] Discharging obl_context_morphism with evidence...")
    original_obl = spec.obligation_by_id("obl_context_morphism")
    assert original_obl is not None
    discharged_obl = discharge_obligation(
        original_obl,
        evidence="tests/test_context_morphism.py::test_compose_preserves_id PASSED",
    )
    print(f"    is_discharged (before) = {original_obl.is_discharged}")
    print(f"    is_discharged (after)  = {discharged_obl.is_discharged}")

    updated_obligations = tuple(
        discharged_obl if o.obligation_id == discharged_obl.obligation_id else o
        for o in spec.obligations
    )
    spec = ImplementationReadySpec(
        spec_id=spec.spec_id,
        obligations=updated_obligations,
        gaps=spec.gaps,
        trust_level=TrustTierEnum.CHECKED,
        is_complete=False,
        target_language=spec.target_language,
    )

    # -- 10. Create ConcretizationTrace with ConcretizationStep instances -----
    print("\n[10] Building ConcretizationTrace...")
    steps = (
        ConcretizationStep(
            step_id="step_001",
            abstract_form="∃ cover {Uᵢ} : Σ(U:Cover C). H¹(U,𝒜) = 0",
            concrete_form="cover: list[str] = ['U0', 'U1', 'U2']",
            justification=(
                "Finite explicit cover; H¹ triviality checked by Python unittest."
            ),
            trust_delta=1,
        ),
        ConcretizationStep(
            step_id="step_002",
            abstract_form="gᵢⱼ : Uᵢ ∩ Uⱼ → G  (sheaf morphism)",
            concrete_form="transitions: dict[tuple[int,int], int] = {(0,1): 1, (1,2): 1}",
            justification=(
                "Group G = ℤ represented as int; cocycle encoded as dict."
            ),
            trust_delta=0,
        ),
        ConcretizationStep(
            step_id="step_003",
            abstract_form="T : TrustTier  (abstract ordered element)",
            concrete_form="TrustTierEnum.CHECKED  (Python IntEnum, value=2)",
            justification=(
                "TrustTierEnum is an IntEnum isomorphic to the 4-element chain lattice."
            ),
            trust_delta=1,
        ),
    )
    trace = ConcretizationTrace(
        trace_id=str(uuid.uuid4()),
        steps=steps,
        source_spec="abstract_judgment_geometry_v1",
        target_spec=spec.spec_id,
    )
    print(f"    trace_id      = {trace.trace_id}")
    print(f"    steps         = {trace.step_count()}")
    print(f"    net_trust_delta = {trace.net_trust_delta()}")

    # -- 11. validate_concretization_trace ------------------------------------
    print("\n[11] Calling validate_concretization_trace...")
    valid, errs = validate_concretization_trace(trace)
    print(f"    valid  = {valid}")
    print(f"    errors = {errs}")

    # -- 12. generate_concrete_spec -------------------------------------------
    print("\n[12] Calling generate_concrete_spec from abstract dict...")
    abstract_dict: Dict[str, Any] = {
        "spec_id": "generated_spec_001",
        "trust": 2,
        "obligations": [
            {
                "id": "gen_obl_001",
                "description": "Implement site morphism functor.",
                "theory_ref": "theory2.tex §2",
                "kind": "FUNCTION",
                "trust": 2,
                "deps": [],
            },
            {
                "id": "gen_obl_002",
                "description": "Prove functor laws hold.",
                "theory_ref": "theory2.tex §2.3",
                "kind": "PROOF",
                "trust": 3,
                "deps": ["gen_obl_001"],
            },
        ],
        "gaps": [
            {
                "id": "gen_gap_001",
                "abstract": "Functor F : C → Set (abstract category theory)",
                "concrete": "Python callable mapping objects to frozensets.",
                "kind": "COMPUTATIONAL_CONTENT",
                "severity": "MAJOR",
            }
        ],
    }
    generated_spec = generate_concrete_spec(abstract_dict, language="python")
    print(f"    spec_id        = {generated_spec.spec_id}")
    print(f"    obligations    = {len(generated_spec.obligations)}")
    print(f"    gaps           = {len(generated_spec.gaps)}")
    print(f"    trust_level    = {generated_spec.trust_level.name}")
    print(f"    is_complete    = {generated_spec.is_complete}")
    print(f"    readiness score= {score_readiness(generated_spec):.4f}")

    # -- Final readiness after partial discharge/bridging ---------------------
    print("\n[Final] Re-checking readiness after partial work...")
    ready2, blocking2 = check_implementation_readiness(spec)
    score2 = score_readiness(spec)
    print(f"    ready          = {ready2}")
    print(f"    blocking gaps  = {len(blocking2)}")
    print(f"    readiness score= {score2:.4f}")
    print(f"    judgment tuple = {_describe_judgment_tuple(T=spec.trust_level)}")

    print("\n" + "=" * 70)
    print("Smoke test complete.")
    print("=" * 70)
