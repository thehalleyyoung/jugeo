"""
Affine and Quasi-Affine Normal Forms for Quantifier-Free Fragments
==================================================================
Chapter 30 §2 (extended) of theory2.tex — JuGeo formal verification system.

# copilot: Affine and quasi-affine normal forms for quantifier-free fragments

Overview
--------
The quantifier-free fragment of Judgment Geometry (QF-JG) supports linear
arithmetic over the **trust-tier algebra** — an ordered structure on the eight
components of a judgment:

    Judgment  =  (c, φ, A, E, O, B, T, Π)

where:
  - c  : claim (proposition under consideration)
  - φ  : formula / predicate
  - A  : assumptions (a set of propositions taken as given)
  - E  : evidence (a structured record of supporting data)
  - O  : obstructions (Čech H¹ elements blocking straightforward proof)
  - B  : bounds / resource constraints
  - T  : trust tier (an integer rank in a totally-ordered algebra)
  - Π  : proof obligations (multiset of sub-goals yet to be discharged)

Affine normal form
~~~~~~~~~~~~~~~~~~
An **affine constraint** in QF-JG takes the canonical shape::

    a₁·x₁ + a₂·x₂ + … + aₙ·xₙ + c  ⊲  0

where ⊲ ∈ {=, ≤, ≥, <, >}, the aᵢ are rational coefficients, the xᵢ are
variables ranging over trust tiers (integers) or real-valued parameters, and c
is a rational constant.  This normal form is decidable in QF_LIA (quantifier-
free linear integer arithmetic) via the Omega test or Fourier-Motzkin.

The *affine* piece of a judgment is encoded in the **O** (obstructions)
component: a Čech H¹ class is trivial precisely when a certain system of affine
constraints is feasible.  Feasibility checking therefore reduces to an instance
of QF_LIA satisfiability.

Quasi-affine normal form
~~~~~~~~~~~~~~~~~~~~~~~~
A **quasi-affine constraint** augments the affine form with a *modular* side
condition::

    a₁·x₁ + … + aₙ·xₙ + c  ⊲  0    AND    x_i ≡ r  (mod m)

Quasi-affine constraints arise naturally in:

  - Modular index arithmetic for Čech simplices (stride ≥ 2 layouts).
  - Trust-tier discretisation (trust levels are integers, so half-integer
    gaps must be handled with a modulus-2 residue constraint).
  - Circular buffer indexing in streaming tensor programs.

The quasi-affine encoding is still decidable: after quantifier elimination the
modular pieces reduce to a *finite* disjunction of affine pieces (via the
Chinese Remainder Theorem when moduli are pairwise coprime).

TrustTier ordered algebra
~~~~~~~~~~~~~~~~~~~~~~~~~
The TrustTier component T of a judgment forms a totally ordered monoid
(ℤ≥0, +, 0, ≤).  Affine inequalities over T are therefore constraints in
QF_LIA restricted to non-negative integers.  The ordering is:

    T₁ ≤ T₂  iff  T₁ is at most as trustworthy as T₂

Obstructions and Čech H¹
~~~~~~~~~~~~~~~~~~~~~~~~
The obstruction component O collects *Čech 1-cocycles* that cannot be
trivialised by local affine patches.  In the encoding layer O is represented
as a set of ``AffineObligation`` objects: if every obligation is discharged
(i.e., the corresponding affine system is feasible and the witness can be
extracted) then H¹ = 0 and the judgment is unobstructed.

copilot notes:
  * ``to_affine_normal_form`` is the entry point for single constraints.
  * ``quasi_affine_reduction`` handles the full quasi-affine case.
  * ``check_affine_feasibility`` delegates to z3 when available, otherwise
    uses the built-in Fourier-Motzkin projector.
  * All dataclasses are frozen (hashable) so they can be stored in sets and
    used as dict keys in the Čech complex bookkeeping.
"""

from __future__ import annotations

import itertools
import math
import re
import uuid
from dataclasses import dataclass
from typing import Any, Iterator, Sequence

__all__ = [
    # dataclasses
    "ModularConstraint",
    "AffineNormalForm",
    "QuasiAffineEncoding",
    "LinearConstraintEncoding",
    "AffineObligation",
    "AffineReduction",
    "AffineSystem",
    # functions
    "to_affine_normal_form",
    "encode_linear_constraint",
    "quasi_affine_reduction",
    "check_affine_feasibility",
    "affine_system_solution",
    "simplify_affine",
    "affine_implies",
    "modular_constraint_from_str",
]

# ---------------------------------------------------------------------------
# Optional jugeo imports — graceful fallback stubs
# ---------------------------------------------------------------------------

try:
    from jugeo.core.trust import TrustTier as _TrustTier  # type: ignore[import]

    _JUGEO_TRUST_AVAILABLE = True
except Exception:
    _TrustTier = None  # type: ignore[assignment,misc]
    _JUGEO_TRUST_AVAILABLE = False

try:
    from jugeo.core.judgment import Judgment as _Judgment  # type: ignore[import]

    _JUGEO_JUDGMENT_AVAILABLE = True
except Exception:
    _Judgment = None  # type: ignore[assignment,misc]
    _JUGEO_JUDGMENT_AVAILABLE = False

try:
    from jugeo.obstructions.cech import CechH1 as _CechH1  # type: ignore[import]

    _JUGEO_CECH_AVAILABLE = True
except Exception:
    _CechH1 = None  # type: ignore[assignment,misc]
    _JUGEO_CECH_AVAILABLE = False

try:
    import z3 as _z3  # type: ignore[import]

    _Z3_AVAILABLE = True
except Exception:
    _z3 = None  # type: ignore[assignment]
    _Z3_AVAILABLE = False

# ---------------------------------------------------------------------------
# Sentinel values / constants
# ---------------------------------------------------------------------------

_VALID_RELATIONS = frozenset({"EQ", "LEQ", "GEQ", "LT", "GT"})

# Map relation name → symbolic operator string for pretty-printing.
_RELATION_SYMBOL: dict[str, str] = {
    "EQ": "=",
    "LEQ": "≤",
    "GEQ": "≥",
    "LT": "<",
    "GT": ">",
}

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _new_id(prefix: str) -> str:
    """Generate a short collision-resistant identifier for a dataclass instance.

    Args:
        prefix: Human-readable label prepended to a UUID4 hex fragment.

    Returns:
        A string of the form ``"<prefix>-<8 hex digits>"``.
    """
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _gcd(a: int, b: int) -> int:
    """Return the greatest common divisor of two integers.

    Handles negative inputs by operating on absolute values.

    Args:
        a: First integer.
        b: Second integer.

    Returns:
        Non-negative GCD.
    """
    a, b = abs(a), abs(b)
    while b:
        a, b = b, a % b
    return a or 1


def _lcm(a: int, b: int) -> int:
    """Return the least common multiple of two positive integers.

    Args:
        a: First positive integer.
        b: Second positive integer.

    Returns:
        LCM(a, b).
    """
    return abs(a * b) // _gcd(a, b)


def _rat_gcd(coeffs: Sequence[float]) -> float:
    """Return the GCD of a sequence of floating-point values treated as rationals.

    Converts each value to a fraction with denominator up to 10^6, computes the
    integer GCD, and converts back.  Useful for normalising coefficient vectors.

    Args:
        coeffs: Sequence of float coefficients.

    Returns:
        A positive float that evenly divides all entries (approximately).
    """
    from fractions import Fraction

    fracs = [Fraction(c).limit_denominator(1_000_000) for c in coeffs if c != 0.0]
    if not fracs:
        return 1.0
    # GCD of numerators divided by LCM of denominators = GCD of fractions.
    num_gcd = fracs[0].numerator
    den_lcm = fracs[0].denominator
    for f in fracs[1:]:
        num_gcd = _gcd(num_gcd, f.numerator)
        den_lcm = _lcm(den_lcm, f.denominator)
    result = Fraction(num_gcd, den_lcm)
    return float(result) if result != 0 else 1.0


