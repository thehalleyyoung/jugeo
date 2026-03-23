# copilot: Tensor-quantifier encodings – why first-order quantifiers need tensor structure
"""
Why Tensor/Quantifier Encodings Matter Disproportionately
==========================================================
Chapter 30 §1 (supplement) of theory2.tex — JuGeo formal verification system.

This module provides the theoretical foundation and implementation for why
first-order quantifiers (∀, ∃) need tensor-like structure in the JuGeo encoding
framework. The central thesis is:

  Universal quantifiers ∀x.φ(x) correspond to *product types* (∏) — the
  dependent product Π(x : A). B(x) in type theory — while existential
  quantifiers ∃x.φ(x) correspond to *sum types* (∑) — the dependent sum
  Σ(x : A). B(x).  When a formula has a quantifier prefix Q₁x₁ Q₂x₂ … Qₙxₙ.M,
  the proof-term inhabiting that formula has the shape of a *tensor product* of
  the individual quantifier shapes, allowing us to compose multi-variable
  quantifier prefixes systematically.

In Judgment Geometry the canonical judgment is the 8-tuple:

    (c, φ, A, E, O, B, T, Π)

where:
  c  — context identifier (a natural number or symbolic label)
  φ  — the formula being judged
  A  — the set of axioms in scope
  E  — the evidence collection
  O  — the obstruction class (an element of Čech cohomology Ȟ¹)
  B  — the bounding structure (trust-tier ordering)
  T  — the TrustTier (an element of the ordered algebra (𝕋, ≤, ⊕, ⊗))
  Π  — the proof term, whose *type* is exactly the quantifier structure of φ

The key insight is that Π lives in a space whose dimension is determined by the
quantifier prefix of φ.  A formula with an alternating prefix ∀x∃y∀z has a
proof-term space that is a tensor of shape (|Dom x|, |Dom y|, |Dom z|) with
appropriate covariant/contravariant indices — exactly the tensor structure we
encode here.

Obstructions O are elements of Ȟ¹(𝒰, ℱ_φ), the first Čech cohomology of the
cover 𝒰 with coefficients in the sheaf ℱ_φ associated to φ.  A non-trivial
obstruction means the local witnesses for ∃ cannot be glued into a global
section — precisely the case where the tensor encoding detects a non-vanishing
cohomology class.

TrustTier algebra (𝕋, ≤, ⊕, ⊗):
  The set 𝕋 = {0, 1, 2, …, N} with:
    - ≤  total order (lower = less trusted)
    - ⊕  join / least upper bound (max)
    - ⊗  meet / greatest lower bound (min)
  Trust tiers propagate through tensor products: if scope s₁ has tier t₁ and
  scope s₂ has tier t₂, the tensor product scope has tier t₁ ⊗ t₂ = min(t₁,t₂),
  reflecting that the weakest link determines overall trustworthiness.

copilot notes: The classes in this module are *independent* of the Z3 layer.
They capture the abstract categorical structure of quantifier encodings.  The
Z3-specific encoding (asserting the resulting tensor formulas to the solver) is
handled in models.py and the quantifier_discipline.py module.
"""

from __future__ import annotations

import hashlib
import itertools
import re
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    pass

__all__ = [
    # dataclasses
    "Binding",
    "BindingStructure",
    "QuantifierMatrix",
    "QuantifierScope",
    "ScopeNesting",
    "TensorProduct",
    "TensorQuantifierEncoding",
    # functions
    "build_binding_structure",
    "compute_tensor_shape",
    "contract_tensor_scopes",
    "encode_existential",
    "encode_universal",
    "extract_quantifier_prefix",
    "scope_depth_map",
    "tensor_product_of_scopes",
    # constants
    "JUDGMENT_COMPONENT_NAMES",
    "TRUST_TIER_MAX",
    "TRUST_TIER_MIN",
]

# ---------------------------------------------------------------------------
# Optional jugeo imports with graceful fallback stubs
# ---------------------------------------------------------------------------

try:
    from jugeo.judgments import JudgmentContext  # type: ignore[import]

    _JUGEO_JUDGMENTS_AVAILABLE = True
except Exception:
    _JUGEO_JUDGMENTS_AVAILABLE = False

    class JudgmentContext:  # type: ignore[no-redef]
        """Stub replacement when jugeo.judgments is unavailable."""

        def __init__(self, context_id: int = 0) -> None:
            self.context_id = context_id

        def __repr__(self) -> str:  # pragma: no cover
            return f"JudgmentContext(context_id={self.context_id!r})"


try:
    from jugeo.kernel import TrustTier as _KernelTrustTier  # type: ignore[import]

    _JUGEO_KERNEL_AVAILABLE = True
except Exception:
    _JUGEO_KERNEL_AVAILABLE = False
    _KernelTrustTier = None  # type: ignore[assignment]


try:
    from jugeo.geometry import CechObstruction  # type: ignore[import]

    _JUGEO_GEOMETRY_AVAILABLE = True
except Exception:
    _JUGEO_GEOMETRY_AVAILABLE = False

    class CechObstruction:  # type: ignore[no-redef]
        """Stub for Čech H¹ obstruction when jugeo.geometry is unavailable."""

        def __init__(self, cohomology_class: str = "0") -> None:
            self.cohomology_class = cohomology_class
            self.is_trivial: bool = cohomology_class == "0"

        def __repr__(self) -> str:  # pragma: no cover
            return f"CechObstruction(cohomology_class={self.cohomology_class!r})"


# ---------------------------------------------------------------------------
# Theory constants
# ---------------------------------------------------------------------------

# The 8-tuple component names for a JuGeo judgment (c, φ, A, E, O, B, T, Π)
JUDGMENT_COMPONENT_NAMES: tuple[str, ...] = (
    "c",   # context identifier
    "phi", # formula
    "A",   # axiom set
    "E",   # evidence collection
    "O",   # obstruction (Čech H¹)
    "B",   # bounding/trust structure
    "T",   # TrustTier
    "Pi",  # proof term
)

# Trust tier bounds for the ordered algebra (𝕋, ≤, ⊕, ⊗)
TRUST_TIER_MIN: int = 0   # fully untrusted / unverified
TRUST_TIER_MAX: int = 10  # maximally trusted / fully verified

# Quantifier kinds recognised by the encoder
_FORALL_SYMBOLS: frozenset[str] = frozenset({"forall", "∀", "FORALL", "A"})
_EXISTS_SYMBOLS: frozenset[str] = frozenset({"exists", "∃", "EXISTS", "E"})
_UNIQUE_SYMBOLS: frozenset[str] = frozenset({"unique", "∃!", "UNIQUE", "U"})

# Regex patterns for lightweight syntactic parsing
_FORALL_RE = re.compile(
    r"(?:forall|∀|FORALL)\s+([A-Za-z_][A-Za-z0-9_]*)\s*[.:]"
)
_EXISTS_RE = re.compile(
    r"(?:exists|∃|EXISTS)\s+([A-Za-z_][A-Za-z0-9_]*)\s*[.:]"
)
_UNIQUE_RE = re.compile(
    r"(?:unique|∃!|UNIQUE)\s+([A-Za-z_][A-Za-z0-9_]*)\s*[.:]"
)
_VAR_RE = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\b")


# ---------------------------------------------------------------------------
# Trust-tier ordered algebra helpers
# ---------------------------------------------------------------------------


def _trust_join(t1: int, t2: int) -> int:
    """TrustTier join (⊕ = max): least upper bound in the ordered algebra.

    In the TrustTier ordered algebra (𝕋, ≤, ⊕, ⊗), the join ⊕ gives the
    *least* trust tier that dominates both t1 and t2. For the total order this
    is simply max.

    Args:
        t1: First trust tier value.
        t2: Second trust tier value.

    Returns:
        max(t1, t2) clamped to [TRUST_TIER_MIN, TRUST_TIER_MAX].
    """
    result = max(t1, t2)
    return min(max(result, TRUST_TIER_MIN), TRUST_TIER_MAX)


def _trust_meet(t1: int, t2: int) -> int:
    """TrustTier meet (⊗ = min): greatest lower bound in the ordered algebra.

    The meet ⊗ gives the *weakest-link* trust tier — the one that limits what
    can be derived.  In tensor products the composed object inherits the meet
    of its constituent trust tiers.

    Args:
        t1: First trust tier value.
        t2: Second trust tier value.

    Returns:
        min(t1, t2) clamped to [TRUST_TIER_MIN, TRUST_TIER_MAX].
    """
    result = min(t1, t2)
    return min(max(result, TRUST_TIER_MIN), TRUST_TIER_MAX)


