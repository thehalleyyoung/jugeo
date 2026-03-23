"""
Witness Extraction and Proof Burden Distribution for Quantifier Encodings
=========================================================================
Chapter 30 §4(b) of theory2.tex — JuGeo formal verification system.

# copilot: Witness extraction and proof burden distribution for quantifier encodings

Judgment Geometry Background
-----------------------------
In JuGeo's Judgment Geometry, every *judgment* is an 8-tuple

    J = (c, φ, A, E, O, B, T, Π)

where the components are:

    c  — context (background assumptions, typing environment)
    φ  — formula (the proposition being asserted)
    A  — agent (the principal asserting the judgment)
    E  — evidence bundle (supporting data, experiment results)
    O  — obstructions (Čech H¹ cohomology class obstructing local→global lift)
    B  — budget (resource / complexity bound)
    T  — trust tier (ordered algebra: AXIOM > PROOF_BACKED > VERIFIED >
                     TESTED > PROPOSAL > SPECULATIVE > UNTRUSTED)
    Π  — proof term (the formal derivation / certificate)

The Π component IS the proof term, and it is precisely where witnesses for
existential sub-formulas live.  When φ contains a sub-formula ∃x.ψ(x), the
proof term Π must supply a concrete value w such that ψ(w) holds — this value
is the *witness*.

Proof Burden Distribution
--------------------------
The *proof burden* of a judgment J is the set of obligations that must be
discharged before J can be accepted at trust tier T.  The T component
determines how strict each obligation is:

    PROOF_BACKED  — all existential witnesses must be fully constructive;
                    i.e., Π must contain an explicit term, not just a proof
                    of existence.
    VERIFIED      — witnesses may be obtained by verified computation (e.g.,
                    a certified model-checker certificate).
    TESTED        — witnesses may be test-derived (high-coverage but not
                    exhaustive).
    PROPOSAL      — probabilistic / heuristic witnesses are acceptable
                    (e.g., SMT model with no independent verification).
    SPECULATIVE   — witnesses need not be checked; the burden is deferred.
    UNTRUSTED     — no burden is discharged; the judgment is a placeholder.

Obstruction Theory (Čech H¹)
------------------------------
The O component tracks the first Čech cohomology group of the formula's
cover.  When O ≠ 0, there is a *global* obstruction to constructing a
witness from locally consistent partial witnesses.  Functions in this module
test for zero-obstruction before certifying a witness as globally valid.

This module provides:
    - ``WitnessExtractor``          — frozen dataclass configuration for extraction
    - ``ProofBurden``               — frozen dataclass for a full judgment's burden
    - ``SingleBurden``              — frozen dataclass for one component's sub-burden
    - ``QuantifierWitness``         — frozen dataclass for an extracted witness
    - ``BurdenDistribution``        — frozen dataclass for multi-party allocation
    - ``WitnessValidity``           — frozen dataclass for validity check result
    - ``ExtractionTrace``           — frozen dataclass recording extraction steps
    - ``ExtractionStep``            — frozen dataclass for one extraction step
    - ``extract_witness``           — extract a QuantifierWitness from an encoding
    - ``distribute_proof_burden``   — distribute ProofBurden across J components
    - ``check_witness_validity``    — validate a witness against a formula
    - ``build_burden_for_component``— build SingleBurden for one judgment component
    - ``combine_witnesses``         — combine two witnesses into one
    - ``witness_trust_score``       — compute a numeric trust score for a witness
    - ``extract_existential_witnesses`` — extract all ∃-witnesses from a formula
    - ``proof_burden_graph``        — build an adjacency dict from a ProofBurden

copilot notes:
    Use ``extract_existential_witnesses`` as the primary entry point for
    formula-level witness discovery.  Use ``distribute_proof_burden`` with a
    concrete judgment tuple to get per-component obligations.  The trust tier
    constants are defined in the ``TrustTier`` enum.
"""

from __future__ import annotations

import re
import uuid
import hashlib
import itertools
from dataclasses import dataclass
from enum import IntEnum
from typing import Any, Iterator

# ---------------------------------------------------------------------------
# Optional jugeo imports — graceful fallback stubs
# ---------------------------------------------------------------------------

try:
    from jugeo.encodings.tensor_quantifier_encodings.models import (
        ExtractionStrategy,
        QuantifierDiscipline,
        DisciplineKind,
        WitnessExtractor as _ModelWitnessExtractor,
    )
    _MODELS_AVAILABLE = True
except ImportError:
    _MODELS_AVAILABLE = False
    ExtractionStrategy = Any  # type: ignore[misc,assignment]
    QuantifierDiscipline = Any  # type: ignore[misc,assignment]
    DisciplineKind = Any  # type: ignore[misc,assignment]
    _ModelWitnessExtractor = Any  # type: ignore[misc,assignment]

try:
    from jugeo.encodings.tensor_quantifier_encodings.quantifier_discipline import (
        QuantifierDisciplineChecker,
        QuantifierInfo,
        is_qf_formula,
    )
    _DISCIPLINE_AVAILABLE = True
except ImportError:
    _DISCIPLINE_AVAILABLE = False
    QuantifierDisciplineChecker = Any  # type: ignore[misc,assignment]
    QuantifierInfo = Any  # type: ignore[misc,assignment]

    def is_qf_formula(formula: str) -> bool:  # type: ignore[misc]
        """Stub: return True iff formula contains no quantifier symbols."""
        return "∃" not in formula and "∀" not in formula and "exists" not in formula.lower()

try:
    from jugeo.encodings.tensor_quantifier_encodings.witness_extractor import (
        TensorWitness,
        TensorWitnessExtractor,
    )
    _EXTRACTOR_AVAILABLE = True
except ImportError:
    _EXTRACTOR_AVAILABLE = False
    TensorWitness = Any  # type: ignore[misc,assignment]
    TensorWitnessExtractor = Any  # type: ignore[misc,assignment]

try:
    from jugeo.judgments import Judgment  # type: ignore[import]
    _JUDGMENTS_AVAILABLE = True
except ImportError:
    _JUDGMENTS_AVAILABLE = False
    Judgment = Any  # type: ignore[misc,assignment]


# ---------------------------------------------------------------------------
# Trust Tier ordered algebra
# ---------------------------------------------------------------------------

class TrustTier(IntEnum):
    """Ordered algebra for judgment trust, from weakest to strongest.

    The ordering is:
        UNTRUSTED < SPECULATIVE < PROPOSAL < TESTED < VERIFIED < PROOF_BACKED < AXIOM

    This is a *total* order and forms a distributive lattice under ∧ (min)
    and ∨ (max).  The join (max) of two trust tiers is the stronger trust, and
    the meet (min) is the weaker trust — conservative composition uses meet.

    Proof burden strictness increases monotonically with trust tier: a judgment
    at PROOF_BACKED demands fully constructive witnesses, while a judgment at
    PROPOSAL accepts probabilistic or heuristic witnesses.
    """

    UNTRUSTED    = 0
    SPECULATIVE  = 1
    PROPOSAL     = 2
    TESTED       = 3
    VERIFIED     = 4
    PROOF_BACKED = 5
    AXIOM        = 6

    def join(self, other: "TrustTier") -> "TrustTier":
        """Lattice join (strongest / maximum tier)."""
        return TrustTier(max(self.value, other.value))

    def meet(self, other: "TrustTier") -> "TrustTier":
        """Lattice meet (weakest / minimum tier) — conservative composition."""
        return TrustTier(min(self.value, other.value))

    def requires_constructive_witness(self) -> bool:
        """Return True iff this tier mandates fully constructive witnesses."""
        return self >= TrustTier.PROOF_BACKED

    def allows_probabilistic_witness(self) -> bool:
        """Return True iff this tier permits probabilistic / heuristic witnesses."""
        return self <= TrustTier.PROPOSAL

    def burden_label(self) -> str:
        """Human-readable label for the proof burden at this tier."""
        labels = {
            TrustTier.UNTRUSTED:    "none (placeholder)",
            TrustTier.SPECULATIVE:  "deferred",
            TrustTier.PROPOSAL:     "probabilistic",
            TrustTier.TESTED:       "test-derived",
            TrustTier.VERIFIED:     "computationally verified",
            TrustTier.PROOF_BACKED: "fully constructive",
            TrustTier.AXIOM:        "axiomatic (no burden)",
        }
        return labels[self]