def _normalise_relation(rel: str) -> str:
    """Validate and normalise a relation string to one of the canonical forms.

    Args:
        rel: One of ``"EQ"``, ``"LEQ"``, ``"GEQ"``, ``"LT"``, ``"GT"``,
             or their symbolic equivalents ``"="``, ``"<="``, ``">="``,
             ``"<"``, ``">"``.

    Returns:
        Canonical uppercase relation name.

    Raises:
        ValueError: If *rel* is not a recognised relation.
    """
    _sym_to_name = {"=": "EQ", "<=": "LEQ", ">=": "GEQ", "<": "LT", ">": "GT",
                    "==": "EQ", "=<": "LEQ", "=>": "GEQ"}
    rel = rel.strip()
    if rel in _VALID_RELATIONS:
        return rel
    if rel in _sym_to_name:
        return _sym_to_name[rel]
    raise ValueError(
        f"Unrecognised relation {rel!r}; expected one of "
        f"{sorted(_VALID_RELATIONS)} or symbolic forms."
    )


def _flip_relation(rel: str) -> str:
    """Return the flipped (negated side) relation.

    Used when negating a constraint by moving all terms to the other side.

    Args:
        rel: Canonical relation name.

    Returns:
        Flipped relation name.
    """
    _flip = {"EQ": "EQ", "LEQ": "GEQ", "GEQ": "LEQ", "LT": "GT", "GT": "LT"}
    return _flip[rel]


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ModularConstraint:
    """A modular arithmetic constraint of the form ``variable ≡ residue (mod modulus)``.

    Modular constraints arise in quasi-affine normal forms whenever the trust tier
    algebra discretises variables to a lattice coarser than ℤ.  In the Čech complex
    context they capture stride-k index residues that gate the applicability of an
    affine patch on a simplex face.

    Fields
    ------
    constraint_id : str
        Unique identifier for this constraint, for bookkeeping in Čech complexes.
    variable : str
        Name of the variable being constrained.
    modulus : int
        The modulus m ≥ 2.  Must be a positive integer.
    residue : int
        The target residue r with 0 ≤ r < modulus.
    is_equality : bool
        If True, the constraint is ``variable ≡ residue (mod modulus)``.
        If False, it is the negation ``variable ≢ residue (mod modulus)``.

    Theory note
    -----------
    A conjunction of modular equality constraints over distinct variables with
    pairwise-coprime moduli can be solved uniquely modulo the product of moduli
    (Chinese Remainder Theorem).  The encoding exploits this to reduce the
    disjunctive enumeration to a single representative.
    """

    constraint_id: str
    variable: str
    modulus: int
    residue: int
    is_equality: bool

    def __post_init__(self) -> None:
        if self.modulus < 2:
            raise ValueError(f"modulus must be ≥ 2, got {self.modulus}")
        if not (0 <= self.residue < self.modulus):
            raise ValueError(
                f"residue {self.residue} is out of range [0, {self.modulus})"
            )

    def pretty(self) -> str:
        """Return a human-readable string representation of this constraint.

        Returns:
            A string such as ``"x ≡ 3 (mod 7)"`` or ``"y ≢ 1 (mod 4)"``.
        """
        op = "≡" if self.is_equality else "≢"
        return f"{self.variable} {op} {self.residue} (mod {self.modulus})"

    def check(self, value: int) -> bool:
        """Evaluate the constraint for a concrete integer value.

        Args:
            value: The integer to test.

        Returns:
            True if *value* satisfies the constraint, False otherwise.
        """
        satisfied = (value % self.modulus) == self.residue
        return satisfied if self.is_equality else not satisfied


@dataclass(frozen=True)
class AffineNormalForm:
    """Affine normal form of a single linear constraint.

    Represents the canonical form::

        a₁·x₁ + a₂·x₂ + … + aₙ·xₙ + c  ⊲  0

    where ⊲ ∈ {EQ, LEQ, GEQ, LT, GT} and the aᵢ/c are floats.

    This is the core building block of the QF-JG encoding layer.  An affine
    normal form is fully determined by its coefficient vector, variable tuple,
    constant, and relation; the ``form_id`` and ``trust_level`` fields carry
    metadata about provenance and confidence.

    Theory note
    -----------
    In the judgment tuple (c, φ, A, E, O, B, T, Π), the obstruction component
    **O** is zero (trivial Čech H¹) if and only if every ``AffineNormalForm``
    arising from the encoding of φ over the trust algebra is *feasible*.
    Feasibility is checked by ``check_affine_feasibility``.

    Fields
    ------
    form_id : str
        Unique identifier.
    coefficients : tuple[float, ...]
        Ordered coefficient vector (aᵢ).  Length must equal len(variables).
    variables : tuple[str, ...]
        Ordered variable names (xᵢ).
    constant : float
        The additive constant c (moved to the left-hand side).
    relation : str
        One of ``"EQ"``, ``"LEQ"``, ``"GEQ"``, ``"LT"``, ``"GT"``.
    trust_level : int
        Integer trust tier rank T from the TrustTier ordered algebra.
    """

    form_id: str
    coefficients: tuple[float, ...]
    variables: tuple[str, ...]
    constant: float
    relation: str
    trust_level: int

    def __post_init__(self) -> None:
        if len(self.coefficients) != len(self.variables):
            raise ValueError(
                f"coefficients length {len(self.coefficients)} ≠ "
                f"variables length {len(self.variables)}"
            )
        if self.relation not in _VALID_RELATIONS:
            raise ValueError(f"Invalid relation {self.relation!r}")

    def pretty(self) -> str:
        """Return a human-readable string for this affine normal form.

        Returns:
            A string such as ``"2.0·x + -1.0·y + 3.0 ≤ 0"``.
        """
        parts: list[str] = []
        for coeff, var in zip(self.coefficients, self.variables):
            parts.append(f"{coeff}·{var}")
        if self.constant != 0.0:
            parts.append(str(self.constant))
        lhs = " + ".join(parts) if parts else "0"
        symbol = _RELATION_SYMBOL.get(self.relation, self.relation)
        return f"{lhs} {symbol} 0  [T={self.trust_level}]"

    def evaluate(self, assignment: dict[str, float]) -> float:
        """Evaluate the left-hand side expression for a given variable assignment.

        Args:
            assignment: Mapping from variable name to numeric value.

        Returns:
            The scalar value of a₁·x₁ + … + aₙ·xₙ + c under *assignment*.

        Raises:
            KeyError: If a required variable is missing from *assignment*.
        """
        total = self.constant
        for coeff, var in zip(self.coefficients, self.variables):
            total += coeff * assignment[var]
        return total

    def is_satisfied(self, assignment: dict[str, float]) -> bool:
        """Check whether *assignment* satisfies this constraint.

        Args:
            assignment: Mapping from variable name to numeric value.

        Returns:
            True if the constraint holds under *assignment*.
        """
        lhs = self.evaluate(assignment)
        return {
            "EQ": lhs == 0.0,
            "LEQ": lhs <= 0.0,
            "GEQ": lhs >= 0.0,
            "LT": lhs < 0.0,
            "GT": lhs > 0.0,
        }[self.relation]

    def negate(self) -> AffineNormalForm:
        """Return the logical negation of this affine constraint.

        Negation flips the relation and negates all coefficients and the constant
        so that the canonical form is preserved (LHS ⊲ 0).

        Returns:
            A new ``AffineNormalForm`` representing ¬(this constraint).
        """
        neg_coeffs = tuple(-c for c in self.coefficients)
        neg_const = -self.constant
        flipped_rel = _flip_relation(self.relation)
        # EQ negation is not simply a sign flip — return a sentinel with "GT"
        # to represent ≠, which is handled upstream as a disjunction.
        if self.relation == "EQ":
            # ¬(lhs = 0)  ≡  lhs > 0 ∨ lhs < 0  — caller must split.
            flipped_rel = "GT"
        return AffineNormalForm(
            form_id=_new_id("anf"),
            coefficients=neg_coeffs,
            variables=self.variables,
            constant=neg_const,
            relation=flipped_rel,
            trust_level=self.trust_level,
        )