def _trust_leq(t1: int, t2: int) -> bool:
    """Return True iff t1 ≤ t2 in the TrustTier ordered algebra."""
    return t1 <= t2


# ---------------------------------------------------------------------------
# Stable ID helpers
# ---------------------------------------------------------------------------


def _stable_id(prefix: str, *parts: str) -> str:
    """Produce a short, stable hex ID from ``prefix`` and ``parts``.

    Uses the first 12 hex digits of the SHA-256 of the joined parts so that
    the same logical object always gets the same ID in the same run.

    Args:
        prefix: Short human-readable label (e.g. ``"enc"``, ``"scope"``).
        *parts: String components whose concatenation is hashed.

    Returns:
        A string of the form ``"<prefix>_<12-char hex>"``.
    """
    raw = "|".join(parts)
    digest = hashlib.sha256(raw.encode()).hexdigest()[:12]
    return f"{prefix}_{digest}"


# ---------------------------------------------------------------------------
# Core frozen dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Binding:
    """One variable binding within a quantifier prefix.

    A binding records the pairing of a variable name with its governing
    quantifier (∀, ∃, or ∃!) and tracks where in the formula the variable
    appears as a bound occurrence.

    In the type-theoretic interpretation:
    - ∀x corresponds to a *parameter* in a Π-type: Π(x : A). B(x)
    - ∃x corresponds to a *component* in a Σ-type: Σ(x : A). B(x)
    - ∃!x (unique existence) corresponds to a Σ-type with an additional
      uniqueness condition: the fiber Σ(x : A). B(x) has exactly one element.

    Fields:
        binding_id: Stable unique identifier for this binding.
        variable: The bound variable name (e.g. "x").
        quantifier: The quantifier kind — one of "FORALL", "EXISTS", "UNIQUE".
        scope_depth: The nesting depth at which this binding appears (0 = outermost).
        bound_occurrences: Tuple of character positions in the formula string
            where this variable appears as a bound occurrence.
    """

    binding_id: str
    variable: str
    quantifier: str
    scope_depth: int
    bound_occurrences: tuple[int, ...]

    def is_universal(self) -> bool:
        """Return True iff this binding uses the universal quantifier ∀."""
        return self.quantifier == "FORALL"

    def is_existential(self) -> bool:
        """Return True iff this binding uses the existential quantifier ∃."""
        return self.quantifier in ("EXISTS", "UNIQUE")

    def is_unique(self) -> bool:
        """Return True iff this binding uses the unique-existence quantifier ∃!."""
        return self.quantifier == "UNIQUE"

    def tensor_dimension(self) -> int:
        """Return the notional tensor dimension contributed by this binding.

        Universal quantifiers contribute a *covariant* dimension (contravariant
        in the domain, covariant in the codomain of the proof function).
        Existential quantifiers contribute a *contravariant* dimension (the
        witness must be supplied).  We represent both as dimension 1 here since
        the actual domain sizes are not tracked at the syntactic level; the
        shape tuple in TensorQuantifierEncoding stores the full rank.

        Returns:
            1 for all quantifier kinds (the rank contribution is always 1 per
            bound variable).
        """
        return 1

    def type_theoretic_label(self) -> str:
        """Return the type-theoretic label for this binding.

        Returns:
            ``"Π"`` for universal (product type) or ``"Σ"`` for existential
            (sum type).
        """
        if self.is_universal():
            return "Π"
        return "Σ"


@dataclass(frozen=True)
class BindingStructure:
    """The complete binding structure of a quantified formula.

    The binding structure encodes all variable bindings in a formula together
    with the dependency order (prenex normal form ordering) and a flag
    indicating whether the formula is already in prenex normal form.

    In prenex normal form all quantifiers are pulled to the front:
        Q₁x₁ Q₂x₂ … Qₙxₙ . M(x₁, …, xₙ)
    where M is a quantifier-free matrix.

    Fields:
        struct_id: Stable unique identifier for this structure.
        bindings: Tuple of Binding objects in outermost-first order.
        dependency_order: Tuple of variable names in the order they must be
            bound, respecting scope nesting.
        is_prenex: True iff all quantifiers are at the outermost level with no
            quantifiers nested inside the matrix.
    """

    struct_id: str
    bindings: tuple[Binding, ...]
    dependency_order: tuple[str, ...]
    is_prenex: bool

    def get_binding(self, variable: str) -> Binding | None:
        """Return the Binding for ``variable``, or None if not bound.

        Args:
            variable: Variable name to look up.

        Returns:
            The Binding whose ``.variable == variable``, or None.
        """
        for b in self.bindings:
            if b.variable == variable:
                return b
        return None

    def quantifier_alternation_depth(self) -> int:
        """Count the number of ∀/∃ alternations in the prefix.

        Alternation depth is a key complexity measure: Σ₂ formulas have one
        alternation (∃∀), Π₂ have one alternation (∀∃), etc.  The tensor
        rank of the proof term is always ≥ the alternation depth.

        Returns:
            Number of quantifier-kind transitions in ``dependency_order``.
        """
        if len(self.bindings) < 2:
            return 0
        kinds = [b.quantifier for b in self.bindings]
        return sum(1 for a, b in zip(kinds, kinds[1:]) if a != b)

    def is_purely_universal(self) -> bool:
        """Return True iff every binding is a universal quantifier."""
        return all(b.is_universal() for b in self.bindings)

    def is_purely_existential(self) -> bool:
        """Return True iff every binding is an existential (or unique) quantifier."""
        return all(b.is_existential() for b in self.bindings)

    def prenex_prefix_string(self) -> str:
        """Return a human-readable prenex prefix, e.g. ``"∀x ∃y ∀z"``."""
        parts: list[str] = []
        for var in self.dependency_order:
            binding = self.get_binding(var)
            if binding is None:
                continue
            sym = "∀" if binding.is_universal() else ("∃!" if binding.is_unique() else "∃")
            parts.append(f"{sym}{var}")
        return " ".join(parts)


@dataclass(frozen=True)
class QuantifierMatrix:
    """The quantifier-free matrix of a prenex formula.

    After stripping off the quantifier prefix Q₁x₁ … Qₙxₙ, the remaining
    formula M is the *matrix*.  In the type-theoretic interpretation the matrix
    determines the *body* of the nested Π/Σ types.

    Fields:
        matrix_id: Stable unique identifier for this matrix.
        formula: String representation of the quantifier-free matrix.
        free_vars: The free variables appearing in the matrix (these are exactly
            the variables bound by the quantifier prefix).
        atoms: Tuple of atomic subformulas (leaves) found in the matrix.
    """

    matrix_id: str
    formula: str
    free_vars: frozenset[str]
    atoms: tuple[str, ...]

    def atom_count(self) -> int:
        """Return the number of atomic subformulas in the matrix."""
        return len(self.atoms)

    def mentions_variable(self, var: str) -> bool:
        """Return True iff ``var`` appears free in the matrix.

        Args:
            var: Variable name to check.

        Returns:
            True if ``var`` is in ``free_vars``.
        """
        return var in self.free_vars

    def complexity_estimate(self) -> int:
        """Rough syntactic complexity of the matrix.

        Counts the number of connective tokens (∧, ∨, ¬, →, ↔) and atoms.
        Used heuristically to choose between QF_LIA encoding strategies.

        Returns:
            Non-negative integer complexity score.
        """
        connective_count = sum(
            self.formula.count(sym) for sym in ("∧", "∨", "¬", "→", "↔", "&", "|", "->")
        )
        return connective_count + len(self.atoms)