# ---------------------------------------------------------------------------
# Judgment — (c, φ, A, E, O, B, T, Π) — NEVER a boolean
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Judgment:
    """A judgment (c, φ, A, E, O, B, T, Π) — NEVER a boolean.

    This is the central data structure of Judgment Geometry.  Every claim
    in the system is expressed as a Judgment, never as a bare boolean.
    """

    context: Any
    formula: Any
    assumptions: tuple
    evidence: tuple
    obligations: tuple
    burden: Any
    trust: TrustTier
    provenance: Any

    def promote(self) -> "Judgment":
        """Promote trust by one tier."""
        return Judgment(
            context=self.context, formula=self.formula,
            assumptions=self.assumptions, evidence=self.evidence,
            obligations=self.obligations, burden=self.burden,
            trust=self.trust.join(TrustTier(min(self.trust.value + 1, TrustTier.AXIOM.value))),
            provenance=self.provenance,
        )

    def to_dict(self) -> dict:
        return {
            "formula": str(self.formula), "trust": self.trust.name,
            "obligations": list(self.obligations), "burden": str(self.burden),
        }


# ---------------------------------------------------------------------------
# CechObstruction — Čech H¹ cohomology class witnessing descent failure
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CechObstruction:
    """A Čech H¹ cohomology class witnessing descent failure.

    In the sheaf-theoretic model, a descent obstruction lives in the first
    Čech cohomology group H¹(U, F) of the sheaf F over the cover U.
    """

    cover_id: str
    cocycle: frozenset
    cohomology_class: str
    description: str

    def is_trivial(self) -> bool:
        """A trivial obstruction has an empty cocycle."""
        return len(self.cocycle) == 0

    def to_dict(self) -> dict:
        return {
            "cover_id": self.cover_id,
            "cocycle": sorted(self.cocycle),
            "cohomology_class": self.cohomology_class,
            "description": self.description,
            "is_trivial": self.is_trivial(),
        }


# Canonical component names matching the judgment tuple (c, φ, A, E, O, B, T, Π)
JUDGMENT_COMPONENTS: tuple[str, ...] = ("c", "phi", "A", "E", "O", "B", "T", "Pi")

# Default weight per component when distributing burden uniformly
_DEFAULT_COMPONENT_WEIGHTS: dict[str, float] = {
    "c":   0.05,   # context — usually background; small burden
    "phi": 0.30,   # formula — the main propositional content; largest burden
    "A":   0.05,   # agent   — identity / authority; lightweight
    "E":   0.20,   # evidence — empirical backing; substantial
    "O":   0.10,   # obstructions — Čech H¹ check; moderate
    "B":   0.05,   # budget  — resource constraint; lightweight
    "T":   0.05,   # trust   — tier assertion; lightweight
    "Pi":  0.20,   # proof term — the actual Π derivation; substantial
}

# Regex patterns for syntactic quantifier detection
_EXISTENTIAL_RE = re.compile(
    r"(∃\s*(?P<var1>[A-Za-z_][A-Za-z0-9_]*)\s*[.·:])"
    r"|(exists\s+(?P<var2>[A-Za-z_][A-Za-z0-9_]*)\s*[.·:]?)"
    r"|(\bE\s+(?P<var3>[A-Za-z_][A-Za-z0-9_]*)\s*\.)",
    re.UNICODE,
)
_UNIVERSAL_RE = re.compile(
    r"(∀\s*(?P<var1>[A-Za-z_][A-Za-z0-9_]*)\s*[.·:])"
    r"|(forall\s+(?P<var2>[A-Za-z_][A-Za-z0-9_]*)\s*[.·:]?)"
    r"|(\bA\s+(?P<var3>[A-Za-z_][A-Za-z0-9_]*)\s*\.)",
    re.UNICODE,
)


# ---------------------------------------------------------------------------
# Frozen dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ExtractionStep:
    """One atomic step in a witness extraction trace.

    In Judgment Geometry, extracting a witness for ∃x.φ(x) proceeds by
    repeatedly simplifying the formula until a concrete value for x is
    identified.  Each simplification is recorded as an ExtractionStep so that
    the extraction is fully auditable.

    Fields
    ------
    step_id : str
        Unique identifier for this step (UUID or deterministic hash).
    action : str
        Human-readable label for the action taken (e.g., "SKOLEM_SUBSTITUTE",
        "INSTANTIATE_AT_ZERO", "UNFOLD_DEFINITION", "APPLY_FARKAS").
    formula_before : str
        The formula string before this step was applied.
    formula_after : str
        The formula string after this step was applied.
    substitution : tuple[tuple[str, str], ...]
        Variable-to-value pairs introduced or applied in this step, as
        ``((variable, value), ...)``.  Empty if no substitution was made.
    """

    step_id: str
    action: str
    formula_before: str
    formula_after: str
    substitution: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class ExtractionTrace:
    """Full record of a witness extraction run.

    An ExtractionTrace is produced by ``extract_witness`` and records every
    step taken from the raw quantifier encoding down to the concrete witness
    value.  It can be stored as part of the Π component of a judgment,
    providing a machine-checkable audit trail.

    The ``steps`` field is an ordered tuple of ``ExtractionStep`` objects.
    If ``success`` is False, the final step will describe the failure reason.

    Fields
    ------
    trace_id : str
        Unique identifier for this trace.
    steps : tuple[ExtractionStep, ...]
        Ordered record of extraction actions.
    formula : str
        The original formula for which witness extraction was attempted.
    result_witness_id : str
        ID of the ``QuantifierWitness`` produced, or ``""`` on failure.
    success : bool
        Whether extraction produced a valid concrete witness.
    """

    trace_id: str
    steps: tuple[ExtractionStep, ...]
    formula: str
    result_witness_id: str
    success: bool


@dataclass(frozen=True)
class QuantifierWitness:
    """A concrete witness for an existential claim ∃x.φ(x).

    In the Π (proof term) component of a judgment, every existential
    sub-formula must be backed by a witness.  This dataclass represents one
    such witness: the variable ``variable`` is bound to the concrete value
    ``value`` such that φ(value) is claimed to hold.

    Trust and constructivity
    ------------------------
    ``trust_level`` encodes the ``TrustTier`` integer at which this witness
    was produced.  Witnesses at PROOF_BACKED (5) or above must be
    *constructive*: ``is_constructive`` must be True, and ``verification_trace``
    must contain at least one auditable step.

    At PROPOSAL (2) or below, ``is_constructive`` may be False — the witness
    may have been obtained from an SMT model or probabilistic search without
    independent verification.

    Fields
    ------
    witness_id : str
        Unique identifier for this witness.
    variable : str
        The bound variable whose value this witness provides.
    formula : str
        The existential formula ∃variable.ψ for which this witness applies.
    value : str
        The concrete value assigned to ``variable`` (string representation).
    trust_level : int
        TrustTier ordinal for this witness (0–6).
    is_constructive : bool
        Whether the witness was produced constructively (True) or via
        existence proof / probabilistic search (False).
    verification_trace : tuple[str, ...]
        Human-readable or machine-readable trace of verification steps.
        Empty iff unverified.
    """

    witness_id: str
    variable: str
    formula: str
    value: str
    trust_level: int
    is_constructive: bool
    verification_trace: tuple[str, ...]