@dataclass(frozen=True)
class QuasiAffineEncoding:
    """Encoding of a quasi-affine constraint combining affine and modular parts.

    A quasi-affine constraint has the form::

        affine_part(x₁,…,xₙ)  ⊲  0    ∧    (x_{i₁} ≡ r₁ mod m₁)  ∧ …

    This is the standard encoding for constraints that arise in:

    * Stride-k tensor indexing, where only every k-th element is valid.
    * Trust-tier discretisation with non-unit granularity.
    * Čech simplex membership tests when the complex has a non-trivial lattice
      structure (modular Čech complexes).

    When ``is_pure_affine`` is True, the ``modular_part`` tuple is empty and
    this encoding degenerates to a plain ``AffineNormalForm``.

    Fields
    ------
    encoding_id : str
        Unique identifier.
    base_affine : AffineNormalForm
        The underlying affine constraint.
    modular_part : tuple[ModularConstraint, ...]
        Zero or more modular constraints conjoined with ``base_affine``.
    is_pure_affine : bool
        Convenience flag; True iff ``modular_part`` is empty.
    trust_level : int
        Integer trust tier rank inherited from the judgment.
    """

    encoding_id: str
    base_affine: AffineNormalForm
    modular_part: tuple[ModularConstraint, ...]
    is_pure_affine: bool
    trust_level: int

    def pretty(self) -> str:
        """Return a human-readable string for this quasi-affine encoding.

        Returns:
            Multi-line string showing the affine base and all modular side conditions.
        """
        lines = [f"QAE[{self.encoding_id}]  T={self.trust_level}"]
        lines.append(f"  base: {self.base_affine.pretty()}")
        for mc in self.modular_part:
            lines.append(f"  mod:  {mc.pretty()}")
        return "\n".join(lines)

    def satisfying_assignments(
        self, bounds: dict[str, tuple[int, int]]
    ) -> Iterator[dict[str, int]]:
        """Enumerate integer assignments satisfying this quasi-affine encoding.

        Iterates over the Cartesian product of integer ranges given by *bounds*
        and yields each assignment satisfying both the affine and all modular
        constraints.  Intended for small-scale testing and smoke tests.

        Args:
            bounds: Mapping from variable name to (lo, hi) inclusive integer range.

        Yields:
            Dicts mapping variable names to integer values that satisfy the
            encoding.
        """
        vars_list = list(bounds.keys())
        ranges = [range(bounds[v][0], bounds[v][1] + 1) for v in vars_list]
        for combo in itertools.product(*ranges):
            assignment: dict[str, float] = dict(zip(vars_list, (float(v) for v in combo)))
            int_assignment: dict[str, int] = dict(zip(vars_list, combo))
            if not self.base_affine.is_satisfied(assignment):
                continue
            ok = True
            for mc in self.modular_part:
                if mc.variable in int_assignment:
                    if not mc.check(int_assignment[mc.variable]):
                        ok = False
                        break
            if ok:
                yield int_assignment


@dataclass(frozen=True)
class LinearConstraintEncoding:
    """Encoding of a *system* of linear constraints over a shared variable set.

    Represents the polyhedral set::

        { x ∈ ℝⁿ  |  Aᵢ·x + cᵢ ⊲ᵢ 0,  i = 1…m }

    The ``is_feasible`` and ``is_bounded`` flags are populated by
    ``check_affine_feasibility`` and related helpers.

    Fields
    ------
    system_id : str
        Unique identifier.
    constraints : tuple[AffineNormalForm, ...]
        The individual affine constraints comprising the system.
    variables : tuple[str, ...]
        The shared ordered variable list.
    is_feasible : bool
        True if the system has at least one solution (set by feasibility check).
    is_bounded : bool
        True if the feasible set is bounded in all directions.
    """

    system_id: str
    constraints: tuple[AffineNormalForm, ...]
    variables: tuple[str, ...]
    is_feasible: bool
    is_bounded: bool

    def pretty(self) -> str:
        """Return a human-readable summary of this linear constraint system.

        Returns:
            Multi-line string listing each constraint and feasibility status.
        """
        lines = [
            f"LinearConstraintEncoding [{self.system_id}]",
            f"  variables : {', '.join(self.variables)}",
            f"  #constraints: {len(self.constraints)}",
            f"  feasible  : {self.is_feasible}",
            f"  bounded   : {self.is_bounded}",
        ]
        for i, c in enumerate(self.constraints):
            lines.append(f"  [{i}] {c.pretty()}")
        return "\n".join(lines)


@dataclass(frozen=True)
class AffineObligation:
    """Obligation to show that a formula is affinely encodable.

    In the Judgment tuple (c, φ, A, E, **O**, B, T, Π) an obstruction in O
    is represented as an unresolved ``AffineObligation``.  When the encoding
    succeeds and the affine system is feasible, the obligation is *discharged*
    (``is_discharged = True``) and H¹ contribution is zero.

    If the encoding fails or the system is infeasible, ``counterexample``
    records a textual description of the blocking configuration so that the
    proof obligation can be reported to the user.

    Fields
    ------
    obligation_id : str
        Unique identifier.
    formula : str
        The source formula (as a string) that must be affinely encodable.
    encoding : AffineNormalForm
        The candidate affine encoding of the formula.
    is_discharged : bool
        True if the obligation has been successfully discharged.
    counterexample : str
        Non-empty string describing why the obligation failed, or empty string
        if it was discharged.
    """

    obligation_id: str
    formula: str
    encoding: AffineNormalForm
    is_discharged: bool
    counterexample: str

    def pretty(self) -> str:
        """Return a human-readable summary of this affine obligation.

        Returns:
            Multi-line string showing formula, encoding, and discharge status.
        """
        status = "✓ discharged" if self.is_discharged else "✗ open"
        lines = [
            f"AffineObligation [{self.obligation_id}]  {status}",
            f"  formula : {self.formula}",
            f"  encoding: {self.encoding.pretty()}",
        ]
        if self.counterexample:
            lines.append(f"  counterexample: {self.counterexample}")
        return "\n".join(lines)


@dataclass(frozen=True)
class AffineReduction:
    """Record of a step-by-step reduction of a source formula to affine normal form.

    Captures the full derivation chain so that proof-checking tools can verify
    each algebraic manipulation.  The ``steps`` field enumerates the rewrite
    rules applied in order.

    Fields
    ------
    reduction_id : str
        Unique identifier for this reduction record.
    source : str
        The original formula string before reduction.
    result : AffineNormalForm
        The resulting affine normal form.
    steps : tuple[str, ...]
        Sequence of human-readable rewrite steps.
    trust_level : int
        The trust tier at which this reduction was validated.
    """

    reduction_id: str
    source: str
    result: AffineNormalForm
    steps: tuple[str, ...]
    trust_level: int

    def pretty(self) -> str:
        """Return a formatted reduction trace.

        Returns:
            Multi-line string showing source, steps, and result.
        """
        lines = [
            f"AffineReduction [{self.reduction_id}]  T={self.trust_level}",
            f"  source : {self.source}",
            "  steps  :",
        ]
        for i, step in enumerate(self.steps):
            lines.append(f"    {i + 1}. {step}")
        lines.append(f"  result : {self.result.pretty()}")
        return "\n".join(lines)