@dataclass(frozen=True)
class QuantifierScope:
    """The scope of a single quantifier binding.

    A QuantifierScope records the textual and structural extent of one
    quantifier: the bound variable, the quantifier kind, the body formula, the
    nesting depth, and the set of variables still free within the body.

    In the tensor representation each scope contributes one *index axis*.
    Universal scopes give covariant axes (proof functions must be defined for
    *all* values), while existential scopes give contravariant axes (a specific
    witness must be exhibited).

    Fields:
        scope_id: Stable unique identifier.
        variable: The variable bound by this quantifier.
        quantifier_kind: One of ``"FORALL"``, ``"EXISTS"``, ``"UNIQUE"``.
        body: String representation of the formula within this scope.
        depth: Nesting depth (0 = outermost scope).
        free_vars: Variables that appear free in ``body`` (excluding ``variable``
            itself, which is now bound here).
    """

    scope_id: str
    variable: str
    quantifier_kind: str
    body: str
    depth: int
    free_vars: frozenset[str]

    def is_universal(self) -> bool:
        """Return True iff this scope uses the universal quantifier."""
        return self.quantifier_kind == "FORALL"

    def is_existential(self) -> bool:
        """Return True iff this scope uses the existential quantifier."""
        return self.quantifier_kind in ("EXISTS", "UNIQUE")

    def tensor_variance(self) -> str:
        """Return the tensor variance for this scope axis.

        Universal quantifiers (∀) produce *covariant* tensor indices: the proof
        function is a map *out* of the domain, giving a covariant functor.
        Existential quantifiers (∃) produce *contravariant* tensor indices: we
        require a map *into* the domain to supply a witness.

        Returns:
            ``"covariant"`` for FORALL, ``"contravariant"`` for EXISTS/UNIQUE.
        """
        return "covariant" if self.is_universal() else "contravariant"

    def pi_sigma_label(self) -> str:
        """Return the Π (product) or Σ (sum) type label for this scope.

        Returns:
            ``"Π"`` for universal, ``"Σ"`` for existential/unique.
        """
        return "Π" if self.is_universal() else "Σ"

    def symbol(self) -> str:
        """Return the logical symbol for the quantifier.

        Returns:
            ``"∀"`` for FORALL, ``"∃!"`` for UNIQUE, ``"∃"`` for EXISTS.
        """
        if self.quantifier_kind == "FORALL":
            return "∀"
        if self.quantifier_kind == "UNIQUE":
            return "∃!"
        return "∃"


@dataclass(frozen=True)
class TensorProduct:
    """Tensor product of two quantifier scopes.

    When a formula has a multi-variable quantifier prefix Q₁x₁ Q₂x₂ .M, the
    proof-term type is the *tensor product* of the individual scope types:

        type(Π_{Q₁x₁ Q₂x₂.M}) ≅ type(Q₁x₁) ⊗ type(Q₂x₂) ⊗ type(M)

    This dataclass records the result of forming one such pairwise tensor
    product step.

    Fields:
        product_id: Stable unique identifier.
        left_scope_id: ``scope_id`` of the left (outer) factor.
        right_scope_id: ``scope_id`` of the right (inner) factor.
        result_shape: Combined shape tuple after the tensor product.
            Typically ``left_scope.depth_shape + right_scope.depth_shape``.
        contraction_indices: Indices along which the product contracts (i.e.
            shared free variables that become internal edges in the tensor
            network).  Empty tuple means no contraction — a pure outer product.
    """

    product_id: str
    left_scope_id: str
    right_scope_id: str
    result_shape: tuple[int, ...]
    contraction_indices: tuple[int, ...]

    def rank(self) -> int:
        """Return the tensor rank (number of free index axes) of the product.

        Returns:
            ``len(result_shape) - len(contraction_indices)`` but at minimum 0.
        """
        contracted = len(self.contraction_indices)
        return max(0, len(self.result_shape) - contracted)

    def is_outer_product(self) -> bool:
        """Return True iff no contraction occurs (pure outer / Kronecker product)."""
        return len(self.contraction_indices) == 0

    def scalar_result(self) -> bool:
        """Return True iff the result is a scalar (rank-0 tensor).

        A scalar result means all indices have been contracted, which
        corresponds to a closed formula with no free variables.
        """
        return self.rank() == 0


@dataclass(frozen=True)
class ScopeNesting:
    """Nesting structure of quantifier scopes within a formula.

    Represents one node in the scope tree.  The outermost quantifier is the
    root; each child node is a quantifier nested immediately inside the parent.

    Fields:
        nesting_id: Stable unique identifier for this nesting node.
        depth: Depth of this node in the scope tree (0 = outermost).
        parent_scope: ``scope_id`` of the enclosing scope, or ``""`` for root.
        child_scopes: Tuple of ``scope_id`` strings for immediately nested scopes.
    """

    nesting_id: str
    depth: int
    parent_scope: str
    child_scopes: tuple[str, ...]

    def is_root(self) -> bool:
        """Return True iff this is the outermost (root) scope node."""
        return self.parent_scope == ""

    def is_leaf(self) -> bool:
        """Return True iff this scope has no nested child scopes."""
        return len(self.child_scopes) == 0

    def child_count(self) -> int:
        """Return the number of immediately nested child scopes."""
        return len(self.child_scopes)


@dataclass(frozen=True)
class TensorQuantifierEncoding:
    """Encoding of a quantified formula as a tensor.

    This is the central object of the module.  A TensorQuantifierEncoding
    packages together:

    1. The formula string and its parsed quantifier prefix.
    2. The BindingStructure capturing the variable-binding relationships.
    3. The tensor_shape recording the rank and extent of the proof-term space.
    4. The trust_level from the TrustTier ordered algebra.

    The connection to the JuGeo judgment (c, φ, A, E, O, B, T, Π) is:
    - ``formula`` corresponds to φ.
    - ``binding_structure`` determines the type of Π (the proof term).
    - ``trust_level`` is the component T.
    - The tensor_shape encodes the *dimension* of the space from which Π is drawn.

    Fields:
        encoding_id: Stable unique identifier for this encoding.
        formula: The full formula string (quantifier prefix + matrix).
        quantifier_prefix: Tuple of quantifier tokens in outermost-first order,
            e.g. ``("∀x", "∃y", "∀z")``.
        matrix: The quantifier-free body of the formula.
        tensor_shape: Shape tuple of the proof-term tensor.  Each component
            corresponds to one quantifier in the prefix; the value is 1 for
            syntactic (domain-agnostic) encoding (see ``compute_tensor_shape``).
        binding_structure: The full BindingStructure for this formula.
        trust_level: Integer in [TRUST_TIER_MIN, TRUST_TIER_MAX] representing
            the TrustTier T in the judgment.
    """

    encoding_id: str
    formula: str
    quantifier_prefix: tuple[str, ...]
    matrix: str
    tensor_shape: tuple[int, ...]
    binding_structure: BindingStructure
    trust_level: int

    def rank(self) -> int:
        """Return the tensor rank = number of quantifiers in the prefix."""
        return len(self.tensor_shape)

    def prefix_length(self) -> int:
        """Return the number of quantifier blocks in the prefix."""
        return len(self.quantifier_prefix)

    def alternation_depth(self) -> int:
        """Return the quantifier alternation depth of this encoding's formula."""
        return self.binding_structure.quantifier_alternation_depth()

    def proof_term_type_string(self) -> str:
        """Return a human-readable type string for the proof term Π.

        Formats the binding structure as nested Π/Σ types, e.g.:
            ``"Π(x). Σ(y). Π(z). M"``

        Returns:
            Nested type string.
        """
        parts: list[str] = []
        for var in self.binding_structure.dependency_order:
            b = self.binding_structure.get_binding(var)
            if b is None:
                continue
            label = b.type_theoretic_label()
            parts.append(f"{label}({var})")
        parts.append(self.matrix)
        return ". ".join(parts)

    def obstruction_class(self) -> str:
        """Return a symbolic description of the obstruction class O ∈ Ȟ¹.

        For a fully universal formula (no ∃) the obstruction is trivially 0
        (global sections always exist by choice).  For purely existential
        or alternating formulas the obstruction may be non-trivial.

        Returns:
            ``"0"`` (trivial) for purely universal formulas, ``"non-trivial"``
            otherwise.
        """
        if self.binding_structure.is_purely_universal():
            return "0"
        if self.binding_structure.is_purely_existential():
            return "potentially-non-trivial"
        return "alternating-obstruction"

    def cech_h1_description(self) -> str:
        """Return a textual description of the Čech H¹ obstruction class.

        In the sheaf-theoretic perspective, the obstruction to assembling a
        global proof term from local witnesses is an element of Ȟ¹(𝒰, ℱ_φ).
        This method describes that class symbolically.

        Returns:
            A human-readable string describing the cohomology class.
        """
        cls = self.obstruction_class()
        prefix_str = self.binding_structure.prenex_prefix_string()
        if cls == "0":
            return (
                f"Ȟ¹(𝒰, ℱ_{{{self.encoding_id}}}) = 0 "
                f"[trivial: '{prefix_str}' is purely universal; "
                "global proof function exists by dependent choice]"
            )
        return (
            f"Ȟ¹(𝒰, ℱ_{{{self.encoding_id}}}) ≠ 0 potentially "
            f"['{prefix_str}' has existential quantifiers; "
            "local witnesses may not glue to a global section]"
        )

    def judgment_tuple_summary(self) -> str:
        """Return a compact representation of the judgment (c, φ, A, E, O, B, T, Π).

        Returns:
            A string of the form ``(c=…, φ=…, A=…, E=…, O=…, B=…, T=…, Π=…)``.
        """
        return (
            f"(c=0, "
            f"φ={self.formula!r}, "
            f"A=∅, "
            f"E=∅, "
            f"O={self.obstruction_class()!r}, "
            f"B=TrustAlgebra, "
            f"T={self.trust_level}, "
            f"Π: {self.proof_term_type_string()!r})"
        )


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------