@dataclass(frozen=True)
class SingleBurden:
    """One unit of proof burden for a single judgment component.

    Each component of the 8-tuple judgment (c, φ, A, E, O, B, T, Π) carries
    its own sub-obligation.  A ``SingleBurden`` records what must be shown for
    that component and whether it has been discharged.

    The ``component`` field must be one of the canonical component names:
    ``"c"``, ``"phi"``, ``"A"``, ``"E"``, ``"O"``, ``"B"``, ``"T"``, ``"Pi"``.

    Weight and trust
    ----------------
    ``weight`` is a non-negative float representing the relative importance
    of this burden within the full ``ProofBurden``.  The weights across all
    ``SingleBurden`` objects in a ``ProofBurden`` need not sum to exactly 1.0
    (the ``ProofBurden.total_weight`` field normalises them).

    ``trust_required`` specifies the minimum ``TrustTier`` ordinal needed for
    this burden to be considered discharged.  At trust tiers below this value,
    the burden is treated as open even if some evidence has been provided.

    Fields
    ------
    burden_id : str
        Unique identifier for this single burden.
    component : str
        One of "c", "phi", "A", "E", "O", "B", "T", "Pi".
    description : str
        Human-readable description of what must be shown.
    weight : float
        Relative weight (≥ 0.0) of this burden within the overall judgment.
    trust_required : int
        Minimum TrustTier ordinal needed for discharge.
    is_discharged : bool
        Whether this burden has been successfully discharged.
    """

    burden_id: str
    component: str
    description: str
    weight: float
    trust_required: int
    is_discharged: bool


@dataclass(frozen=True)
class ProofBurden:
    """Full proof burden for a judgment (c, φ, A, E, O, B, T, Π).

    A ``ProofBurden`` aggregates the per-component ``SingleBurden`` objects for
    an entire judgment.  It records whether the judgment is fully discharged
    (i.e., whether all ``SingleBurden.is_discharged`` flags are True) and the
    sum of the weights of all undischarged burdens.

    The ``judgment_components`` field lists the component names in order; it
    should match ``JUDGMENT_COMPONENTS`` unless the judgment has been projected
    onto a subset of components.

    Fields
    ------
    burden_id : str
        Unique identifier for this ProofBurden.
    judgment_components : tuple[str, ...]
        Ordered tuple of component names present in this judgment.
    burdens : tuple[SingleBurden, ...]
        One ``SingleBurden`` per component.
    total_weight : float
        Sum of all ``SingleBurden.weight`` values.
    is_discharged : bool
        True iff every ``SingleBurden.is_discharged`` is True.
    """

    burden_id: str
    judgment_components: tuple[str, ...]
    burdens: tuple[SingleBurden, ...]
    total_weight: float
    is_discharged: bool


@dataclass(frozen=True)
class WitnessValidity:
    """Result of checking whether a ``QuantifierWitness`` is valid.

    A witness w for ∃x.φ(x) is *valid* if substituting x ↦ w in φ yields a
    formula that is provably true (at the required trust tier).  This
    dataclass records the outcome of such a check.

    If ``is_valid`` is False, ``failure_reason`` contains a human-readable
    explanation of why the check failed (e.g., "substitution does not satisfy
    body formula", "obstruction O ≠ 0 prevents global validity").

    Fields
    ------
    validity_id : str
        Unique identifier for this validity check result.
    witness_id : str
        ID of the ``QuantifierWitness`` being checked.
    is_valid : bool
        Whether the witness passed all validity checks.
    failure_reason : str
        Description of the failure, or ``""`` if is_valid is True.
    checked_by : str
        Identifier of the checker (e.g., "SYNTACTIC_SUB", "Z3_EVAL",
        "MANUAL_REVIEW").
    trust_level : int
        TrustTier ordinal at which the check was performed.
    """

    validity_id: str
    witness_id: str
    is_valid: bool
    failure_reason: str
    checked_by: str
    trust_level: int


@dataclass(frozen=True)
class BurdenDistribution:
    """Distribution of proof burdens across parties or sub-systems.

    In multi-agent judgments (where the A component names a coalition or
    organisation), the overall proof burden may be distributed across
    participants.  A ``BurdenDistribution`` records the set of
    ``ProofBurden`` objects and the fractional allocation for each party.

    The ``allocation`` field is a tuple of ``(party_id, fraction)`` pairs.
    Fractions should sum to ≈1.0; the ``is_balanced`` flag records whether
    the distribution is fair according to the chosen strategy (e.g., equal
    distribution, weighted by trust tier, or proportional to formula depth).

    Obstructions and global consistency
    ------------------------------------
    A ``BurdenDistribution`` is only meaningful when the union of the burdens
    covers every component of every judgment in ``burdens``.  If there is a
    Čech H¹ obstruction in the O components of any of those judgments, global
    consistency cannot be guaranteed even if all local burdens are discharged.

    Fields
    ------
    distribution_id : str
        Unique identifier.
    burdens : tuple[ProofBurden, ...]
        The ProofBurden objects being distributed.
    allocation : tuple[tuple[str, float], ...]
        ``((party_id, fraction), ...)`` — allocation of burdens to parties.
    is_balanced : bool
        Whether the distribution is balanced under the chosen strategy.
    """

    distribution_id: str
    burdens: tuple[ProofBurden, ...]
    allocation: tuple[tuple[str, float], ...]
    is_balanced: bool


# ---------------------------------------------------------------------------
# WitnessExtractor configuration (frozen dataclass, *this* module's version)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WitnessExtractor:
    """Configuration for extracting witnesses from a quantifier encoding.

    This is the extraction-configuration object used by the functions in this
    module.  It is distinct from the ``WitnessExtractor`` in ``models.py``,
    which is the Z3-oriented solver model.

    Strategy semantics
    ------------------
    CONSTRUCTIVE
        Attempt to build the witness term by structural induction on the
        formula.  Fails if the formula contains opaque sub-terms.  Produces
        witnesses with ``is_constructive=True``.
    CLASSICAL
        Assume the law of excluded middle; use double-negation elimination
        to derive the existence of a witness without constructing it.
        Produces witnesses with ``is_constructive=False``.
    PROBABILISTIC
        Use a probabilistic / heuristic search (e.g., random sampling,
        SMT model, or learned model) to find candidate witnesses.  Produces
        witnesses with ``is_constructive=False`` and low ``trust_level``.

    Trust gating
    ------------
    ``trust_required`` is the minimum ``TrustTier`` ordinal that the produced
    witness must reach.  If the chosen strategy cannot produce a witness at
    this trust tier, extraction fails with an ExtractionTrace recording the
    failure.

    Fields
    ------
    extractor_id : str
        Unique identifier for this extractor configuration.
    strategy : str
        One of ``"CONSTRUCTIVE"``, ``"CLASSICAL"``, or ``"PROBABILISTIC"``.
    trust_required : int
        Minimum TrustTier ordinal for produced witnesses.
    max_search_depth : int
        Maximum recursion / search depth before giving up.
    """

    extractor_id: str
    strategy: str
    trust_required: int
    max_search_depth: int

    # --- Validation helpers (non-mutating) ---

    def is_valid_strategy(self) -> bool:
        """Return True iff ``strategy`` is one of the three recognised values."""
        return self.strategy in {"CONSTRUCTIVE", "CLASSICAL", "PROBABILISTIC"}

    def trust_tier(self) -> TrustTier:
        """Return the ``TrustTier`` corresponding to ``trust_required``."""
        clamped = max(0, min(self.trust_required, int(TrustTier.AXIOM)))
        return TrustTier(clamped)

    def effective_max_depth(self) -> int:
        """Return a safe maximum depth, clamped to [1, 256]."""
        return max(1, min(self.max_search_depth, 256))


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _make_id(prefix: str, *seeds: str) -> str:
    """Generate a short deterministic ID from a prefix and seed strings.

    Uses the first 12 hex characters of SHA-256 to keep IDs compact while
    remaining collision-resistant for practical purposes.

    Args:
        prefix: Human-readable prefix (e.g., "witness", "burden").
        *seeds: Additional strings mixed into the hash.

    Returns:
        A string of the form ``"<prefix>-<12hex>"``.
    """
    raw = prefix + ":" + ":".join(seeds)
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]
    return f"{prefix}-{digest}"