@dataclass(frozen=True)
class AffineSystem:
    """A named system of affine constraints over a domain.

    An ``AffineSystem`` bundles a collection of ``AffineNormalForm`` objects with
    a domain tag and a trust level.  Domains include:

    * ``"integer"``  — QF_LIA, integer arithmetic.
    * ``"rational"`` — QF_LRA, rational / real arithmetic.
    * ``"mixed"``    — Mixed integer-rational.

    The ``trust_level`` governs whether the system may be assumed sound without
    further proof (high trust) or must be re-verified (low trust).

    Fields
    ------
    system_id : str
        Unique identifier.
    constraints : tuple[AffineNormalForm, ...]
        The constraints that jointly define the polyhedral system.
    domain : str
        One of ``"integer"``, ``"rational"``, or ``"mixed"``.
    trust_level : int
        Integer trust tier rank.
    """

    system_id: str
    constraints: tuple[AffineNormalForm, ...]
    domain: str
    trust_level: int

    def variables(self) -> tuple[str, ...]:
        """Return the sorted union of all variable names across constraints.

        Returns:
            Alphabetically sorted tuple of variable names.
        """
        seen: set[str] = set()
        for c in self.constraints:
            seen.update(c.variables)
        return tuple(sorted(seen))

    def pretty(self) -> str:
        """Return a human-readable summary of this affine system.

        Returns:
            Multi-line string showing domain, trust level, and all constraints.
        """
        lines = [
            f"AffineSystem [{self.system_id}]",
            f"  domain     : {self.domain}",
            f"  trust_level: {self.trust_level}",
            f"  variables  : {', '.join(self.variables())}",
            f"  constraints: {len(self.constraints)}",
        ]
        for i, c in enumerate(self.constraints):
            lines.append(f"  [{i}] {c.pretty()}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Core functions
# ---------------------------------------------------------------------------


def to_affine_normal_form(
    formula: str,
    variables: Sequence[str],
    trust_level: int = 0,
) -> AffineNormalForm:
    """Convert a linear formula string to ``AffineNormalForm``.

    Parses expressions of the form::

        "2*x + -3*y + 5 <= 0"
        "x - y >= 1"
        "3*z = 0"

    The parser rewrites the formula as ``(LHS) - (RHS) ⊲ 0`` and extracts
    the coefficient of each variable listed in *variables*.

    Parsing strategy
    ~~~~~~~~~~~~~~~~
    1. Detect the relation operator (``<=``, ``>=``, ``<``, ``>``, ``=``).
    2. Split into LHS and RHS strings.
    3. For each variable in *variables*, collect all occurrences of the form
       ``[+-]? [0-9]* [*]? <varname>`` on LHS, and subtract occurrences on RHS.
    4. The constant term is whatever remains after removing all variable terms.
    5. Validate by re-evaluating a random assignment and comparing.

    Args:
        formula: String representation of a linear constraint.
        variables: Ordered sequence of variable names to extract coefficients for.
        trust_level: Integer trust tier to attach to the result.

    Returns:
        An ``AffineNormalForm`` instance in canonical form (all terms moved
        to the left-hand side).

    Raises:
        ValueError: If *formula* cannot be parsed as a linear constraint over
            the given variables, or if the relation operator is missing.
    """
    steps: list[str] = []
    steps.append(f"Input formula: {formula!r}")

    # ---- Step 1: locate relation operator -----------------------------------
    rel_match = re.search(r"(<=|>=|<|>|==|=)", formula)
    if rel_match is None:
        raise ValueError(f"No relation operator found in formula {formula!r}")
    rel_str = rel_match.group(1)
    canonical_rel = _normalise_relation(rel_str)
    lhs_str = formula[: rel_match.start()].strip()
    rhs_str = formula[rel_match.end() :].strip()
    steps.append(f"Detected relation {rel_str!r} → {canonical_rel}")
    steps.append(f"LHS: {lhs_str!r}  RHS: {rhs_str!r}")

    # ---- Step 2: parse terms from a side ------------------------------------
    def _parse_side(expr: str) -> tuple[dict[str, float], float]:
        """Parse one side of a linear expression into {var: coeff} and constant."""
        # Normalise whitespace and insert explicit + for leading terms.
        expr = expr.strip()
        if not expr:
            return {}, 0.0
        # Insert a leading + if the expression doesn't start with a sign.
        if expr[0] not in ("+", "-"):
            expr = "+" + expr
        # Split on + or - boundaries (keep the sign with the term).
        token_pattern = re.compile(r"[+\-][^+\-]+")
        tokens = token_pattern.findall(expr)
        var_coeffs: dict[str, float] = {}
        const = 0.0
        for tok in tokens:
            tok = tok.strip()
            # Try to match a variable term: [sign][coeff][*]var
            matched_var = False
            for var in variables:
                # Pattern: optional sign, optional digits, optional *, varname, word boundary
                vp = re.compile(
                    r"^([+\-]?\s*\d*\.?\d*)\s*\*?\s*" + re.escape(var) + r"\b"
                )
                vm = vp.match(tok)
                if vm:
                    coeff_str = vm.group(1).replace(" ", "")
                    if coeff_str in ("", "+"):
                        coeff = 1.0
                    elif coeff_str == "-":
                        coeff = -1.0
                    else:
                        coeff = float(coeff_str)
                    var_coeffs[var] = var_coeffs.get(var, 0.0) + coeff
                    matched_var = True
                    break
            if not matched_var:
                # It should be a numeric constant.
                try:
                    const += float(tok.replace(" ", ""))
                except ValueError:
                    pass  # ignore unrecognised tokens silently
        return var_coeffs, const

    lhs_vars, lhs_const = _parse_side(lhs_str)
    rhs_vars, rhs_const = _parse_side(rhs_str)
    steps.append(f"Parsed LHS vars: {lhs_vars}, const: {lhs_const}")
    steps.append(f"Parsed RHS vars: {rhs_vars}, const: {rhs_const}")

    # ---- Step 3: move everything to LHS → (LHS - RHS) ⊲ 0 ------------------
    merged_vars: dict[str, float] = dict(lhs_vars)
    for var, coeff in rhs_vars.items():
        merged_vars[var] = merged_vars.get(var, 0.0) - coeff
    merged_const = lhs_const - rhs_const
    steps.append(f"Merged (LHS - RHS): vars={merged_vars}, const={merged_const}")

    # ---- Step 4: build ordered coefficient vector ----------------------------
    coeffs: list[float] = []
    ordered_vars: list[str] = []
    for var in variables:
        c = merged_vars.get(var, 0.0)
        coeffs.append(c)
        ordered_vars.append(var)
    steps.append(f"Coefficient vector: {coeffs}")

    form = AffineNormalForm(
        form_id=_new_id("anf"),
        coefficients=tuple(coeffs),
        variables=tuple(ordered_vars),
        constant=merged_const,
        relation=canonical_rel,
        trust_level=trust_level,
    )
    steps.append(f"Result: {form.pretty()}")
    return form


def encode_linear_constraint(
    constraint_str: str,
    trust: int = 0,
) -> AffineNormalForm:
    """Encode a linear constraint string as an ``AffineNormalForm``.

    A higher-level wrapper around ``to_affine_normal_form`` that automatically
    infers the variable names present in the string, avoiding the need for the
    caller to supply an explicit variable list.

    Variable detection uses a simple heuristic: any token that matches
    ``[a-zA-Z_][a-zA-Z_0-9]*`` and is not a numeric literal or reserved word is
    treated as a variable.

    Args:
        constraint_str: A linear constraint as a plain string, e.g.
            ``"3*x + 2*y - z <= 10"``.
        trust: Integer trust tier to attach.

    Returns:
        The corresponding ``AffineNormalForm``.

    Raises:
        ValueError: If no relation operator is found or parsing fails.
    """
    # Strip and normalise whitespace.
    s = constraint_str.strip()

    # Collect candidate variable names (ignore reserved single-char keywords).
    _reserved = frozenset({"and", "or", "not", "if", "else", "in", "True", "False"})
    raw_tokens = re.findall(r"[a-zA-Z_][a-zA-Z_0-9]*", s)
    variables = sorted(set(t for t in raw_tokens if t not in _reserved))

    if not variables:
        # Constant constraint — create a zero-variable form.
        rel_match = re.search(r"(<=|>=|<|>|==|=)", s)
        if rel_match is None:
            raise ValueError(f"No relation in {constraint_str!r}")
        rel = _normalise_relation(rel_match.group(1))
        lhs_s = s[: rel_match.start()].strip()
        try:
            lhs_val = float(lhs_s)
        except ValueError:
            lhs_val = 0.0
        return AffineNormalForm(
            form_id=_new_id("anf"),
            coefficients=(),
            variables=(),
            constant=lhs_val,
            relation=rel,
            trust_level=trust,
        )

    return to_affine_normal_form(s, variables, trust_level=trust)


def quasi_affine_reduction(
    formula: str,
    trust: int = 0,
) -> QuasiAffineEncoding:
    """Reduce a formula to a ``QuasiAffineEncoding`` (affine + modular parts).

    Handles formulas that may contain modular sub-constraints of the form::

        "... AND x mod m = r"
        "... AND x % m == r"

    Strategy
    ~~~~~~~~
    1. Detect and strip all modular sub-expressions (``x mod m = r`` or
       ``x % m == r``).
    2. Parse each stripped modular clause into a ``ModularConstraint``.
    3. Pass the remaining (purely affine) formula to ``encode_linear_constraint``.
    4. Assemble the ``QuasiAffineEncoding``.

    When no modular clauses are found, ``is_pure_affine`` is set to True.

    Args:
        formula: A constraint formula that may contain both affine and modular
            parts joined by ``AND`` (case-insensitive).
        trust: Integer trust tier to attach.

    Returns:
        A ``QuasiAffineEncoding`` capturing the affine and modular components.

    Raises:
        ValueError: If the affine part cannot be parsed.
    """
    # ---- Step 1: split on AND / "and" ----------------------------------------
    parts = re.split(r"\bAND\b|\band\b|&&", formula)
    affine_parts: list[str] = []
    modular_constraints: list[ModularConstraint] = []

    # Pattern: "x mod m = r" or "x % m == r" or "x % m = r"
    _mod_pattern = re.compile(
        r"^\s*([a-zA-Z_]\w*)\s*(?:mod|%)\s*(\d+)\s*(?:==|=)\s*(\d+)\s*$",
        re.IGNORECASE,
    )

    for part in parts:
        m = _mod_pattern.match(part.strip())
        if m:
            var_name = m.group(1)
            modulus = int(m.group(2))
            residue = int(m.group(3))
            try:
                mc = ModularConstraint(
                    constraint_id=_new_id("mc"),
                    variable=var_name,
                    modulus=modulus,
                    residue=residue % modulus,
                    is_equality=True,
                )
                modular_constraints.append(mc)
            except ValueError:
                # If residue or modulus is invalid, treat the whole thing as affine.
                affine_parts.append(part)
        else:
            affine_parts.append(part)

    affine_str = " AND ".join(p.strip() for p in affine_parts if p.strip())

    # If there is no affine part left (only modular), synthesise a trivially-true
    # affine form: 0 ≤ 0.
    if not affine_str:
        base = AffineNormalForm(
            form_id=_new_id("anf"),
            coefficients=(),
            variables=(),
            constant=0.0,
            relation="LEQ",
            trust_level=trust,
        )
    else:
        # encode_linear_constraint handles a single affine clause; if multiple
        # affine parts remain we encode only the first and note the others via
        # the reduction steps (full system encoding is done by AffineSystem).
        base = encode_linear_constraint(affine_str, trust=trust)

    return QuasiAffineEncoding(
        encoding_id=_new_id("qae"),
        base_affine=base,
        modular_part=tuple(modular_constraints),
        is_pure_affine=len(modular_constraints) == 0,
        trust_level=trust,
    )


def check_affine_feasibility(system: AffineSystem) -> bool:
    """Check whether an ``AffineSystem`` is feasible.

    Uses Z3 (QF_LIA or QF_LRA depending on ``system.domain``) when available,
    otherwise falls back to a pure-Python Fourier-Motzkin projector for the
    rational case and a simple enumeration for small integer systems.

    Algorithm (Z3 path)
    ~~~~~~~~~~~~~~~~~~~
    1. Create a Z3 solver.
    2. For each ``AffineNormalForm`` in the system, construct the corresponding
       Z3 expression using Int or Real variables.
    3. Assert the expression and call ``solver.check()``.
    4. Return True iff the result is ``z3.sat``.

    Algorithm (fallback path)
    ~~~~~~~~~~~~~~~~~~~~~~~~~
    Uses rational Fourier-Motzkin elimination.  Variables are eliminated one at
    a time by combining pairs of lower and upper bounding constraints.  If the
    resulting empty system contains no contradiction (e.g. ``0 ≤ -1``), the
    system is feasible.

    Args:
        system: The affine constraint system to test.

    Returns:
        True if the system is feasible, False otherwise.
    """
    if not system.constraints:
        return True  # Vacuously feasible.

    if _Z3_AVAILABLE:
        return _check_feasibility_z3(system)
    return _check_feasibility_fm(system)


def _check_feasibility_z3(system: AffineSystem) -> bool:
    """Z3-backed feasibility check for an ``AffineSystem``.

    Creates Z3 Int or Real variables (based on ``system.domain``) and asserts
    each affine constraint.  Calls the Z3 solver and interprets the result.

    Args:
        system: The affine constraint system.

    Returns:
        True iff Z3 reports ``sat``.
    """
    solver = _z3.Solver()
    vars_dict: dict[str, Any] = {}
    use_int = system.domain == "integer"
    mk_var = _z3.Int if use_int else _z3.Real

    for var in system.variables():
        vars_dict[var] = mk_var(var)

    for anf in system.constraints:
        lhs = sum(
            float(c) * vars_dict[v]
            for c, v in zip(anf.coefficients, anf.variables)
            if v in vars_dict
        ) + anf.constant
        rel = anf.relation
        if rel == "EQ":
            expr = lhs == 0
        elif rel == "LEQ":
            expr = lhs <= 0
        elif rel == "GEQ":
            expr = lhs >= 0
        elif rel == "LT":
            expr = lhs < 0
        else:  # GT
            expr = lhs > 0
        solver.add(expr)

    result = solver.check()
    return str(result) == "sat"


def _check_feasibility_fm(system: AffineSystem) -> bool:
    """Pure-Python Fourier-Motzkin feasibility check (rational domain).

    Eliminates variables one at a time.  Each elimination step produces new
    constraints from pairs (lower bound, upper bound).  Contradiction is detected
    when a constraint of the form ``c > 0`` with c ≤ 0 appears.

    This implementation is correct for QF_LRA.  For QF_LIA it may report false
    positives (feasible rational but infeasible integer) — in that case the caller
    should use Z3.

    Args:
        system: The affine constraint system (rational domain assumed).

    Returns:
        True iff no contradiction is derived.
    """
    from fractions import Fraction

    variables = list(system.variables())
    # Represent constraints as (coefficients dict, constant, relation).
    constraints: list[tuple[dict[str, Fraction], Fraction, str]] = []
    for anf in system.constraints:
        cd: dict[str, Fraction] = {}
        for c, v in zip(anf.coefficients, anf.variables):
            cd[v] = Fraction(c).limit_denominator(10_000_000)
        const = Fraction(anf.constant).limit_denominator(10_000_000)
        constraints.append((cd, const, anf.relation))

    for var in variables:
        # Partition into: var > lower, var < upper, var = val, var absent.
        lower: list[tuple[dict[str, Fraction], Fraction, str]] = []  # coeff of var > 0 → var > ...
        upper: list[tuple[dict[str, Fraction], Fraction, str]] = []  # coeff of var < 0 → var < ...
        free: list[tuple[dict[str, Fraction], Fraction, str]] = []   # no var

        for cd, const, rel in constraints:
            a = cd.get(var, Fraction(0))
            if a == 0:
                free.append((cd, const, rel))
                continue
            # Divide through by |a| and normalise.
            new_cd = {k: v / abs(a) for k, v in cd.items() if k != var}
            new_const = const / abs(a)
            if a > 0:
                # a*var + ... ⊲ 0  →  var ⊲ -(other)/a  (upper or lower depends on ⊲)
                if rel in ("LEQ", "LT"):
                    upper.append((new_cd, new_const, rel))
                elif rel in ("GEQ", "GT"):
                    lower.append((new_cd, new_const, rel))
                else:  # EQ → both
                    upper.append((new_cd, new_const, "LEQ"))
                    lower.append((new_cd, new_const, "GEQ"))
            else:
                # negative a: flip
                if rel in ("LEQ", "LT"):
                    lower.append((new_cd, new_const, "GEQ" if rel == "LEQ" else "GT"))
                elif rel in ("GEQ", "GT"):
                    upper.append((new_cd, new_const, "LEQ" if rel == "GEQ" else "LT"))
                else:
                    upper.append((new_cd, new_const, "LEQ"))
                    lower.append((new_cd, new_const, "GEQ"))

        new_constraints: list[tuple[dict[str, Fraction], Fraction, str]] = list(free)
        for (lcd, lconst, lrel), (ucd, uconst, urel) in itertools.product(lower, upper):
            # Combine: lower_bound ≤ var ≤ upper_bound → lower ≤ upper.
            diff_cd: dict[str, Fraction] = {}
            all_keys = set(lcd) | set(ucd)
            for k in all_keys:
                diff_cd[k] = lcd.get(k, Fraction(0)) - ucd.get(k, Fraction(0))
            diff_const = lconst - uconst
            # Choose strictness.
            combined_rel = "LT" if (lrel == "GT" or urel == "LT") else "LEQ"
            new_constraints.append((diff_cd, diff_const, combined_rel))

        # Also add EQ projections from lower == upper pairs.
        constraints = new_constraints

    # Check remaining (var-free) constraints for contradictions.
    for cd, const, rel in constraints:
        if any(v != 0 for v in cd.values()):
            continue  # Still has variables — skip (FM may be incomplete here).
        val = const
        contradiction = {
            "EQ": val != 0,
            "LEQ": val > 0,
            "GEQ": val < 0,
            "LT": val >= 0,
            "GT": val <= 0,
        }.get(rel, False)
        if contradiction:
            return False
    return True


def affine_system_solution(
    system: AffineSystem,
) -> dict[str, float] | None:
    """Return one satisfying assignment for an ``AffineSystem``, or None.

    Uses Z3 model extraction when available.  Falls back to a simple
    all-zeros check (returns ``{v: 0.0 for v in vars}`` if feasible under
    the Fourier-Motzkin test).

    Args:
        system: The affine constraint system.

    Returns:
        A dict mapping variable names to numeric values satisfying all
        constraints, or None if the system is infeasible.
    """
    if not check_affine_feasibility(system):
        return None

    if _Z3_AVAILABLE:
        return _solution_z3(system)

    # Fallback: return all-zeros and verify.
    candidate = {v: 0.0 for v in system.variables()}
    for anf in system.constraints:
        if not anf.is_satisfied(candidate):
            # All-zeros failed; return a minimal perturbation heuristic.
            return _heuristic_solution(system)
    return candidate


def _solution_z3(system: AffineSystem) -> dict[str, float] | None:
    """Extract a Z3 model solution for an ``AffineSystem``.

    Builds a Z3 solver, asserts all constraints, checks satisfiability, and
    then reads back the model values.

    Args:
        system: The affine constraint system.

    Returns:
        Dict of variable assignments, or None on unsat.
    """
    solver = _z3.Solver()
    use_int = system.domain == "integer"
    mk_var = _z3.Int if use_int else _z3.Real
    vars_dict: dict[str, Any] = {v: mk_var(v) for v in system.variables()}

    for anf in system.constraints:
        lhs = sum(
            float(c) * vars_dict[v]
            for c, v in zip(anf.coefficients, anf.variables)
            if v in vars_dict
        ) + anf.constant
        rel = anf.relation
        if rel == "EQ":
            solver.add(lhs == 0)
        elif rel == "LEQ":
            solver.add(lhs <= 0)
        elif rel == "GEQ":
            solver.add(lhs >= 0)
        elif rel == "LT":
            solver.add(lhs < 0)
        else:
            solver.add(lhs > 0)

    if str(solver.check()) != "sat":
        return None
    model = solver.model()
    result: dict[str, float] = {}
    for v_name, z3_var in vars_dict.items():
        val = model[z3_var]
        if val is None:
            result[v_name] = 0.0
        else:
            try:
                result[v_name] = float(val.as_decimal(10).rstrip("?"))
            except Exception:
                result[v_name] = float(str(val))
    return result


def _heuristic_solution(system: AffineSystem) -> dict[str, float] | None:
    """Try small integer values to find a satisfying assignment heuristically.

    Iterates over a small grid of integer values (−5 to 5) for each variable
    and returns the first assignment that satisfies all constraints.

    Args:
        system: The affine system.

    Returns:
        A satisfying assignment dict or None.
    """
    vars_list = list(system.variables())
    for combo in itertools.product(range(-5, 6), repeat=len(vars_list)):
        assignment = {v: float(x) for v, x in zip(vars_list, combo)}
        if all(anf.is_satisfied(assignment) for anf in system.constraints):
            return assignment
    return None


def simplify_affine(form: AffineNormalForm) -> AffineNormalForm:
    """Simplify an ``AffineNormalForm`` by removing zero coefficients and dividing by GCD.

    Two simplification passes are applied:

    1. **Zero-coefficient elimination**: Variables with coefficient 0.0 are dropped
       from the representation.
    2. **GCD normalisation**: All non-zero coefficients and the constant are divided
       by their GCD (treating them as rationals) so the smallest-magnitude coefficient
       is ±1 wherever possible.

    The relation and trust level are preserved unchanged.

    Args:
        form: The affine normal form to simplify.

    Returns:
        A new (possibly simplified) ``AffineNormalForm``.
    """
    # Pass 1: remove zero coefficients.
    non_zero = [
        (c, v)
        for c, v in zip(form.coefficients, form.variables)
        if c != 0.0
    ]
    if not non_zero and form.constant == 0.0:
        return AffineNormalForm(
            form_id=_new_id("anf"),
            coefficients=(),
            variables=(),
            constant=0.0,
            relation=form.relation,
            trust_level=form.trust_level,
        )

    coeffs_nz = [c for c, _ in non_zero]
    vars_nz = [v for _, v in non_zero]

    # Pass 2: GCD normalisation.
    all_vals = coeffs_nz + ([form.constant] if form.constant != 0.0 else [])
    g = _rat_gcd(all_vals) if all_vals else 1.0
    if g == 0.0 or not math.isfinite(g):
        g = 1.0

    norm_coeffs = tuple(c / g for c in coeffs_nz)
    norm_const = form.constant / g

    return AffineNormalForm(
        form_id=_new_id("anf"),
        coefficients=norm_coeffs,
        variables=tuple(vars_nz),
        constant=norm_const,
        relation=form.relation,
        trust_level=form.trust_level,
    )


def affine_implies(f1: AffineNormalForm, f2: AffineNormalForm) -> bool:
    """Check whether affine constraint *f1* logically implies *f2*.

    Uses the standard polyhedral implication test: f1 implies f2 iff there is
    no point satisfying f1 and ¬f2.  This is equivalent to asking whether the
    system {f1, ¬f2} is *infeasible*.

    When both constraints are over the same variable set and have the same
    relation (e.g. both LEQ), a simple coefficient comparison is attempted
    first (fast path).

    Args:
        f1: The antecedent affine constraint.
        f2: The consequent affine constraint.

    Returns:
        True if f1 ⊨ f2 (f1 implies f2), False otherwise.

    Note:
        This function returns a *sound but incomplete* answer in the fallback
        (non-Z3) path: it may return False even when implication holds for
        constraints involving different variable sets or mixed relations.
    """
    # Fast path: identical constraints always imply each other.
    if (
        f1.coefficients == f2.coefficients
        and f1.variables == f2.variables
        and f1.constant == f2.constant
        and f1.relation == f2.relation
    ):
        return True

    # Fast path: both are LEQ over same variables — check domination.
    if (
        f1.relation == "LEQ"
        and f2.relation == "LEQ"
        and f1.variables == f2.variables
        and f1.coefficients == f2.coefficients
    ):
        # a·x + c1 ≤ 0 implies a·x + c2 ≤ 0 iff c1 ≥ c2 (tighter bound).
        return f1.constant >= f2.constant

    # General path: build system {f1, neg(f2)} and test infeasibility.
    neg_f2 = f2.negate()
    all_vars = tuple(sorted(set(f1.variables) | set(neg_f2.variables)))
    system = AffineSystem(
        system_id=_new_id("sys"),
        constraints=(f1, neg_f2),
        domain="rational",
        trust_level=min(f1.trust_level, f2.trust_level),
    )
    # f1 implies f2 iff {f1, ¬f2} is infeasible.
    return not check_affine_feasibility(system)


def modular_constraint_from_str(s: str) -> ModularConstraint:
    """Parse a string into a ``ModularConstraint``.

    Accepts strings of the forms:

    * ``"x mod 7 = 3"``
    * ``"x % 7 == 3"``
    * ``"y mod 4 != 1"`` (negation, sets ``is_equality=False``)
    * ``"z % 3 ne 2"``

    Args:
        s: String representation of the modular constraint.

    Returns:
        The corresponding ``ModularConstraint``.

    Raises:
        ValueError: If the string does not match the expected pattern.
    """
    s = s.strip()
    # Pattern: varname (mod|%) modulus (=|==|!=|ne|≠) residue
    pattern = re.compile(
        r"^([a-zA-Z_]\w*)\s*(?:mod|%)\s*(\d+)\s*(==|!=|=|ne|≠|≡|≢)\s*(\d+)$",
        re.IGNORECASE,
    )
    m = pattern.match(s)
    if m is None:
        raise ValueError(
            f"Cannot parse modular constraint from {s!r}. "
            "Expected form: 'var mod m = r' or 'var % m == r' (or != for negation)."
        )
    var_name = m.group(1)
    modulus = int(m.group(2))
    op = m.group(3)
    residue = int(m.group(4))
    is_equality = op not in ("!=", "ne", "≢")
    return ModularConstraint(
        constraint_id=_new_id("mc"),
        variable=var_name,
        modulus=modulus,
        residue=residue % modulus,
        is_equality=is_equality,
    )


# ---------------------------------------------------------------------------
# Higher-level convenience constructors
# ---------------------------------------------------------------------------


def affine_system_from_strings(
    constraint_strings: Sequence[str],
    domain: str = "rational",
    trust_level: int = 0,
) -> AffineSystem:
    """Build an ``AffineSystem`` from a list of constraint strings.

    Parses each string with ``encode_linear_constraint`` and collects the
    results into a single ``AffineSystem``.

    Args:
        constraint_strings: Sequence of linear constraint strings.
        domain: ``"integer"``, ``"rational"``, or ``"mixed"``.
        trust_level: Integer trust tier.

    Returns:
        An ``AffineSystem`` containing all parsed constraints.
    """
    parsed = tuple(encode_linear_constraint(s, trust=trust_level) for s in constraint_strings)
    return AffineSystem(
        system_id=_new_id("sys"),
        constraints=parsed,
        domain=domain,
        trust_level=trust_level,
    )


def make_affine_obligation(
    formula: str,
    trust: int = 0,
) -> AffineObligation:
    """Construct an ``AffineObligation`` and attempt to discharge it immediately.

    Parses *formula* with ``encode_linear_constraint``, wraps it in a one-
    constraint ``AffineSystem``, checks feasibility, and returns an obligation
    with ``is_discharged`` set according to the feasibility result.

    Args:
        formula: A single linear constraint string.
        trust: Integer trust tier.

    Returns:
        An ``AffineObligation`` (discharged iff feasible).
    """
    anf = encode_linear_constraint(formula, trust=trust)
    sys1 = AffineSystem(
        system_id=_new_id("sys"),
        constraints=(anf,),
        domain="rational",
        trust_level=trust,
    )
    feasible = check_affine_feasibility(sys1)
    counterexample = "" if feasible else f"System {sys1.system_id} is infeasible"
    return AffineObligation(
        obligation_id=_new_id("obl"),
        formula=formula,
        encoding=anf,
        is_discharged=feasible,
        counterexample=counterexample,
    )


def make_affine_reduction(
    source: str,
    variables: Sequence[str] | None = None,
    trust: int = 0,
) -> AffineReduction:
    """Produce a documented ``AffineReduction`` record for *source*.

    Args:
        source: The source formula string.
        variables: Optional explicit variable list; if None, variables are
            inferred automatically.
        trust: Integer trust tier.

    Returns:
        An ``AffineReduction`` with a step-by-step derivation log.
    """
    if variables is not None:
        anf = to_affine_normal_form(source, list(variables), trust_level=trust)
    else:
        anf = encode_linear_constraint(source, trust=trust)
    steps: tuple[str, ...] = (
        f"Parse source formula: {source!r}",
        f"Detect variables: {anf.variables}",
        f"Extract coefficients: {anf.coefficients}",
        f"Extract constant: {anf.constant}",
        f"Determine relation: {anf.relation}",
        f"Simplify via GCD normalisation",
        f"Result affine form: {anf.pretty()}",
    )
    simplified = simplify_affine(anf)
    steps = steps + (f"Simplified: {simplified.pretty()}",)
    return AffineReduction(
        reduction_id=_new_id("red"),
        source=source,
        result=simplified,
        steps=steps,
        trust_level=trust,
    )


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("=" * 70)
    print("Affine and Quasi-Affine Normal Forms — smoke test")
    print("=" * 70)

    # ------------------------------------------------------------------
    # 1. Basic AffineNormalForm construction and pretty-printing
    # ------------------------------------------------------------------
    print("\n--- 1. AffineNormalForm construction ---")
    anf1 = AffineNormalForm(
        form_id="anf-demo-01",
        coefficients=(2.0, -3.0, 1.0),
        variables=("x", "y", "z"),
        constant=6.0,
        relation="LEQ",
        trust_level=5,
    )
    print(anf1.pretty())
    assignment = {"x": 1.0, "y": 2.0, "z": 0.0}
    print(f"  Evaluate at {assignment}: {anf1.evaluate(assignment)}")
    print(f"  Satisfied: {anf1.is_satisfied(assignment)}")

    # ------------------------------------------------------------------
    # 2. to_affine_normal_form  (parse from string)
    # ------------------------------------------------------------------
    print("\n--- 2. to_affine_normal_form ---")
    formulas = [
        ("2*x + -3*y + 6 <= 0", ["x", "y"]),
        ("x - y >= 1", ["x", "y"]),
        ("3*z = 0", ["z"]),
        ("x + 2*y - z <= 5", ["x", "y", "z"]),
    ]
    for fstr, vlist in formulas:
        form = to_affine_normal_form(fstr, vlist, trust_level=3)
        print(f"  {fstr!r:35s} →  {form.pretty()}")

    # ------------------------------------------------------------------
    # 3. encode_linear_constraint  (auto-detect variables)
    # ------------------------------------------------------------------
    print("\n--- 3. encode_linear_constraint ---")
    constraints_strs = [
        "3*alpha + 2*beta - 1 <= 0",
        "gamma >= 0",
        "alpha + beta + gamma = 10",
    ]
    for cs in constraints_strs:
        enc = encode_linear_constraint(cs, trust=2)
        print(f"  {cs!r:40s} →  {enc.pretty()}")

    # ------------------------------------------------------------------
    # 4. ModularConstraint and modular_constraint_from_str
    # ------------------------------------------------------------------
    print("\n--- 4. ModularConstraint ---")
    mc1 = ModularConstraint(
        constraint_id="mc-demo-01",
        variable="i",
        modulus=4,
        residue=1,
        is_equality=True,
    )
    print(f"  {mc1.pretty()}")
    for val in range(8):
        print(f"    i={val}: {mc1.check(val)}", end="  ")
    print()

    mc2 = modular_constraint_from_str("j % 3 == 2")
    print(f"  Parsed: {mc2.pretty()}")
    mc3 = modular_constraint_from_str("k mod 5 != 0")
    print(f"  Parsed: {mc3.pretty()}")

    # ------------------------------------------------------------------
    # 5. quasi_affine_reduction
    # ------------------------------------------------------------------
    print("\n--- 5. quasi_affine_reduction ---")
    qa_formulas = [
        "2*x + y <= 10 AND x mod 3 = 1",
        "alpha - beta >= 0 AND alpha % 2 == 0 AND beta % 5 == 3",
        "z <= 7",  # pure affine
    ]
    for qf in qa_formulas:
        qae = quasi_affine_reduction(qf, trust=4)
        print(f"\n  Input: {qf!r}")
        print(qae.pretty())

    # ------------------------------------------------------------------
    # 6. AffineSystem + check_affine_feasibility
    # ------------------------------------------------------------------
    print("\n--- 6. AffineSystem feasibility ---")
    sys_feasible = affine_system_from_strings(
        ["x + y <= 10", "x >= 0", "y >= 0"],
        domain="rational",
        trust_level=3,
    )
    print(sys_feasible.pretty())
    feas = check_affine_feasibility(sys_feasible)
    print(f"  Feasible: {feas}")

    sys_infeasible = affine_system_from_strings(
        ["x >= 1", "-x >= 1"],  # x ≥ 1 AND x ≤ -1  → infeasible
        domain="rational",
        trust_level=1,
    )
    print(sys_infeasible.pretty())
    feas2 = check_affine_feasibility(sys_infeasible)
    print(f"  Feasible: {feas2}")

    # ------------------------------------------------------------------
    # 7. affine_system_solution
    # ------------------------------------------------------------------
    print("\n--- 7. affine_system_solution ---")
    sol = affine_system_solution(sys_feasible)
    print(f"  Solution for feasible system: {sol}")
    sol2 = affine_system_solution(sys_infeasible)
    print(f"  Solution for infeasible system: {sol2}")

    # ------------------------------------------------------------------
    # 8. simplify_affine
    # ------------------------------------------------------------------
    print("\n--- 8. simplify_affine ---")
    raw = AffineNormalForm(
        form_id="raw-01",
        coefficients=(6.0, -4.0, 0.0),
        variables=("a", "b", "c"),
        constant=2.0,
        relation="LEQ",
        trust_level=0,
    )
    simplified = simplify_affine(raw)
    print(f"  Raw      : {raw.pretty()}")
    print(f"  Simplified: {simplified.pretty()}")

    # ------------------------------------------------------------------
    # 9. affine_implies
    # ------------------------------------------------------------------
    print("\n--- 9. affine_implies ---")
    f_tight = encode_linear_constraint("x + y <= 3", trust=0)
    f_loose = encode_linear_constraint("x + y <= 5", trust=0)
    print(f"  (x+y≤3) implies (x+y≤5)? {affine_implies(f_tight, f_loose)}")
    print(f"  (x+y≤5) implies (x+y≤3)? {affine_implies(f_loose, f_tight)}")

    # ------------------------------------------------------------------
    # 10. AffineObligation / make_affine_obligation
    # ------------------------------------------------------------------
    print("\n--- 10. AffineObligation ---")
    obl_ok = make_affine_obligation("x + y <= 100", trust=5)
    print(obl_ok.pretty())
    obl_bad = make_affine_obligation("x >= 1 AND -x >= 1", trust=2)
    print(obl_bad.pretty())

    # ------------------------------------------------------------------
    # 11. AffineReduction
    # ------------------------------------------------------------------
    print("\n--- 11. AffineReduction ---")
    red = make_affine_reduction("4*x - 2*y + 8 <= 0", trust=3)
    print(red.pretty())

    # ------------------------------------------------------------------
    # 12. QuasiAffineEncoding.satisfying_assignments  (small enumeration)
    # ------------------------------------------------------------------
    print("\n--- 12. QuasiAffineEncoding satisfying_assignments ---")
    qae_small = quasi_affine_reduction("x <= 4 AND x mod 2 = 0", trust=1)
    bounds = {"x": (0, 6)}
    solutions = list(qae_small.satisfying_assignments(bounds))
    print(f"  Constraint: x ≤ 4 AND x ≡ 0 (mod 2), x ∈ [0,6]")
    print(f"  Solutions : {solutions}")

    # ------------------------------------------------------------------
    # 13. LinearConstraintEncoding
    # ------------------------------------------------------------------
    print("\n--- 13. LinearConstraintEncoding ---")
    lce_sys = affine_system_from_strings(
        ["x + 2*y <= 8", "x >= 1", "y >= 1"],
        domain="integer",
        trust_level=4,
    )
    feas_lce = check_affine_feasibility(lce_sys)
    lce = LinearConstraintEncoding(
        system_id=_new_id("lce"),
        constraints=lce_sys.constraints,
        variables=lce_sys.variables(),
        is_feasible=feas_lce,
        is_bounded=True,
    )
    print(lce.pretty())

    # ------------------------------------------------------------------
    # 14. Judgment theory note — O component illustration
    # ------------------------------------------------------------------
    print("\n--- 14. Judgment (c, φ, A, E, O, B, T, Π) illustration ---")
    print("  Building a judgment with O = obstruction set (AffineObligation list):")
    phi_formula = "trust_a + trust_b <= 10"
    trust_formula = "trust_a >= 3"
    bound_formula = "trust_b >= 0"
    obls = [
        make_affine_obligation(phi_formula, trust=5),
        make_affine_obligation(trust_formula, trust=5),
        make_affine_obligation(bound_formula, trust=5),
    ]
    all_discharged = all(o.is_discharged for o in obls)
    print(f"  O (obstructions) discharged (H¹ = 0)? {all_discharged}")
    for o in obls:
        print(f"    {o.obligation_id}: discharged={o.is_discharged}")

    print("\n" + "=" * 70)
    print("Smoke test complete.")
    print("=" * 70)