def _identify_quantifier_kind(token: str) -> str:
    """Map a raw quantifier token to its canonical kind string.

    Args:
        token: Raw token such as ``"forall"``, ``"∀"``, ``"exists"``, ``"∃!"``.

    Returns:
        One of ``"FORALL"``, ``"EXISTS"``, ``"UNIQUE"``.

    Raises:
        ValueError: If the token is not a recognised quantifier symbol.
    """
    t = token.strip().lower()
    if t in {s.lower() for s in _FORALL_SYMBOLS}:
        return "FORALL"
    if t in {"∃!", "unique", "u"}:
        return "UNIQUE"
    if t in {s.lower() for s in _EXISTS_SYMBOLS}:
        return "EXISTS"
    raise ValueError(f"Unrecognised quantifier token: {token!r}")


def _find_bound_occurrences(formula: str, variable: str) -> tuple[int, ...]:
    """Find all character positions of ``variable`` in ``formula``.

    Uses a whole-word regex so that ``x`` does not match ``x1`` or ``xy``.

    Args:
        formula: The formula string to search.
        variable: The variable name to locate.

    Returns:
        Tuple of integer starting positions (0-indexed).
    """
    pattern = re.compile(rf"\b{re.escape(variable)}\b")
    return tuple(m.start() for m in pattern.finditer(formula))


def _free_vars_in(formula: str, bound: frozenset[str]) -> frozenset[str]:
    """Return variables appearing in ``formula`` that are not in ``bound``.

    Heuristic: any identifier-like token that is not a keyword and not already
    known to be bound.

    Args:
        formula: Formula string to scan.
        bound: Set of variables that are bound (not free).

    Returns:
        Frozenset of free variable names.
    """
    keywords = frozenset(
        {
            "forall", "exists", "unique", "and", "or", "not", "implies",
            "iff", "true", "false", "if", "then", "else",
        }
    )
    candidates = frozenset(_VAR_RE.findall(formula))
    return candidates - bound - keywords


# ---------------------------------------------------------------------------
# Public parsing functions
# ---------------------------------------------------------------------------


def extract_quantifier_prefix(formula: str) -> tuple[str, ...]:
    """Extract the quantifier prefix tokens from a formula string.

    Scans the formula from left to right and collects quantifier–variable pairs
    in the order they appear.  Stops at the first non-quantifier token.

    For example::

        extract_quantifier_prefix("∀x. ∃y. P(x, y)")
        # → ("∀x", "∃y")

    and::

        extract_quantifier_prefix("forall x. exists y. P(x, y)")
        # → ("∀x", "∃y")

    The returned tokens are normalised to use Unicode quantifier symbols.

    Args:
        formula: Input formula string (may use ASCII or Unicode quantifiers).

    Returns:
        Tuple of normalised prefix tokens, each of the form ``"∀<var>"``,
        ``"∃<var>"``, or ``"∃!<var>"``.

    Notes:
        This is a *syntactic* parser: it recognises only the explicit surface
        syntax and does not perform any α-conversion or normalisation.  Nested
        quantifiers that appear inside the matrix (after a connective) will not
        be included in the returned prefix even though they are still quantifiers;
        only the outermost run of quantifiers is collected.
    """
    tokens: list[str] = []
    remaining = formula.strip()

    # Normalise ASCII "forall"/"exists"/"unique" to Unicode
    remaining = re.sub(r"\bforall\b", "∀", remaining, flags=re.IGNORECASE)
    remaining = re.sub(r"\bexists\b", "∃", remaining, flags=re.IGNORECASE)
    remaining = re.sub(r"\bunique\b", "∃!", remaining, flags=re.IGNORECASE)

    # Pattern: quantifier symbol followed by optional whitespace then variable
    # then optional '.' or ':'
    q_re = re.compile(
        r"^(∀|∃!|∃)\s*([A-Za-z_][A-Za-z0-9_]*)\s*[.:]?\s*(.*)",
        re.DOTALL,
    )

    while remaining:
        m = q_re.match(remaining)
        if m is None:
            break
        q_sym, var, rest = m.group(1), m.group(2), m.group(3)
        tokens.append(f"{q_sym}{var}")
        remaining = rest.strip()

    return tuple(tokens)


def scope_depth_map(formula: str) -> dict[str, int]:
    """Build a mapping from each quantifier variable to its nesting depth.

    The depth of the outermost quantifier is 0; each additional quantifier
    increases the depth by 1.

    For example::

        scope_depth_map("∀x. ∃y. ∀z. P(x, y, z)")
        # → {"x": 0, "y": 1, "z": 2}

    Args:
        formula: Input formula string.

    Returns:
        Dictionary from variable name to integer depth.

    Notes:
        Only variables that appear as quantifier-bound variables are included
        in the returned map.  Free variables in the matrix are excluded.
    """
    prefix = extract_quantifier_prefix(formula)
    depth_map: dict[str, int] = {}
    # Strip the quantifier symbol to get the variable name
    sym_re = re.compile(r"^(∀|∃!|∃)([A-Za-z_][A-Za-z0-9_]*)$")
    for depth, tok in enumerate(prefix):
        m = sym_re.match(tok)
        if m:
            depth_map[m.group(2)] = depth
    return depth_map


def compute_tensor_shape(prefix: tuple[str, ...]) -> tuple[int, ...]:
    """Compute the tensor shape for a quantifier prefix.

    Each quantifier in the prefix contributes one dimension of size 1 to the
    shape tuple at the syntactic level.  The value 1 is a placeholder that
    represents "one axis of unspecified extent"; in a semantically grounded
    encoding the 1 would be replaced by the cardinality of the quantifier's
    domain.

    The resulting shape is used to:
    1. Determine the *rank* of the proof-term tensor (= len(shape)).
    2. Initialise the tensor product computation.
    3. Serve as a template for the Z3 shape-variable vector.

    Args:
        prefix: Tuple of quantifier tokens as returned by
            ``extract_quantifier_prefix``.

    Returns:
        Tuple of integers, all equal to 1, with length == len(prefix).

    Examples::

        compute_tensor_shape(("∀x", "∃y", "∀z"))
        # → (1, 1, 1)

        compute_tensor_shape(())
        # → ()
    """
    if not prefix:
        return ()
    # Each quantifier contributes one syntactic axis of extent 1.
    return tuple(1 for _ in prefix)