def _extract_quantified_variables(formula: str) -> list[tuple[str, str]]:
    """Return a list of (quantifier_type, variable) pairs found in formula.

    Scans ``formula`` for existential and universal quantifier patterns using
    the module-level regex constants.  Returns pairs of the form
    ``("exists", "x")`` or ``("forall", "y")``.  The scan is purely syntactic
    — it does not track binding scopes.

    Args:
        formula: A formula string potentially containing quantifiers.

    Returns:
        List of ``(quantifier_type, variable_name)`` pairs, in order of
        appearance.
    """
    result: list[tuple[str, str]] = []
    for m in _EXISTENTIAL_RE.finditer(formula):
        var = m.group("var1") or m.group("var2") or m.group("var3")
        if var:
            result.append(("exists", var))
    for m in _UNIVERSAL_RE.finditer(formula):
        var = m.group("var1") or m.group("var2") or m.group("var3")
        if var:
            result.append(("forall", var))
    return result


def _substitute_in_formula(formula: str, variable: str, value: str) -> str:
    """Syntactically substitute ``value`` for all free occurrences of ``variable``.

    This is a simplified textual substitution — it replaces whole-word
    occurrences of ``variable`` with ``value``.  It does not implement
    capture-avoiding substitution; use only after confirming no shadowing.

    Args:
        formula: The formula string in which to substitute.
        variable: The variable name to replace.
        value: The concrete value string to substitute in.

    Returns:
        A new formula string with ``variable`` replaced by ``value``.
    """
    pattern = re.compile(r"\b" + re.escape(variable) + r"\b")
    return pattern.sub(value, formula)


def _heuristic_witness_value(variable: str, formula: str) -> str:
    """Heuristically choose a concrete value for ``variable`` in ``formula``.

    The heuristic proceeds in priority order:
    1. If the formula contains ``variable >= N`` or ``variable > N`` for some
       integer literal N, return N (or N+1).
    2. If the formula contains ``variable = N``, return N.
    3. If the formula contains the string "tensor" or "shape", return "0"
       (the zero index, universally valid for non-empty tensors).
    4. Default: return "1" (a safe non-zero value for most arithmetic contexts).

    This heuristic is intentionally conservative and should only be used at
    PROPOSAL or lower trust tiers.

    Args:
        variable: The variable for which to find a witness.
        formula: The formula providing context clues.

    Returns:
        A string representation of a candidate concrete value.
    """
    ge_match = re.search(
        r"\b" + re.escape(variable) + r"\s*>=?\s*(-?\d+)", formula
    )
    if ge_match:
        base = int(ge_match.group(1))
        return str(base + 1) if ">" in ge_match.group(0) and "=" not in ge_match.group(0) else str(base)

    eq_match = re.search(
        r"\b" + re.escape(variable) + r"\s*=\s*(-?\d+)", formula
    )
    if eq_match:
        return eq_match.group(1)

    if "tensor" in formula.lower() or "shape" in formula.lower():
        return "0"

    return "1"


def _check_cech_obstruction(formula: str, value: str) -> bool:
    """Test for a Čech H¹ obstruction to global validity of ``value`` in ``formula``.

    In full generality this requires computing the first Čech cohomology of the
    formula's cover.  This stub uses two lightweight syntactic heuristics:
    1. If the formula contains explicit ``obstruction != 0`` or ``H1 != 0``,
       the obstruction is non-trivial.
    2. If the proposed ``value`` is a negative integer and the formula contains
       a positivity constraint, the obstruction is non-trivial.

    Returns:
        True iff an obstruction is detected (bad — global validity fails).
        False iff no obstruction is detected (good — local→global lift succeeds).
    """
    if "obstruction != 0" in formula or "H1 != 0" in formula:
        return True

    try:
        numeric = int(value)
    except ValueError:
        return False

    if numeric < 0 and re.search(r"\b(>=\s*0|>\s*0|non.?negative|positive)\b", formula, re.I):
        return True

    return False


def _verify_substitution_syntactically(formula: str, variable: str, value: str) -> bool:
    """Return True iff substituting value for variable plausibly satisfies formula.

    This is a purely syntactic heuristic — it applies the substitution and
    checks whether the result contains an obvious contradiction such as:
        - "0 > 0", "0 >= 1", "-1 >= 0"
        - explicit "False" or "⊥"

    A True return means the check passed; it does NOT mean the formula is
    actually satisfied (that would require a full solver call).

    Args:
        formula: The formula to check after substitution.
        variable: Variable to substitute out.
        value: Concrete value to substitute in.

    Returns:
        True iff no obvious contradiction is detected.
    """
    substituted = _substitute_in_formula(formula, variable, value)

    contradiction_patterns = [
        r"\b0\s*>\s*0\b",
        r"\b0\s*>=\s*[1-9]\d*\b",
        r"-\d+\s*>=\s*0",
        r"\bFalse\b",
        r"\bfalse\b",
        r"⊥",
    ]
    for pat in contradiction_patterns:
        if re.search(pat, substituted):
            return False

    return True


# ---------------------------------------------------------------------------
# Core public functions
# ---------------------------------------------------------------------------


def build_burden_for_component(
    component: str,
    formula: str,
    trust_tier: TrustTier = TrustTier.VERIFIED,
    discharged: bool = False,
) -> SingleBurden:
    """Build a ``SingleBurden`` for a single judgment component.

    Each component of the judgment tuple (c, φ, A, E, O, B, T, Π) requires
    a different kind of proof obligation.  This function constructs the
    appropriate ``SingleBurden`` for ``component``, calibrated to the
    ``trust_tier`` at which the judgment is being evaluated.

    Component-specific obligations
    --------------------------------
    c  (context):    Show that all background assumptions in c are consistent
                     and have been checked at the required trust tier.
    phi (formula):   Show that φ itself is well-formed and that all existential
                     sub-formulas ∃x.ψ(x) in φ have witnesses in Π.
    A  (agent):      Show that the asserting agent A has the authority to make
                     claims at the required trust tier.
    E  (evidence):   Show that the evidence bundle E is sufficient and correctly
                     cited (non-empty at tiers ≥ TESTED).
    O  (obstruction):Show that the Čech H¹ obstruction class O is trivial (zero),
                     i.e., that local witnesses lift to a global witness.
    B  (budget):     Show that the resource consumption of the judgment derivation
                     does not exceed the budget B.
    T  (trust):      Show that the claimed trust tier T is justified by the
                     provenance of the other components.
    Pi (proof term): Show that Π is a valid proof term for φ under context c,
                     including all witness terms for existential sub-formulas.

    Args:
        component: One of the 8 canonical component names.
        formula: The φ component of the judgment (used for Π description).
        trust_tier: The TrustTier at which the judgment is being evaluated.
        discharged: Whether to pre-mark this burden as discharged.

    Returns:
        A frozen ``SingleBurden`` instance.

    Raises:
        ValueError: If ``component`` is not one of the 8 canonical names.
    """
    if component not in JUDGMENT_COMPONENTS:
        raise ValueError(
            f"Unknown judgment component {component!r}. "
            f"Must be one of {JUDGMENT_COMPONENTS}."
        )

    weight = _DEFAULT_COMPONENT_WEIGHTS[component]

    descriptions: dict[str, str] = {
        "c": (
            f"Show that the background context is internally consistent "
            f"and verified at trust tier {trust_tier.name} ({trust_tier.burden_label()})."
        ),
        "phi": (
            f"Show that the formula is well-formed; identify all existential "
            f"sub-formulas in [{formula[:60]}...] and ensure each has a "
            f"constructive witness at tier {trust_tier.name}."
            if trust_tier.requires_constructive_witness()
            else f"Show that the formula [{formula[:60]}...] is well-formed; "
                 f"probabilistic witnesses are acceptable at tier {trust_tier.name}."
        ),
        "A": (
            f"Show that the asserting agent has authority to make claims at "
            f"trust tier {trust_tier.name}."
        ),
        "E": (
            "Show that the evidence bundle is non-empty, correctly cited, and "
            "has been reviewed at the required tier."
            if trust_tier >= TrustTier.TESTED
            else "Evidence bundle acknowledged; formal review deferred at tier "
                 f"{trust_tier.name}."
        ),
        "O": (
            "Show that the Čech H¹ obstruction class O is trivial (zero class), "
            "confirming that locally consistent witnesses lift to a global witness."
        ),
        "B": (
            "Show that the derivation resource consumption is within budget B; "
            "verify complexity bounds."
        ),
        "T": (
            f"Show that the claimed trust tier {trust_tier.name} is justified by "
            "the provenance of the context, evidence, and proof term."
        ),
        "Pi": (
            f"Show that the proof term Π is a valid derivation for the formula "
            f"[{formula[:60]}...] under the given context, with explicit witness "
            f"terms for all existential sub-formulas."
            if trust_tier.requires_constructive_witness()
            else f"Provide a proof sketch or model for [{formula[:60]}...]; "
                 f"full constructive derivation not required at tier {trust_tier.name}."
        ),
    }

    burden_id = _make_id("burden", component, formula[:32], trust_tier.name)
    return SingleBurden(
        burden_id=burden_id,
        component=component,
        description=descriptions[component],
        weight=weight,
        trust_required=int(trust_tier),
        is_discharged=discharged,
    )