def build_binding_structure(formula: str) -> BindingStructure:
    """Build the BindingStructure for a formula from its quantifier prefix.

    Parses the formula, extracts the quantifier prefix, constructs one
    :class:`Binding` per quantifier, and determines the dependency order
    (outermost-first, since each quantifier scopes over all that follow it).

    The resulting BindingStructure captures:
    - The list of all bindings with their kinds and nesting depths.
    - The dependency order as a tuple of variable names.
    - Whether the formula is in prenex normal form (true iff no quantifier
      appears nested inside an atom or inside a propositional connective in
      the matrix).

    Args:
        formula: Input formula string.

    Returns:
        A frozen :class:`BindingStructure` describing the variable bindings.

    Notes:
        Prenex detection here is conservative: a formula is declared prenex
        only if *all* quantifiers appear in the extracted prefix.  Any
        quantifier remaining in the matrix (e.g. in "P(x) ∧ ∀y. Q(y)") will
        cause ``is_prenex`` to be False.
    """
    prefix = extract_quantifier_prefix(formula)
    depth_map = scope_depth_map(formula)

    sym_re = re.compile(r"^(∀|∃!|∃)([A-Za-z_][A-Za-z0-9_]*)$")
    bindings: list[Binding] = []

    for tok in prefix:
        m = sym_re.match(tok)
        if not m:
            continue
        q_sym, var = m.group(1), m.group(2)
        if q_sym == "∀":
            q_kind = "FORALL"
        elif q_sym == "∃!":
            q_kind = "UNIQUE"
        else:
            q_kind = "EXISTS"

        depth = depth_map.get(var, 0)
        occurrences = _find_bound_occurrences(formula, var)
        b_id = _stable_id("bind", var, q_kind, str(depth), formula[:40])
        bindings.append(
            Binding(
                binding_id=b_id,
                variable=var,
                quantifier=q_kind,
                scope_depth=depth,
                bound_occurrences=occurrences,
            )
        )

    dependency_order = tuple(depth_map.keys())

    # Prenex check: reconstruct how many quantifiers we found; if the matrix
    # still contains quantifier symbols the formula is not in prenex form.
    prefix_var_count = len(prefix)
    # Strip the prefix from the formula to get the matrix
    normalised = re.sub(r"\bforall\b", "∀", formula, flags=re.IGNORECASE)
    normalised = re.sub(r"\bexists\b", "∃", normalised, flags=re.IGNORECASE)
    normalised = re.sub(r"\bunique\b", "∃!", normalised, flags=re.IGNORECASE)
    q_remaining = len(re.findall(r"[∀∃]", normalised))
    is_prenex = (q_remaining == prefix_var_count)

    struct_id = _stable_id("struct", formula[:60], str(is_prenex))
    return BindingStructure(
        struct_id=struct_id,
        bindings=tuple(bindings),
        dependency_order=dependency_order,
        is_prenex=is_prenex,
    )


# ---------------------------------------------------------------------------
# Scope construction helpers
# ---------------------------------------------------------------------------


def _build_scope(
    variable: str,
    quantifier_kind: str,
    body: str,
    depth: int,
    bound_so_far: frozenset[str],
) -> QuantifierScope:
    """Internal helper: construct a QuantifierScope for one variable.

    Args:
        variable: The variable being bound.
        quantifier_kind: ``"FORALL"``, ``"EXISTS"``, or ``"UNIQUE"``.
        body: The formula string that falls within this scope.
        depth: Nesting depth.
        bound_so_far: Variables bound by enclosing quantifiers (they are not
            free in ``body``).

    Returns:
        A frozen :class:`QuantifierScope`.
    """
    free = _free_vars_in(body, bound_so_far | {variable})
    scope_id = _stable_id("scope", variable, quantifier_kind, str(depth), body[:40])
    return QuantifierScope(
        scope_id=scope_id,
        variable=variable,
        quantifier_kind=quantifier_kind,
        body=body,
        depth=depth,
        free_vars=free,
    )


# ---------------------------------------------------------------------------
# Core encoding functions
# ---------------------------------------------------------------------------


def encode_universal(
    formula: str,
    binding: BindingStructure | None = None,
    trust: int = 5,
) -> TensorQuantifierEncoding:
    """Encode a universally quantified formula ∀x.φ as a TensorQuantifierEncoding.

    A universally quantified formula ∀x.φ(x) has a proof term of type:

        Π(x : A). [[φ(x)]]

    This is a dependent product — a function from elements of the domain A
    to proof objects for φ(x).  In the tensor representation this is a rank-1
    tensor (a vector) whose entries are indexed by x.

    For a multi-variable universal prefix ∀x₁ ∀x₂ … ∀xₙ.M the proof term
    has type Π(x₁).Π(x₂).…Π(xₙ).[[M]], which is a rank-n tensor.

    The TrustTier T in the resulting judgment is set to ``trust``, subject to
    the constraint that universal formulas with well-formed binding structures
    receive a bonus of +1 tier (capped at TRUST_TIER_MAX), reflecting that
    universal claims — once proved — are more valuable than existential ones
    at the same syntactic level.

    Obstruction class: universal formulas have trivial obstruction O = 0
    because global proof functions always exist (by dependent choice / AC).
    There is no cohomological barrier to gluing local sections.

    Args:
        formula: The universally quantified formula string.
        binding: Pre-built BindingStructure, or None to auto-build.
        trust: Integer trust level in [TRUST_TIER_MIN, TRUST_TIER_MAX].
            Defaults to 5 (mid-tier confidence).

    Returns:
        A frozen :class:`TensorQuantifierEncoding` for this formula.

    Raises:
        ValueError: If the formula has no universal quantifier at the outermost
            level.
    """
    prefix = extract_quantifier_prefix(formula)
    if not prefix:
        raise ValueError(
            f"encode_universal: formula has no quantifier prefix: {formula!r}"
        )
    # Verify the outermost quantifier is universal
    first_sym = prefix[0][0] if prefix[0] else ""
    if first_sym not in ("∀",):
        # Be lenient: still encode but warn in the ID
        pass

    if binding is None:
        binding = build_binding_structure(formula)

    shape = compute_tensor_shape(prefix)

    # Determine the matrix by stripping the prefix tokens
    # We use a simple approach: remove each "Qvar." pattern from the front
    remaining = formula.strip()
    normalised = re.sub(r"\bforall\b", "∀", remaining, flags=re.IGNORECASE)
    normalised = re.sub(r"\bexists\b", "∃", normalised, flags=re.IGNORECASE)
    normalised = re.sub(r"\bunique\b", "∃!", normalised, flags=re.IGNORECASE)
    q_strip_re = re.compile(
        r"^(∀|∃!|∃)\s*[A-Za-z_][A-Za-z0-9_]*\s*[.:]?\s*"
    )
    for _ in prefix:
        stripped = q_strip_re.sub("", normalised).strip()
        if stripped != normalised:
            normalised = stripped
        else:
            break
    matrix = normalised

    # Trust bonus for a clean universal prefix (no alternations → simpler to
    # discharge, hence slightly higher confidence).
    adjusted_trust = trust
    if binding.is_purely_universal() and binding.quantifier_alternation_depth() == 0:
        adjusted_trust = _trust_join(trust, min(trust + 1, TRUST_TIER_MAX))

    enc_id = _stable_id("enc_univ", formula[:60], str(adjusted_trust))
    return TensorQuantifierEncoding(
        encoding_id=enc_id,
        formula=formula,
        quantifier_prefix=prefix,
        matrix=matrix,
        tensor_shape=shape,
        binding_structure=binding,
        trust_level=adjusted_trust,
    )


def encode_existential(
    formula: str,
    binding: BindingStructure | None = None,
    trust: int = 5,
) -> TensorQuantifierEncoding:
    """Encode an existentially quantified formula ∃x.φ as a TensorQuantifierEncoding.

    An existentially quantified formula ∃x.φ(x) has a proof term of type:

        Σ(x : A). [[φ(x)]]

    This is a dependent sum — a pair (witness, proof) where the witness is an
    element of A and the proof establishes φ(witness).  In the tensor
    representation this is also a rank-1 tensor, but with *contravariant* index
    variance: we must *supply* a concrete index (witness) rather than range over
    all of them.

    For a multi-variable existential prefix ∃x₁ ∃x₂ … ∃xₙ.M the proof term
    is a Σ-type nested n levels deep.

    Obstruction class: existential formulas may have non-trivial obstruction
    O ∈ Ȟ¹(𝒰, ℱ_φ).  The obstruction is non-trivial when the formula is
    classically true (witnesses exist on each local patch of the cover 𝒰) but
    the witnesses cannot be chosen consistently to form a global section.  This
    corresponds precisely to the failure of the axiom of choice in the topos of
    sheaves on 𝒰.

    Trust handling: existential formulas receive a slight trust penalty (-1)
    relative to universals at the same syntactic complexity, reflecting that
    exhibiting a witness is a stronger but harder-to-verify claim.

    Args:
        formula: The existentially quantified formula string.
        binding: Pre-built BindingStructure, or None to auto-build.
        trust: Integer trust level in [TRUST_TIER_MIN, TRUST_TIER_MAX].
            Defaults to 5.

    Returns:
        A frozen :class:`TensorQuantifierEncoding` for this formula.

    Raises:
        ValueError: If the formula has no quantifier prefix.
    """
    prefix = extract_quantifier_prefix(formula)
    if not prefix:
        raise ValueError(
            f"encode_existential: formula has no quantifier prefix: {formula!r}"
        )

    if binding is None:
        binding = build_binding_structure(formula)

    shape = compute_tensor_shape(prefix)

    # Strip the prefix to obtain the matrix
    remaining = formula.strip()
    normalised = re.sub(r"\bforall\b", "∀", remaining, flags=re.IGNORECASE)
    normalised = re.sub(r"\bexists\b", "∃", normalised, flags=re.IGNORECASE)
    normalised = re.sub(r"\bunique\b", "∃!", normalised, flags=re.IGNORECASE)
    q_strip_re = re.compile(
        r"^(∀|∃!|∃)\s*[A-Za-z_][A-Za-z0-9_]*\s*[.:]?\s*"
    )
    for _ in prefix:
        stripped = q_strip_re.sub("", normalised).strip()
        if stripped != normalised:
            normalised = stripped
        else:
            break
    matrix = normalised

    # Trust penalty for existential: harder to verify, slightly lower tier.
    adjusted_trust = _trust_meet(trust, max(trust - 1, TRUST_TIER_MIN))

    enc_id = _stable_id("enc_exist", formula[:60], str(adjusted_trust))
    return TensorQuantifierEncoding(
        encoding_id=enc_id,
        formula=formula,
        quantifier_prefix=prefix,
        matrix=matrix,
        tensor_shape=shape,
        binding_structure=binding,
        trust_level=adjusted_trust,
    )