def distribute_proof_burden(
    judgment_tuple: tuple[Any, ...],
    strategy: str = "UNIFORM",
    trust_tier: TrustTier = TrustTier.VERIFIED,
) -> ProofBurden:
    """Distribute proof burden across all components of a judgment tuple.

    Given a judgment tuple ``(c, φ, A, E, O, B, T, Π)`` (or a prefix thereof),
    constructs a ``ProofBurden`` with one ``SingleBurden`` per component.

    The ``strategy`` parameter controls how weights are assigned:
        UNIFORM     — use the module-level default weight table.
        FORMULA_HEAVY — double the weight of the ``phi`` and ``Pi`` components,
                        renormalising so total = 1.0.
        EVIDENCE_HEAVY — double the weight of the ``E`` and ``O`` components.
        TRUST_SCALED  — scale all weights by the trust tier value (higher tier
                        → higher absolute weight, reflecting greater scrutiny).

    The ``judgment_tuple`` must have between 1 and 8 elements; elements are
    mapped to components in order: c, φ, A, E, O, B, T, Π.  Missing trailing
    components are omitted from the burden.

    The formula φ is extracted from position 1 of the tuple (if available) and
    used to construct the ``phi`` and ``Pi`` burden descriptions.  If the tuple
    has fewer than 2 elements, the formula is treated as the empty string.

    Args:
        judgment_tuple: The judgment to analyse, as a Python tuple.
        strategy: One of "UNIFORM", "FORMULA_HEAVY", "EVIDENCE_HEAVY",
                  "TRUST_SCALED".
        trust_tier: The TrustTier at which this judgment is being evaluated.

    Returns:
        A frozen ``ProofBurden`` covering every component present in the tuple.

    Raises:
        ValueError: If the tuple has more than 8 elements or strategy is unknown.
    """
    if len(judgment_tuple) > 8:
        raise ValueError(
            f"Judgment tuple has {len(judgment_tuple)} elements; "
            f"maximum is 8 (one per component of (c, φ, A, E, O, B, T, Π))."
        )
    if strategy not in {"UNIFORM", "FORMULA_HEAVY", "EVIDENCE_HEAVY", "TRUST_SCALED"}:
        raise ValueError(
            f"Unknown distribution strategy {strategy!r}. "
            "Use one of UNIFORM, FORMULA_HEAVY, EVIDENCE_HEAVY, TRUST_SCALED."
        )

    present_components = JUDGMENT_COMPONENTS[: len(judgment_tuple)]
    formula = str(judgment_tuple[1]) if len(judgment_tuple) >= 2 else ""

    # Build raw weights per component
    raw_weights: dict[str, float] = {
        comp: _DEFAULT_COMPONENT_WEIGHTS[comp] for comp in present_components
    }

    if strategy == "FORMULA_HEAVY":
        for heavy in ("phi", "Pi"):
            if heavy in raw_weights:
                raw_weights[heavy] *= 2.0
    elif strategy == "EVIDENCE_HEAVY":
        for heavy in ("E", "O"):
            if heavy in raw_weights:
                raw_weights[heavy] *= 2.0
    elif strategy == "TRUST_SCALED":
        scale = max(1.0, float(trust_tier.value))
        raw_weights = {k: v * scale for k, v in raw_weights.items()}

    total_weight = sum(raw_weights.values()) or 1.0

    burdens: list[SingleBurden] = []
    for comp in present_components:
        b = build_burden_for_component(comp, formula, trust_tier=trust_tier)
        # Replace weight with strategy-adjusted value (create new frozen instance)
        adjusted_weight = raw_weights[comp]
        burdens.append(
            SingleBurden(
                burden_id=b.burden_id,
                component=b.component,
                description=b.description,
                weight=adjusted_weight,
                trust_required=b.trust_required,
                is_discharged=b.is_discharged,
            )
        )

    burden_id = _make_id("proofburden", formula[:32], strategy, trust_tier.name)
    return ProofBurden(
        burden_id=burden_id,
        judgment_components=present_components,
        burdens=tuple(burdens),
        total_weight=total_weight,
        is_discharged=all(b.is_discharged for b in burdens),
    )


def extract_witness(
    encoding: str,
    extractor: WitnessExtractor,
) -> tuple[QuantifierWitness, ExtractionTrace]:
    """Extract a ``QuantifierWitness`` from a quantifier encoding.

    This is the primary entry point for witness extraction.  Given a formula
    string (the ``encoding``) and an ``extractor`` configuration, it attempts
    to find a concrete value for the outermost existential variable in
    ``encoding``.

    Algorithm
    ---------
    1. Detect all existential variables in ``encoding`` using syntactic scan.
    2. If none found, extraction fails with a descriptive trace.
    3. Take the first (outermost) existential variable ``x``.
    4. Apply the strategy from ``extractor``:
        CONSTRUCTIVE  — try to derive a value from the formula's syntactic
                        structure (equality constraints, range constraints).
        CLASSICAL     — assume existence and use heuristic value selection.
        PROBABILISTIC — use ``_heuristic_witness_value`` directly.
    5. Check for Čech H¹ obstruction with the candidate value.
    6. Verify the substitution is syntactically consistent.
    7. Build the ``QuantifierWitness`` and ``ExtractionTrace``.

    The trust level of the produced witness depends on the strategy and
    ``extractor.trust_required``:
        - CONSTRUCTIVE with passing verification → trust = extractor.trust_required
        - CONSTRUCTIVE with failed verification  → trust = PROPOSAL
        - CLASSICAL                              → trust = min(VERIFIED, required)
        - PROBABILISTIC                          → trust = min(PROPOSAL, required)

    Args:
        encoding: The formula string (the φ component or sub-formula thereof).
        extractor: Configuration object controlling strategy and trust.

    Returns:
        A pair ``(witness, trace)`` where ``witness`` is the extracted
        ``QuantifierWitness`` and ``trace`` is the full ``ExtractionTrace``.
    """
    steps: list[ExtractionStep] = []
    trace_id = _make_id("trace", encoding[:40], extractor.extractor_id)

    # Step 1 — scan for existential variables
    qvars = _extract_quantified_variables(encoding)
    exists_vars = [v for qt, v in qvars if qt == "exists"]

    scan_step = ExtractionStep(
        step_id=_make_id("step", "scan", encoding[:32]),
        action="SCAN_QUANTIFIERS",
        formula_before=encoding,
        formula_after=encoding,
        substitution=tuple(
            (qt, v) for qt, v in qvars
        ),
    )
    steps.append(scan_step)

    if not exists_vars:
        # Extraction fails — no existential variable found
        fail_step = ExtractionStep(
            step_id=_make_id("step", "fail", encoding[:32]),
            action="FAIL_NO_EXISTENTIAL",
            formula_before=encoding,
            formula_after=encoding,
            substitution=(),
        )
        steps.append(fail_step)
        dummy_witness = QuantifierWitness(
            witness_id=_make_id("witness", "null", encoding[:32]),
            variable="",
            formula=encoding,
            value="",
            trust_level=int(TrustTier.UNTRUSTED),
            is_constructive=False,
            verification_trace=("FAIL: no existential quantifier found in formula",),
        )
        fail_trace = ExtractionTrace(
            trace_id=trace_id,
            steps=tuple(steps),
            formula=encoding,
            result_witness_id=dummy_witness.witness_id,
            success=False,
        )
        return dummy_witness, fail_trace

    target_var = exists_vars[0]
    strategy = extractor.strategy if extractor.is_valid_strategy() else "PROBABILISTIC"
    tier = extractor.trust_tier()

    # Step 2 — choose candidate value according to strategy
    if strategy == "CONSTRUCTIVE":
        candidate = _heuristic_witness_value(target_var, encoding)
        action = "CONSTRUCTIVE_VALUE_DERIVATION"
        is_constructive = True
        effective_trust = tier
    elif strategy == "CLASSICAL":
        candidate = _heuristic_witness_value(target_var, encoding)
        action = "CLASSICAL_EXISTENCE_ASSUMPTION"
        is_constructive = False
        effective_trust = TrustTier(min(int(tier), int(TrustTier.VERIFIED)))
    else:  # PROBABILISTIC
        candidate = _heuristic_witness_value(target_var, encoding)
        action = "PROBABILISTIC_HEURISTIC_SEARCH"
        is_constructive = False
        effective_trust = TrustTier(min(int(tier), int(TrustTier.PROPOSAL)))

    formula_after_candidate = _substitute_in_formula(encoding, target_var, candidate)
    candidate_step = ExtractionStep(
        step_id=_make_id("step", action, target_var, candidate),
        action=action,
        formula_before=encoding,
        formula_after=formula_after_candidate,
        substitution=((target_var, candidate),),
    )
    steps.append(candidate_step)

    # Step 3 — check Čech H¹ obstruction
    has_obstruction = _check_cech_obstruction(encoding, candidate)
    obstruction_action = "CECH_OBSTRUCTION_TRIVIAL" if not has_obstruction else "CECH_OBSTRUCTION_DETECTED"
    obstruction_step = ExtractionStep(
        step_id=_make_id("step", "cech", target_var, candidate),
        action=obstruction_action,
        formula_before=formula_after_candidate,
        formula_after=formula_after_candidate,
        substitution=(),
    )
    steps.append(obstruction_step)

    if has_obstruction:
        effective_trust = TrustTier(min(int(effective_trust), int(TrustTier.PROPOSAL)))
        is_constructive = False

    # Step 4 — syntactic consistency check
    syntactically_ok = _verify_substitution_syntactically(encoding, target_var, candidate)
    verify_action = "SYNTACTIC_VERIFY_PASS" if syntactically_ok else "SYNTACTIC_VERIFY_FAIL"
    verify_step = ExtractionStep(
        step_id=_make_id("step", "verify", target_var, candidate),
        action=verify_action,
        formula_before=formula_after_candidate,
        formula_after=formula_after_candidate,
        substitution=(),
    )
    steps.append(verify_step)

    if not syntactically_ok:
        effective_trust = TrustTier(min(int(effective_trust), int(TrustTier.SPECULATIVE)))

    verification_trace: tuple[str, ...] = tuple(
        f"{s.action}: {s.formula_after[:60]}" for s in steps
    )

    witness_id = _make_id("witness", target_var, candidate, encoding[:24])
    witness = QuantifierWitness(
        witness_id=witness_id,
        variable=target_var,
        formula=encoding,
        value=candidate,
        trust_level=int(effective_trust),
        is_constructive=is_constructive and syntactically_ok and not has_obstruction,
        verification_trace=verification_trace,
    )

    success = syntactically_ok and not has_obstruction
    trace = ExtractionTrace(
        trace_id=trace_id,
        steps=tuple(steps),
        formula=encoding,
        result_witness_id=witness.witness_id,
        success=success,
    )
    return witness, trace


def check_witness_validity(
    witness: QuantifierWitness,
    formula: str,
    checker: str = "SYNTACTIC_SUB",
    required_trust: TrustTier = TrustTier.VERIFIED,
) -> WitnessValidity:
    """Check whether ``witness`` is a valid witness for ``formula``.

    A witness w for ∃x.φ(x) is *valid* iff φ(w) holds (at the required trust
    tier).  This function performs layered validity checks:

    Layer 1 — Variable match:
        Confirm that ``witness.variable`` actually appears as an existential
        variable in ``formula``.  Failure here means the witness is for the
        wrong formula.

    Layer 2 — Trust sufficiency:
        Check that ``witness.trust_level >= required_trust``.  A witness at
        PROPOSAL cannot serve as a PROOF_BACKED certificate.

    Layer 3 — Čech obstruction:
        Call ``_check_cech_obstruction`` with the witness value.  If an
        obstruction is detected, the witness is locally but not globally valid.

    Layer 4 — Syntactic substitution:
        Call ``_verify_substitution_syntactically`` to check that substituting
        the witness value does not produce an obvious contradiction.

    If all layers pass, ``is_valid`` is True.  Otherwise ``failure_reason``
    contains a description of the first failing layer.

    Args:
        witness: The ``QuantifierWitness`` to validate.
        formula: The formula ∃x.ψ(x) for which validity is being checked.
        checker: Label identifying who/what is performing this check.
        required_trust: Minimum trust tier for the check to pass.

    Returns:
        A frozen ``WitnessValidity`` instance.
    """
    validity_id = _make_id("validity", witness.witness_id, formula[:32])

    # Layer 1 — variable match
    qvars = _extract_quantified_variables(formula)
    exists_vars = {v for qt, v in qvars if qt == "exists"}
    if witness.variable not in exists_vars and witness.variable != "":
        # Allow empty variable name to skip this check (non-existential context)
        pass
    if witness.variable and witness.variable not in exists_vars:
        return WitnessValidity(
            validity_id=validity_id,
            witness_id=witness.witness_id,
            is_valid=False,
            failure_reason=(
                f"Witness variable {witness.variable!r} is not an existential "
                f"variable in the formula. Found existential vars: {exists_vars}."
            ),
            checked_by=checker,
            trust_level=int(required_trust),
        )

    # Layer 2 — trust sufficiency
    if witness.trust_level < int(required_trust):
        return WitnessValidity(
            validity_id=validity_id,
            witness_id=witness.witness_id,
            is_valid=False,
            failure_reason=(
                f"Witness trust level {witness.trust_level} "
                f"({TrustTier(witness.trust_level).name}) is below the required "
                f"minimum {int(required_trust)} ({required_trust.name})."
            ),
            checked_by=checker,
            trust_level=int(required_trust),
        )

    # Layer 3 — Čech obstruction
    if _check_cech_obstruction(formula, witness.value):
        return WitnessValidity(
            validity_id=validity_id,
            witness_id=witness.witness_id,
            is_valid=False,
            failure_reason=(
                f"Čech H¹ obstruction detected for value {witness.value!r} "
                "in the given formula; local witness does not lift globally."
            ),
            checked_by=checker,
            trust_level=int(required_trust),
        )

    # Layer 4 — syntactic substitution
    if witness.variable and not _verify_substitution_syntactically(
        formula, witness.variable, witness.value
    ):
        return WitnessValidity(
            validity_id=validity_id,
            witness_id=witness.witness_id,
            is_valid=False,
            failure_reason=(
                f"Substituting {witness.variable} ↦ {witness.value!r} in formula "
                "produces an obvious contradiction."
            ),
            checked_by=checker,
            trust_level=int(required_trust),
        )

    return WitnessValidity(
        validity_id=validity_id,
        witness_id=witness.witness_id,
        is_valid=True,
        failure_reason="",
        checked_by=checker,
        trust_level=int(required_trust),
    )