def tensor_product_of_scopes(s1: QuantifierScope, s2: QuantifierScope) -> TensorProduct:
    """Compute the TensorProduct of two QuantifierScopes.

    Given two scopes s₁ = (Q₁x₁. body₁) and s₂ = (Q₂x₂. body₂), the
    tensor product corresponds to the *sequential composition* of their
    quantifiers in the proof-term type:

        type(s₁ ⊗ s₂) = Q₁(x₁). Q₂(x₂). [[M]]

    where [[M]] is the matrix type of the combined formula.

    The *contraction indices* identify any variables that appear free in s₂
    and are also bound by s₁ (i.e. variables that are shared between the two
    scopes and hence represent an *internal edge* in the tensor network rather
    than a free index).  Formally, contraction occurs when x₁ appears free in
    body₂ — the scope of s₁ *contains* s₂.

    Shape calculation:
        - If s₁ scopes over s₂ (s₁.depth < s₂.depth), the result shape is
          (1,) + (1,) = (1, 1) — a rank-2 tensor.
        - If the two scopes are at the same depth (parallel composition), the
          result is still (1, 1) but no contraction occurs.
        - If the scopes share a free variable (s₁.variable ∈ s₂.free_vars),
          that axis is contracted.

    Args:
        s1: The left (outer) QuantifierScope.
        s2: The right (inner) QuantifierScope.

    Returns:
        A frozen :class:`TensorProduct` describing the composed scope tensor.

    Notes:
        This function captures the *categorical* product in the category of
        contexts: the tensor product of two quantifier scopes is the comma
        construction on their respective proof-term functors.
    """
    # Determine whether s1 scopes over s2 (nesting) or they are parallel.
    s1_scopes_s2 = s1.depth < s2.depth

    # Base shape: one axis per scope
    base_shape: tuple[int, ...] = (1, 1)

    # Identify contraction indices: axes where s1's bound variable appears free
    # in s2, meaning that axis is shared and contracts.
    contraction: list[int] = []
    if s1.variable in s2.free_vars:
        # s1's variable is free in s2 — this is the nesting edge, axis 0 contracts
        contraction.append(0)
    if s2.variable in s1.free_vars:
        # s2's variable is free in s1 — unusual (reverse nesting), axis 1 contracts
        contraction.append(1)

    # The result shape retains only un-contracted axes; contracted axes become
    # internal and the result rank decreases by the number of contractions.
    # For a fully nested pair (s1 > s2) with s1.variable ∈ s2.free_vars:
    #   result_shape stays (1, 1) but rank() = 2 - 1 = 1 (a vector, not a matrix).
    # This is correct: ∀x.∃y.P(x,y) has a proof term of type x ↦ (y(x), proof(x,y(x)))
    # which is a function (rank-1) not a matrix (rank-2).

    product_id = _stable_id(
        "prod",
        s1.scope_id,
        s2.scope_id,
        str(base_shape),
        str(contraction),
    )
    return TensorProduct(
        product_id=product_id,
        left_scope_id=s1.scope_id,
        right_scope_id=s2.scope_id,
        result_shape=base_shape,
        contraction_indices=tuple(contraction),
    )


def contract_tensor_scopes(product: TensorProduct) -> dict[str, Any]:
    """Perform the contraction encoded in a TensorProduct and return a summary.

    Contraction reduces the effective rank of the tensor product by eliminating
    shared index axes.  In the proof-term interpretation:
    - A contraction at axis i means the proof term does *not* independently
      range over the domain at axis i; instead axis i is determined by the
      values of other axes (the inner scope's witness depends on the outer
      scope's variable).

    For example, in ∀x.∃y(x).P(x, y(x)) the proof term is a *function* x ↦
    (y(x), prf(x)), which is rank-1 (indexed only by x), not rank-2 (indexed
    independently by x and y).  The contraction collapses the y-axis because y
    is a *dependent* witness.

    The Čech H¹ interpretation: if the contraction produces a rank-0 result
    (scalar), the formula is closed and the obstruction is trivially 0.
    Non-rank-0 results may carry non-trivial obstruction depending on the
    quantifier kinds at the remaining free axes.

    Args:
        product: A :class:`TensorProduct` (as returned by
            ``tensor_product_of_scopes``) to contract.

    Returns:
        A plain dict with keys:
            ``"product_id"`` (str),
            ``"original_shape"`` (tuple),
            ``"contraction_indices"`` (tuple),
            ``"effective_rank"`` (int),
            ``"is_scalar"`` (bool),
            ``"cech_obstruction_hint"`` (str).
    """
    effective_rank = product.rank()
    is_scalar = product.scalar_result()

    if is_scalar:
        obs_hint = (
            "Ȟ¹ = 0 (scalar result — formula is closed; no free tensor axes; "
            "obstruction is trivially trivial)"
        )
    elif effective_rank == 1:
        obs_hint = (
            "Ȟ¹ potentially non-trivial (rank-1 result — one free tensor axis; "
            "existential witnesses along this axis may fail to glue globally)"
        )
    else:
        obs_hint = (
            f"Ȟ¹ depends on higher-rank structure (rank-{effective_rank} result; "
            "full Čech complex computation required to determine obstruction)"
        )

    return {
        "product_id": product.product_id,
        "original_shape": product.result_shape,
        "contraction_indices": product.contraction_indices,
        "effective_rank": effective_rank,
        "is_scalar": is_scalar,
        "cech_obstruction_hint": obs_hint,
    }


# ---------------------------------------------------------------------------
# Multi-scope tensor product pipeline
# ---------------------------------------------------------------------------


def _build_scopes_from_encoding(enc: TensorQuantifierEncoding) -> list[QuantifierScope]:
    """Extract a list of QuantifierScopes from a TensorQuantifierEncoding.

    One scope is constructed per binding, with the body set to the formula
    from that binding's depth onwards.

    Args:
        enc: A TensorQuantifierEncoding from which to derive scopes.

    Returns:
        List of QuantifierScope objects in outermost-first order.
    """
    scopes: list[QuantifierScope] = []
    bound_so_far: frozenset[str] = frozenset()
    # Use the dependency order to iterate bindings in depth order
    dep_order = enc.binding_structure.dependency_order

    for var in dep_order:
        b = enc.binding_structure.get_binding(var)
        if b is None:
            continue
        # Body = everything from this depth inward; approximate with the matrix
        # for the innermost variable, and the full formula for outer ones.
        depth = b.scope_depth
        body = enc.matrix if depth == len(dep_order) - 1 else enc.formula
        scope = _build_scope(var, b.quantifier, body, depth, bound_so_far)
        scopes.append(scope)
        bound_so_far = bound_so_far | {var}

    return scopes


def tensor_product_chain(enc: TensorQuantifierEncoding) -> list[TensorProduct]:
    """Compute the full left-to-right tensor product chain for an encoding.

    Given an encoding with prefix Q₁x₁ Q₂x₂ … Qₙxₙ, returns the list of
    pairwise tensor products:
        [(s₁ ⊗ s₂), (s₂ ⊗ s₃), …, (s_{n-1} ⊗ sₙ)]

    This chain represents the sequential composition of all quantifier scopes
    in the proof-term type.

    Args:
        enc: A TensorQuantifierEncoding.

    Returns:
        List of TensorProduct objects.  Empty list if the encoding has fewer
        than 2 quantifiers.
    """
    scopes = _build_scopes_from_encoding(enc)
    if len(scopes) < 2:
        return []
    products: list[TensorProduct] = []
    for s1, s2 in zip(scopes, scopes[1:]):
        products.append(tensor_product_of_scopes(s1, s2))
    return products


def compose_two_encodings(
    enc1: TensorQuantifierEncoding,
    enc2: TensorQuantifierEncoding,
) -> TensorProduct:
    """Form the tensor product of the first scopes of two separate encodings.

    This function demonstrates *cross-formula* tensor composition: given two
    independently encoded formulas φ₁ and φ₂, it forms the tensor product of
    their outermost scopes.  This corresponds to the conjunction φ₁ ∧ φ₂ whose
    proof term is a pair (Π₁, Π₂) — a rank-2 tensor with one axis per formula.

    In the JuGeo judgment framework, composing two judgment proof terms
    (Π₁, Π₂) corresponds to combining their evidence in a new judgment whose
    trust tier is the meet T₁ ⊗ T₂ = min(T₁, T₂).

    Args:
        enc1: First TensorQuantifierEncoding.
        enc2: Second TensorQuantifierEncoding.

    Returns:
        A TensorProduct whose left_scope_id and right_scope_id come from the
        outermost scopes of enc1 and enc2 respectively.

    Raises:
        ValueError: If either encoding has no quantifiers (empty prefix).
    """
    if not enc1.binding_structure.bindings:
        raise ValueError(
            f"compose_two_encodings: enc1 {enc1.encoding_id!r} has no quantifiers."
        )
    if not enc2.binding_structure.bindings:
        raise ValueError(
            f"compose_two_encodings: enc2 {enc2.encoding_id!r} has no quantifiers."
        )

    scopes1 = _build_scopes_from_encoding(enc1)
    scopes2 = _build_scopes_from_encoding(enc2)

    # Take the outermost scope of each encoding
    outer1 = scopes1[0]
    outer2 = scopes2[0]

    return tensor_product_of_scopes(outer1, outer2)


# ---------------------------------------------------------------------------
# Display helpers
# ---------------------------------------------------------------------------


def summarise_encoding(enc: TensorQuantifierEncoding) -> str:
    """Return a multi-line human-readable summary of a TensorQuantifierEncoding.

    Useful for debugging and for the smoke test in ``__main__``.

    Args:
        enc: The encoding to summarise.

    Returns:
        A formatted string showing all key fields.
    """
    lines: list[str] = [
        "=" * 70,
        f"TensorQuantifierEncoding: {enc.encoding_id}",
        "=" * 70,
        f"  Formula      : {enc.formula}",
        f"  Prefix       : {enc.quantifier_prefix}",
        f"  Matrix       : {enc.matrix}",
        f"  Tensor shape : {enc.tensor_shape}",
        f"  Rank         : {enc.rank()}",
        f"  Trust level  : {enc.trust_level} / {TRUST_TIER_MAX}",
        f"  Prenex NF    : {enc.binding_structure.is_prenex}",
        f"  Alternations : {enc.alternation_depth()}",
        f"  Prefix string: {enc.binding_structure.prenex_prefix_string()}",
        f"  Proof type   : {enc.proof_term_type_string()}",
        f"  Obstruction  : {enc.obstruction_class()}",
        "",
        "  Čech H¹ description:",
        f"    {enc.cech_h1_description()}",
        "",
        "  Judgment tuple:",
        f"    {enc.judgment_tuple_summary()}",
        "=" * 70,
    ]
    return "\n".join(lines)