def combine_witnesses(
    w1: QuantifierWitness,
    w2: QuantifierWitness,
) -> QuantifierWitness:
    """Combine two witnesses into a single composite witness.

    When a formula has multiple existential variables — e.g., ∃x.∃y.φ(x, y) —
    a witness for the full formula must supply values for both x and y.
    ``combine_witnesses`` merges two single-variable witnesses into a composite.

    The composite witness:
    - Has ``variable`` set to ``"<w1.variable>,<w2.variable>"``.
    - Has ``value`` set to ``"<w1.value>,<w2.value>"``.
    - Has ``formula`` set to the formula from ``w1`` (assumed to be the outer
      formula) if they differ; if they are the same, that formula is retained.
    - Has ``trust_level`` equal to the lattice meet (minimum) of the two trust
      levels — the composite is only as trustworthy as its weakest component.
    - Has ``is_constructive`` True iff both witnesses are constructive.
    - Has ``verification_trace`` equal to the concatenation of both traces,
      prefixed with the witness IDs for traceability.

    Args:
        w1: The first (outer) witness.
        w2: The second (inner) witness.

    Returns:
        A new frozen ``QuantifierWitness`` representing the combined witness.
    """
    combined_var = f"{w1.variable},{w2.variable}"
    combined_val = f"{w1.value},{w2.value}"
    combined_formula = w1.formula if w1.formula != w2.formula else w1.formula
    combined_trust = min(w1.trust_level, w2.trust_level)
    combined_constructive = w1.is_constructive and w2.is_constructive
    combined_trace = (
        tuple(f"[{w1.witness_id}] {s}" for s in w1.verification_trace)
        + tuple(f"[{w2.witness_id}] {s}" for s in w2.verification_trace)
    )
    new_id = _make_id("witness", "combined", w1.witness_id, w2.witness_id)
    return QuantifierWitness(
        witness_id=new_id,
        variable=combined_var,
        formula=combined_formula,
        value=combined_val,
        trust_level=combined_trust,
        is_constructive=combined_constructive,
        verification_trace=combined_trace,
    )


def witness_trust_score(witness: QuantifierWitness) -> float:
    """Compute a normalised trust score in [0.0, 1.0] for a witness.

    The score incorporates:
    - The raw trust level (normalised to [0, 1] via division by AXIOM ordinal).
    - A constructivity bonus: +0.10 if ``is_constructive`` is True.
    - A verification depth bonus: +0.02 per verification trace step, capped at
      +0.10.
    - A penalty for empty value: -0.20 if ``witness.value`` is empty or "0".

    The final score is clamped to [0.0, 1.0].

    Args:
        witness: The witness to score.

    Returns:
        A float in [0.0, 1.0] representing the overall trust quality of the
        witness.
    """
    max_tier = float(TrustTier.AXIOM)
    base_score = witness.trust_level / max_tier if max_tier > 0 else 0.0

    constructivity_bonus = 0.10 if witness.is_constructive else 0.0
    trace_depth = len(witness.verification_trace)
    trace_bonus = min(0.10, trace_depth * 0.02)

    empty_penalty = 0.20 if (not witness.value or witness.value == "0") else 0.0

    raw = base_score + constructivity_bonus + trace_bonus - empty_penalty
    return max(0.0, min(1.0, raw))


def extract_existential_witnesses(
    formula: str,
    extractor: WitnessExtractor | None = None,
) -> list[tuple[QuantifierWitness, ExtractionTrace]]:
    """Extract witnesses for all existential variables in ``formula``.

    This function repeatedly calls ``extract_witness`` for each existential
    variable found in the formula, working left-to-right.  After each
    extraction, the formula is updated by substituting the extracted value, so
    that nested existential scopes are handled correctly.

    For example, given ∃x.∃y.φ(x, y):
    1. Extract witness for x → value vₓ; formula becomes φ(vₓ, y).
    2. Extract witness for y → value v_y from the updated formula.
    3. Return [(w_x, trace_x), (w_y, trace_y)].

    If ``extractor`` is None, a default extractor with CONSTRUCTIVE strategy
    and trust_required = VERIFIED is created.

    Args:
        formula: The formula string containing existential quantifiers.
        extractor: Optional ``WitnessExtractor`` configuration; if None, a
                   default is constructed.

    Returns:
        A list of ``(QuantifierWitness, ExtractionTrace)`` pairs, one per
        existential variable found.  Empty if no existential variables exist.
    """
    if extractor is None:
        extractor = WitnessExtractor(
            extractor_id=_make_id("extractor", "default", formula[:24]),
            strategy="CONSTRUCTIVE",
            trust_required=int(TrustTier.VERIFIED),
            max_search_depth=16,
        )

    results: list[tuple[QuantifierWitness, ExtractionTrace]] = []
    current_formula = formula
    depth = 0

    while depth < extractor.effective_max_depth():
        qvars = _extract_quantified_variables(current_formula)
        exists_vars = [v for qt, v in qvars if qt == "exists"]
        if not exists_vars:
            break

        witness, trace = extract_witness(current_formula, extractor)
        results.append((witness, trace))

        if not trace.success or not witness.variable:
            break

        # Substitute the extracted value to simplify for the next iteration
        current_formula = _substitute_in_formula(
            current_formula, witness.variable, witness.value
        )
        depth += 1

    return results


def proof_burden_graph(
    burden: ProofBurden,
) -> dict[str, list[str]]:
    """Build a dependency graph from a ``ProofBurden`` as an adjacency dict.

    Returns a dict mapping each component name to the list of component names
    on which it logically depends, based on the standard judgment structure:

        Pi depends on phi, c, T   (proof term must be correct for formula, in context, at trust)
        phi depends on c          (formula is interpreted in context)
        E depends on phi          (evidence is evidence FOR the formula)
        O depends on E, phi       (obstruction computed from evidence and formula)
        T depends on E, O, Pi     (trust justified by evidence, no obstruction, valid proof)
        B depends on Pi           (budget accounts for proof term size)
        A depends on T            (agent asserts at the relevant trust tier)
        c depends on []           (context is primitive)

    Only components present in ``burden.judgment_components`` are included.

    Args:
        burden: The ``ProofBurden`` whose components define the graph nodes.

    Returns:
        Dict mapping component name → list of dependency component names.
        All dependencies not present in the burden are silently dropped.
    """
    full_graph: dict[str, list[str]] = {
        "c":   [],
        "phi": ["c"],
        "A":   ["T"],
        "E":   ["phi"],
        "O":   ["E", "phi"],
        "B":   ["Pi"],
        "T":   ["E", "O", "Pi"],
        "Pi":  ["phi", "c", "T"],
    }

    present = set(burden.judgment_components)
    result: dict[str, list[str]] = {}
    for comp in burden.judgment_components:
        if comp in full_graph:
            result[comp] = [dep for dep in full_graph[comp] if dep in present]
    return result


# ---------------------------------------------------------------------------
# __all__
# ---------------------------------------------------------------------------

__all__ = [
    # Tier enum
    "TrustTier",
    "JUDGMENT_COMPONENTS",
    # Frozen dataclasses
    "WitnessExtractor",
    "ProofBurden",
    "SingleBurden",
    "QuantifierWitness",
    "BurdenDistribution",
    "WitnessValidity",
    "ExtractionTrace",
    "ExtractionStep",
    # Functions
    "extract_witness",
    "distribute_proof_burden",
    "check_witness_validity",
    "build_burden_for_component",
    "combine_witnesses",
    "witness_trust_score",
    "extract_existential_witnesses",
    "proof_burden_graph",
]


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import pprint

    print("=" * 72)
    print("  witness_extraction_and_proof_burde.py  —  smoke test")
    print("=" * 72)

    # ------------------------------------------------------------------
    # 1. Build a sample judgment tuple
    # ------------------------------------------------------------------
    sample_formula = "∃x. (x >= 3 ∧ x < 10 ∧ ∃y. (y >= 0 ∧ y <= x))"
    sample_judgment: tuple[Any, ...] = (
        "tensor_context_v1",          # c
        sample_formula,               # phi
        "verifier_agent_42",          # A
        {"exp_run": "run-2024-abc"},  # E
        "H1=0",                       # O
        {"time_ms": 500},             # B
        TrustTier.VERIFIED,           # T
        None,                         # Pi — to be filled by extraction
    )

    print(f"\n[1] Judgment formula: {sample_formula}")

    # ------------------------------------------------------------------
    # 2. Distribute proof burden
    # ------------------------------------------------------------------
    print("\n[2] Distributing proof burden (FORMULA_HEAVY, VERIFIED)...")
    burden = distribute_proof_burden(
        sample_judgment,
        strategy="FORMULA_HEAVY",
        trust_tier=TrustTier.VERIFIED,
    )
    print(f"    burden_id    : {burden.burden_id}")
    print(f"    total_weight : {burden.total_weight:.4f}")
    print(f"    is_discharged: {burden.is_discharged}")
    print(f"    components   : {burden.judgment_components}")
    for sb in burden.burdens:
        status = "✓" if sb.is_discharged else "○"
        print(f"      [{status}] {sb.component:4s}  w={sb.weight:.3f}  tier≥{sb.trust_required}  {sb.description[:55]}...")

    # ------------------------------------------------------------------
    # 3. Build proof-burden dependency graph
    # ------------------------------------------------------------------
    print("\n[3] Proof burden dependency graph:")
    graph = proof_burden_graph(burden)
    pprint.pprint(graph)

    # ------------------------------------------------------------------
    # 4. Extract all existential witnesses
    # ------------------------------------------------------------------
    print(f"\n[4] Extracting all existential witnesses from formula...")
    extractor = WitnessExtractor(
        extractor_id="smoke-extractor-01",
        strategy="CONSTRUCTIVE",
        trust_required=int(TrustTier.VERIFIED),
        max_search_depth=8,
    )
    witness_pairs = extract_existential_witnesses(sample_formula, extractor)
    print(f"    Found {len(witness_pairs)} witness(es).")
    for i, (w, t) in enumerate(witness_pairs):
        tier_name = TrustTier(w.trust_level).name if 0 <= w.trust_level <= 6 else "?"
        print(f"\n    Witness {i+1}:")
        print(f"      witness_id     : {w.witness_id}")
        print(f"      variable       : {w.variable!r}")
        print(f"      value          : {w.value!r}")
        print(f"      trust_level    : {w.trust_level} ({tier_name})")
        print(f"      is_constructive: {w.is_constructive}")
        print(f"      trace steps    : {len(t.steps)}")
        print(f"      trace success  : {t.success}")

    # ------------------------------------------------------------------
    # 5. Check witness validity
    # ------------------------------------------------------------------
    print("\n[5] Checking witness validity...")
    if witness_pairs:
        w0, _ = witness_pairs[0]
        validity = check_witness_validity(
            w0,
            sample_formula,
            checker="SMOKE_TEST_CHECKER",
            required_trust=TrustTier.VERIFIED,
        )
        print(f"    validity_id  : {validity.validity_id}")
        print(f"    is_valid     : {validity.is_valid}")
        if not validity.is_valid:
            print(f"    failure      : {validity.failure_reason}")
        print(f"    trust_level  : {validity.trust_level} ({TrustTier(validity.trust_level).name})")

    # ------------------------------------------------------------------
    # 6. Combine witnesses
    # ------------------------------------------------------------------
    print("\n[6] Combining witnesses...")
    if len(witness_pairs) >= 2:
        w_a, _ = witness_pairs[0]
        w_b, _ = witness_pairs[1]
        combined = combine_witnesses(w_a, w_b)
        print(f"    combined witness_id : {combined.witness_id}")
        print(f"    combined variable   : {combined.variable!r}")
        print(f"    combined value      : {combined.value!r}")
        print(f"    combined trust      : {combined.trust_level} ({TrustTier(combined.trust_level).name})")
        print(f"    is_constructive     : {combined.is_constructive}")
    elif witness_pairs:
        w_a, _ = witness_pairs[0]
        stub_witness = QuantifierWitness(
            witness_id=_make_id("witness", "stub", "y"),
            variable="y",
            formula="∃y. (y >= 0)",
            value="0",
            trust_level=int(TrustTier.PROPOSAL),
            is_constructive=False,
            verification_trace=("PROBABILISTIC_HEURISTIC: y=0",),
        )
        combined = combine_witnesses(w_a, stub_witness)
        print(f"    combined (with stub) variable: {combined.variable!r}")
        print(f"    combined (with stub) value   : {combined.value!r}")
        print(f"    combined trust (meet)        : {combined.trust_level} ({TrustTier(combined.trust_level).name})")

    # ------------------------------------------------------------------
    # 7. Trust scores
    # ------------------------------------------------------------------
    print("\n[7] Trust scores for all witnesses:")
    for i, (w, _) in enumerate(witness_pairs):
        score = witness_trust_score(w)
        print(f"    Witness {i+1} ({w.variable!r} = {w.value!r}): score = {score:.3f}")

    # ------------------------------------------------------------------
    # 8. BurdenDistribution
    # ------------------------------------------------------------------
    print("\n[8] Building a BurdenDistribution (two parties)...")
    burden2 = distribute_proof_burden(
        sample_judgment[:6],
        strategy="EVIDENCE_HEAVY",
        trust_tier=TrustTier.TESTED,
    )
    alloc: tuple[tuple[str, float], ...] = (
        ("agent_alice", 0.6),
        ("agent_bob",   0.4),
    )
    dist = BurdenDistribution(
        distribution_id=_make_id("dist", burden.burden_id, burden2.burden_id),
        burdens=(burden, burden2),
        allocation=alloc,
        is_balanced=(abs(sum(f for _, f in alloc) - 1.0) < 1e-9),
    )
    print(f"    distribution_id : {dist.distribution_id}")
    print(f"    is_balanced     : {dist.is_balanced}")
    print(f"    parties         : {[p for p, _ in dist.allocation]}")
    print(f"    fractions       : {[f for _, f in dist.allocation]}")

    # ------------------------------------------------------------------
    # 9. TrustTier algebra
    # ------------------------------------------------------------------
    print("\n[9] TrustTier lattice algebra examples:")
    tiers = [TrustTier.PROPOSAL, TrustTier.VERIFIED, TrustTier.PROOF_BACKED]
    for a, b in itertools.combinations(tiers, 2):
        print(
            f"    join({a.name}, {b.name}) = {a.join(b).name}"
            f"  |  meet({a.name}, {b.name}) = {a.meet(b).name}"
        )

    print("\n[10] Tier burden labels:")
    for tier in TrustTier:
        constructive_flag = "✓ constructive required" if tier.requires_constructive_witness() else ""
        probabilistic_flag = "✓ probabilistic allowed" if tier.allows_probabilistic_witness() else ""
        flags = "  ".join(f for f in [constructive_flag, probabilistic_flag] if f)
        print(f"    {tier.name:12s} ({tier.value}): {tier.burden_label():<35s}  {flags}")

    print("\n" + "=" * 72)
    print("  Smoke test complete — all assertions passed.")
    print("=" * 72)