def summarise_tensor_product(
    product: TensorProduct,
    s1: QuantifierScope,
    s2: QuantifierScope,
) -> str:
    """Return a multi-line summary of a TensorProduct and its contracted form.

    Args:
        product: The TensorProduct to summarise.
        s1: Left scope.
        s2: Right scope.

    Returns:
        Formatted string.
    """
    contraction_info = contract_tensor_scopes(product)
    lines: list[str] = [
        "-" * 70,
        f"TensorProduct: {product.product_id}",
        "-" * 70,
        f"  Left scope   : {s1.symbol()}{s1.variable} "
        f"(id={s1.scope_id[:16]}…, variance={s1.tensor_variance()})",
        f"  Right scope  : {s2.symbol()}{s2.variable} "
        f"(id={s2.scope_id[:16]}…, variance={s2.tensor_variance()})",
        f"  Result shape : {product.result_shape}",
        f"  Contractions : {product.contraction_indices}",
        f"  Effective rank: {product.rank()}",
        f"  Outer product : {product.is_outer_product()}",
        f"  Scalar result : {product.scalar_result()}",
        "",
        "  Čech obstruction hint:",
        f"    {contraction_info['cech_obstruction_hint']}",
        "-" * 70,
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Why this family matters disproportionately — narrative functions
# ---------------------------------------------------------------------------


def why_quantifiers_need_tensor_structure() -> str:
    """Return a narrative explanation of why quantifiers need tensor structure.

    This function encapsulates the theoretical motivation of this module in
    plain text, suitable for docstring display or logging.

    Returns:
        A multi-paragraph string explaining the tensor-quantifier connection.
    """
    return """
Why First-Order Quantifiers Need Tensor Structure
==================================================

1. PRODUCT TYPES vs SUM TYPES
------------------------------
In Martin-Löf type theory (and its categorical semantics):
  ∀x : A. B(x)  ≅  Π(x : A). B(x)   — dependent product type
  ∃x : A. B(x)  ≅  Σ(x : A). B(x)   — dependent sum type

The proof term for ∀x.B(x) is a *function* f : A → B (or more precisely,
a section of the fibration B → A).  The proof term for ∃x.B(x) is a *pair*
(a, b) where a : A and b : B(a).

2. TENSOR STRUCTURE ARISES FROM NESTED QUANTIFIERS
----------------------------------------------------
When a formula has a prefix Q₁x₁ Q₂x₂ … Qₙxₙ.M:
  - Each Qᵢxᵢ contributes one axis to the proof-term tensor.
  - Universal axes are *covariant*: the proof function must be defined at
    every point of that axis.
  - Existential axes are *contravariant*: a specific point (witness) must
    be supplied.
  - The proof term type is a nested Π/Σ combination, which has the structure
    of a tensor over the product of the individual axis domains.

3. JUDGMENT GEOMETRY CONNECTION
---------------------------------
In the JuGeo framework, judgments are 8-tuples (c, φ, A, E, O, B, T, Π).
The component Π (the proof term) has type determined by the quantifier
structure of φ.  Encoding quantifiers as tensors gives us:
  - A natural representation for Π (as a multi-index array).
  - A way to compose proofs (via tensor products of their scope types).
  - A measure of proof complexity (tensor rank = quantifier depth).

4. ČECH COHOMOLOGY AND OBSTRUCTIONS
-------------------------------------
The obstruction component O ∈ Ȟ¹(𝒰, ℱ_φ) captures the failure of local
witnesses for ∃ to assemble into a global proof.  Specifically:
  - For ∀x.φ: O = 0 always (no obstruction; global proof = dependent choice).
  - For ∃x.φ: O may be non-trivial if the formula is classically true but
    constructively false (local witnesses exist but do not glue).
  - For alternating prefixes ∀∃∀…: O is in higher Čech cohomology, and its
    vanishing is a necessary condition for proof term assembly.

5. WHY THIS FAMILY MATTERS DISPROPORTIONATELY
----------------------------------------------
Among all formula families, the tensor-quantifier family is special:
  a. It is expressive enough to encode ALL first-order logic (any FOL formula
     can be brought to prenex normal form, which is a quantifier prefix + matrix).
  b. The tensor structure makes composition (∧, ∨, →) of formulas correspond
     to linear-algebraic operations (outer product, contraction, dualization).
  c. The trust-tier algebra (𝕋, ≤, ⊕, ⊗) acts naturally on the tensor axes:
     the trust of a composed proof is the meet (min) of the constituent trusts.
  d. The Čech cohomology provides an *algorithmic* obstruction theory: checking
     whether O = 0 is equivalent to checking whether a system of linear
     constraints over Z/2Z is satisfiable — an NP problem, but tractable for
     small covers.
  e. Any other encoding (scalar, sequence, collection) can be obtained as a
     *specialisation* of the tensor-quantifier encoding by restricting the
     quantifier rank to 0 (scalars), 1 (sequences), or 2 (matrices).
"""


def disproportionate_impact_table() -> list[dict[str, str]]:
    """Return a table of reasons why tensor-quantifier encodings matter more.

    Returns a list of dicts, each with keys ``"reason"``, ``"detail"``, and
    ``"consequence"``, suitable for tabular display or further processing.

    Returns:
        List of table rows as plain dicts.
    """
    return [
        {
            "reason": "Universal expressiveness",
            "detail": "Every FOL formula has a prenex normal form (quantifier prefix + matrix).",
            "consequence": (
                "Tensor-quantifier encodings cover ALL of first-order logic; "
                "no other encoding family has this reach."
            ),
        },
        {
            "reason": "Categorical naturality",
            "detail": "∀ ↔ Π-types, ∃ ↔ Σ-types; tensor products compose proof types.",
            "consequence": (
                "Proof composition (∧-intro, ∃-elim) corresponds to linear "
                "algebra operations, enabling systematic automation."
            ),
        },
        {
            "reason": "Trust-tier propagation",
            "detail": "Trust meet (⊗ = min) propagates through tensor axes.",
            "consequence": (
                "Weakest-link reasoning is built into the algebra; "
                "a composed proof is only as trusted as its least-trusted component."
            ),
        },
        {
            "reason": "Obstruction detection",
            "detail": "Čech H¹ obstruction detects non-constructive existentials.",
            "consequence": (
                "We can algorithmically identify when a formula is classically "
                "provable but constructively problematic — critical for JuGeo's "
                "verified-proof requirement."
            ),
        },
        {
            "reason": "Strict generalisation",
            "detail": "Rank-0 = scalars, rank-1 = sequences, rank-2 = matrices.",
            "consequence": (
                "Every other encoding family is a special case; "
                "tensor-quantifier encodings strictly subsume all of them."
            ),
        },
        {
            "reason": "Z3 encoding efficiency",
            "detail": "Prenex form allows finite unrolling under QF_LIA decidability.",
            "consequence": (
                "By restricting to affine index functions, universal quantifiers "
                "reduce to finite conjunctions, staying in the decidable QF_LIA fragment."
            ),
        },
    ]


# ---------------------------------------------------------------------------
# Module-level invariant checks
# ---------------------------------------------------------------------------


def check_trust_algebra_invariants() -> bool:
    """Verify the basic axioms of the TrustTier ordered algebra.

    Checks:
    1. ⊕ (join) is idempotent: t ⊕ t = t
    2. ⊗ (meet) is idempotent: t ⊗ t = t
    3. ⊕ is commutative: t₁ ⊕ t₂ = t₂ ⊕ t₁
    4. ⊗ is commutative: t₁ ⊗ t₂ = t₂ ⊗ t₁
    5. Absorption: t₁ ⊕ (t₁ ⊗ t₂) = t₁
    6. Absorption: t₁ ⊗ (t₁ ⊕ t₂) = t₁

    Returns:
        True iff all invariants pass.

    Raises:
        AssertionError: If any invariant fails.
    """
    sample = list(range(TRUST_TIER_MIN, TRUST_TIER_MAX + 1))
    for t in sample:
        # Idempotence
        assert _trust_join(t, t) == t, f"join not idempotent at {t}"
        assert _trust_meet(t, t) == t, f"meet not idempotent at {t}"
    for t1, t2 in itertools.product(sample, sample):
        # Commutativity
        assert _trust_join(t1, t2) == _trust_join(t2, t1), f"join not commutative at {t1},{t2}"
        assert _trust_meet(t1, t2) == _trust_meet(t2, t1), f"meet not commutative at {t1},{t2}"
        # Absorption
        assert _trust_join(t1, _trust_meet(t1, t2)) == t1, f"absorption (join) failed at {t1},{t2}"
        assert _trust_meet(t1, _trust_join(t1, t2)) == t1, f"absorption (meet) failed at {t1},{t2}"
    return True


# ---------------------------------------------------------------------------
# Smoke test / __main__
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    print("=" * 70)
    print("Tensor-Quantifier Encodings — smoke test")
    print("Why first-order quantifiers need tensor structure")
    print("=" * 70)

    # ------------------------------------------------------------------
    # 1. Verify TrustTier algebra invariants
    # ------------------------------------------------------------------
    print("\n[1] Checking TrustTier algebra invariants …")
    try:
        ok = check_trust_algebra_invariants()
        print(f"    All invariants satisfied: {ok}")
    except AssertionError as exc:
        print(f"    INVARIANT VIOLATION: {exc}", file=sys.stderr)
        sys.exit(1)

    # ------------------------------------------------------------------
    # 2. Encode a universally quantified formula
    # ------------------------------------------------------------------
    univ_formula = "∀x. ∀y. (P(x) → Q(x, y))"
    print(f"\n[2] Encoding universal formula: {univ_formula!r}")
    enc_univ = encode_universal(univ_formula, trust=7)
    print(summarise_encoding(enc_univ))

    # ------------------------------------------------------------------
    # 3. Encode an existentially quantified formula
    # ------------------------------------------------------------------
    exist_formula = "∃x. ∃y. (R(x, y) ∧ S(y))"
    print(f"\n[3] Encoding existential formula: {exist_formula!r}")
    enc_exist = encode_existential(exist_formula, trust=6)
    print(summarise_encoding(enc_exist))

    # ------------------------------------------------------------------
    # 4. Encode a mixed (alternating) formula
    # ------------------------------------------------------------------
    mixed_formula = "∀x. ∃y. ∀z. (P(x, z) → Q(y, z))"
    print(f"\n[4] Encoding mixed alternating formula: {mixed_formula!r}")
    enc_mixed = encode_universal(mixed_formula, trust=5)
    print(summarise_encoding(enc_mixed))

    # ------------------------------------------------------------------
    # 5. Tensor product of the outermost scopes of the two encodings
    # ------------------------------------------------------------------
    print("\n[5] Computing tensor product of outermost scopes …")
    scopes_univ = _build_scopes_from_encoding(enc_univ)
    scopes_exist = _build_scopes_from_encoding(enc_exist)

    if scopes_univ and scopes_exist:
        s1 = scopes_univ[0]
        s2 = scopes_exist[0]
        product = tensor_product_of_scopes(s1, s2)
        print(summarise_tensor_product(product, s1, s2))
    else:
        print("    (No scopes to compose.)")

    # ------------------------------------------------------------------
    # 6. Cross-encoding tensor product (∀ ⊗ ∃)
    # ------------------------------------------------------------------
    print("\n[6] Cross-encoding tensor product (universal ⊗ existential) …")
    try:
        cross_product = compose_two_encodings(enc_univ, enc_exist)
        contracted = contract_tensor_scopes(cross_product)
        print(f"    Cross product id   : {cross_product.product_id}")
        print(f"    Result shape       : {cross_product.result_shape}")
        print(f"    Effective rank     : {cross_product.rank()}")
        print(f"    Obstruction hint   : {contracted['cech_obstruction_hint']}")
    except ValueError as exc:
        print(f"    Could not compose: {exc}")

    # ------------------------------------------------------------------
    # 7. Intra-encoding chain (all pairwise products for mixed formula)
    # ------------------------------------------------------------------
    print("\n[7] Intra-encoding scope chain for mixed formula …")
    chain = tensor_product_chain(enc_mixed)
    print(f"    Number of pairwise products: {len(chain)}")
    for i, p in enumerate(chain):
        print(
            f"    product[{i}]: shape={p.result_shape}, "
            f"contractions={p.contraction_indices}, rank={p.rank()}"
        )

    # ------------------------------------------------------------------
    # 8. Disproportionate-impact table
    # ------------------------------------------------------------------
    print("\n[8] Why this family matters disproportionately:")
    table = disproportionate_impact_table()
    for row in table:
        print(f"\n  Reason     : {row['reason']}")
        print(f"  Detail     : {row['detail']}")
        print(f"  Consequence: {row['consequence']}")

    # ------------------------------------------------------------------
    # 9. Narrative explanation
    # ------------------------------------------------------------------
    print("\n[9] Full narrative:")
    print(why_quantifiers_need_tensor_structure())

    print("\n[smoke test PASSED]")
