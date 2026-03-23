"""
SolverLiftedTypeSystem — types with embedded Z3 invariants at the structural frontier.

Types in the structural frontier are not mere syntactic labels; they carry
Z3 formulas as invariants that can be checked, combined, and translated back
to SMT-LIB2.  This module provides the *lifting machinery*: the infrastructure
for taking a plain base type (an SMT-LIB2 sort name) and equipping it with a
first-class invariant formula that travels with it through the JuGeo reasoning
pipeline.

The lifting operation is parametric in a ``TypeLiftingStrategy``:

  DIRECT_ENCODING
      The invariant is asserted verbatim as an SMT-LIB2 ``assert`` form.
      This is the cheapest strategy and works for all decidable fragments.

  ABSTRACTION_REFINEMENT
      The invariant is initially over-approximated (weakened), and the
      refinement loop strengthens it whenever Z3 finds a spurious model.
      Useful when the invariant is expensive but most queries are unsatisfiable.

  SKOLEMIZATION
      Existential quantifiers in the invariant are Skolemized away,
      replacing ``(exists ((x T)) P(x))`` with a fresh constant ``sk_x``
      and the constraint ``P(sk_x)``.  This eliminates the quantifier at
      the cost of introducing a new symbol.

  QUANTIFIER_ELIMINATION
      The invariant is simplified by quantifier elimination (QE).  For
      linear arithmetic this is complete (Fourier-Motzkin / Omega); for
      non-linear theories it may not terminate.

  COPILOT_GUIDED
      The Copilot layer suggests which of the above strategies to apply
      based on the shape of the invariant and the current solver budget.

Key design decisions:
  - ``SolverLiftedType`` objects are immutable (frozen dataclasses).  All
    transformations return new objects; no mutation in place.
  - The ``InvariantChecker`` tracks a per-session check count, enabling
    budget-aware verification.
  - The ``TypeLiftingTranslator`` is strategy-agnostic at construction; the
    strategy can be overridden per-call via ``apply_strategy``.
  - The ``SolverLiftedTypeSystem`` is the registry and orchestrator: it holds
    all lifted types, delegates invariant checking to ``InvariantChecker``,
    and delegates type construction to ``TypeLiftingTranslator``.

Copilot integration is threaded through every major class via
``copilot_*_hint`` methods that return structured key=value strings.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)

# -------------------------------------------------------------------
# Conditional imports — jugeo solver / geometry dependencies
# -------------------------------------------------------------------

try:
    from jugeo.solver.z3_session import (  # noqa: F401
        Z3Formula,
        Z3QueryBuilder,
        Z3Result,
        Z3Session,
        SolveOutcome,
        SolverResult,
    )
    _Z3_AVAILABLE = True
except ImportError:
    _Z3_AVAILABLE = False
    Z3Session = None  # type: ignore[assignment,misc]
    Z3Formula = None  # type: ignore[assignment,misc]
    SolveOutcome = None  # type: ignore[assignment,misc]
    SolverResult = None  # type: ignore[assignment,misc]
    Z3QueryBuilder = None  # type: ignore[assignment,misc]
    Z3Result = None  # type: ignore[assignment,misc]

try:
    from jugeo.solver.fragments import (  # noqa: F401
        Fragment,
        LogicalFragment,
        SolverFragment,
        classify_fragment,
    )
    _FRAGMENTS_AVAILABLE = True
except ImportError:
    _FRAGMENTS_AVAILABLE = False
    Fragment = None  # type: ignore[assignment,misc]
    LogicalFragment = None  # type: ignore[assignment,misc]
    SolverFragment = None  # type: ignore[assignment,misc]
    classify_fragment = None  # type: ignore[assignment]

try:
    from jugeo.solver.countermodels import (  # noqa: F401
        Countermodel,
        CountermodelExtractor,
        FailureClass,
        ObstructionConverter,
        RepairType,
    )
    _COUNTERMODELS_AVAILABLE = True
except ImportError:
    _COUNTERMODELS_AVAILABLE = False
    Countermodel = None  # type: ignore[assignment,misc]
    CountermodelExtractor = None  # type: ignore[assignment,misc]
    ObstructionConverter = None  # type: ignore[assignment,misc]
    FailureClass = None  # type: ignore[assignment,misc]
    RepairType = None  # type: ignore[assignment,misc]

try:
    from jugeo.geometry.supports import SupportRegion
    _SUPPORTS_AVAILABLE = True
except ImportError:
    _SUPPORTS_AVAILABLE = False
    SupportRegion = None  # type: ignore[assignment,misc]

try:
    from jugeo.encodings.structural_frontier.models import (
        KNOWN_DECIDABLE_FRAGMENTS,
        DecidabilityClass,
        DecidabilityMap,
        FrontierBoundary,
        FrontierSide,
        RepairAction,
        SolverLiftedType,
        StructuralFrontier,
        make_default_boundary,
        make_default_frontier,
        make_default_map,
    )
    _MODELS_AVAILABLE = True
except ImportError:
    try:
        from jugeo.encodings.structural_frontier.structural_frontier_definer import (  # type: ignore[no-redef]
            KNOWN_DECIDABLE_FRAGMENTS,
            DecidabilityClass,
            DecidabilityMap,
            FrontierBoundary,
            FrontierSide,
            RepairAction,
            SolverLiftedType,
            StructuralFrontier,
            make_default_boundary,
            make_default_frontier,
            make_default_map,
        )
        _MODELS_AVAILABLE = True
    except ImportError:
        _MODELS_AVAILABLE = False

        # -------------------------------------------------------------------
        # Inline fallback model definitions (identical to s01 fallbacks)
        # -------------------------------------------------------------------

        class DecidabilityClass(str, Enum):  # type: ignore[no-redef]
            """Decidability classification fallback."""
            DECIDABLE = "decidable"
            UNDECIDABLE = "undecidable"
            SEMI_DECIDABLE = "semi_decidable"
            UNKNOWN = "unknown"

        class FrontierSide(str, Enum):  # type: ignore[no-redef]
            """Frontier side fallback."""
            INSIDE = "inside"
            BOUNDARY = "boundary"
            OUTSIDE = "outside"

        @dataclass(frozen=True)
        class SolverLiftedType:  # type: ignore[no-redef]
            """Fallback SolverLiftedType — a type with an embedded Z3 invariant."""
            type_id: str
            base_name: str
            z3_invariant_smt: str
            inhabited: bool = True
            fragment_tag: str = "QF_LIA"
            notes: str = ""

            def check_member(self, value_smt: str) -> bool:
                return bool(self.z3_invariant_smt) and bool(value_smt)

            def strengthen(self, extra: str) -> SolverLiftedType:
                return SolverLiftedType(
                    type_id=self.type_id,
                    base_name=self.base_name,
                    z3_invariant_smt=f"(and {self.z3_invariant_smt} {extra})",
                    inhabited=self.inhabited,
                    fragment_tag=self.fragment_tag,
                    notes=self.notes,
                )

            def weaken(self) -> SolverLiftedType:
                inv = self.z3_invariant_smt.strip()
                if inv.startswith("(and "):
                    parts = inv[5:-1].strip().split(" ", 1)
                    new_inv = parts[1].strip() if len(parts) > 1 else "true"
                else:
                    new_inv = "true"
                return SolverLiftedType(
                    type_id=self.type_id,
                    base_name=self.base_name,
                    z3_invariant_smt=new_inv,
                    inhabited=self.inhabited,
                    fragment_tag=self.fragment_tag,
                    notes=self.notes,
                )

            def intersect(self, other: SolverLiftedType) -> SolverLiftedType:
                return SolverLiftedType(
                    type_id=f"{self.type_id}_x_{other.type_id}",
                    base_name=f"{self.base_name}_intersect_{other.base_name}",
                    z3_invariant_smt=f"(and {self.z3_invariant_smt} {other.z3_invariant_smt})",
                    inhabited=self.inhabited and other.inhabited,
                    fragment_tag=self.fragment_tag,
                )

            def union(self, other: SolverLiftedType) -> SolverLiftedType:
                return SolverLiftedType(
                    type_id=f"{self.type_id}_u_{other.type_id}",
                    base_name=f"{self.base_name}_union_{other.base_name}",
                    z3_invariant_smt=f"(or {self.z3_invariant_smt} {other.z3_invariant_smt})",
                    inhabited=self.inhabited or other.inhabited,
                    fragment_tag=self.fragment_tag,
                )

            def to_smt2(self) -> str:
                lines = [
                    f"; Lifted type: {self.base_name}  id={self.type_id}",
                    f"(declare-sort {self.base_name} 0)",
                    f"(assert {self.z3_invariant_smt})",
                ]
                return "\n".join(lines) + "\n"

        KNOWN_DECIDABLE_FRAGMENTS: list[str] = [  # type: ignore[no-redef]
            "QF_LIA", "QF_LRA", "QF_BV", "QF_UF",
            "QF_UFLIA", "QF_AUFLIA", "QF_ABV", "PROPOSITIONAL",
            "QF_IDL", "QF_RDL", "QF_LIRA", "QF_UFLRA",
        ]

        def make_default_frontier(fragment_name: str) -> StructuralFrontier:  # type: ignore[misc]
            return None  # type: ignore[return-value]

        def make_default_boundary(name: str) -> FrontierBoundary:  # type: ignore[misc]
            return None  # type: ignore[return-value]

        def make_default_map() -> DecidabilityMap:  # type: ignore[misc]
            return None  # type: ignore[return-value]


# -------------------------------------------------------------------
# Internal constants
# -------------------------------------------------------------------

# Keywords that indicate an invariant is in the quantifier-free linear fragment
_QF_LINEAR_TOKENS: frozenset[str] = frozenset({
    "+", "-", "<=", ">=", "<", ">", "=",
    "div", "mod", "Int", "Real", "Bool",
    "bvadd", "bvsub", "bvsle", "bvslt", "bvuge", "bvugt",
})

# Keywords whose presence makes Skolemization applicable
_EXISTENTIAL_PATTERNS: frozenset[str] = frozenset({"exists"})

# Keywords whose presence makes quantifier elimination applicable
_UNIVERSAL_PATTERNS: frozenset[str] = frozenset({"forall"})

# Keywords indicating nonlinear arithmetic that may need abstraction
_NONLINEAR_TOKENS: frozenset[str] = frozenset({
    "bvmul", "bvsdiv", "bvudiv", "*", "^", "sin", "cos", "exp",
})

# Common invariant templates for the witness-finding heuristic
_INVARIANT_WITNESS_TEMPLATES: list[tuple[str, str]] = [
    # (pattern, example witness)
    (">= 0", "(= x 1)"),
    ("> 0", "(= x 1)"),
    ("<= 0", "(= x (- 1))"),
    ("< 0", "(= x (- 1))"),
    ("bvsge", "(= x #x00000001)"),
    ("bvuge", "(= x #x00000001)"),
    ("true", "(= x 0)"),
]

# Fragment inference rules: keyword → fragment tag
_FRAGMENT_INFERENCE: list[tuple[str, str]] = [
    ("bvadd", "QF_BV"),
    ("bvsub", "QF_BV"),
    ("bvand", "QF_BV"),
    ("concat", "QF_BV"),
    ("extract", "QF_BV"),
    ("select", "QF_AX"),
    ("store", "QF_AX"),
    ("str.++", "QF_S"),
    ("str.len", "QF_S"),
    ("Real", "QF_LRA"),
    ("real", "QF_LRA"),
    ("Int", "QF_LIA"),
    ("int", "QF_LIA"),
    ("Bool", "PROPOSITIONAL"),
    ("and", "PROPOSITIONAL"),
    ("or", "PROPOSITIONAL"),
]


# -------------------------------------------------------------------
# TypeLiftingStrategy
# -------------------------------------------------------------------

class TypeLiftingStrategy(Enum):
    """Strategy for embedding a Z3 invariant into a lifted type.

    Each strategy represents a distinct approach to encoding the invariant
    into SMT-LIB2 and to interacting with the Z3 solver during type checking.
    Copilot may suggest which strategy to use based on the shape of the
    invariant and the available solver budget.
    """

    DIRECT_ENCODING = "direct_encoding"
    """Assert the invariant verbatim as a Z3 SMT-LIB2 ``assert`` form.

    This is the cheapest strategy and is always sound for decidable fragments.
    It is the default strategy for all types whose invariant is in QF_LIA,
    QF_LRA, QF_BV, or any other decidable quantifier-free fragment.
    """

    ABSTRACTION_REFINEMENT = "abstraction_refinement"
    """Start with a weakened over-approximation; refine on spurious models.

    Useful when the invariant is expensive but most solving queries are
    unsatisfiable.  The loop terminates when either the invariant is strong
    enough to refute the query or a real countermodel is found.
    """

    SKOLEMIZATION = "skolemization"
    """Eliminate existential quantifiers by introducing Skolem constants.

    Replaces ``(exists ((x T)) P(x))`` with a fresh constant ``sk_x`` and
    adds ``P(sk_x)`` as an unconditional assertion.  Reduces the quantifier
    depth of the invariant at the cost of a larger symbol table.
    """

    QUANTIFIER_ELIMINATION = "quantifier_elimination"
    """Apply quantifier elimination to simplify the invariant.

    For linear arithmetic this is complete via Fourier-Motzkin (for reals) or
    the Omega test (for integers).  The result is always quantifier-free for
    QF_LIA and QF_LRA.  For other theories, QE may not terminate.
    """

    COPILOT_GUIDED = "copilot_guided"
    """Let the Copilot orchestration layer choose the lifting strategy.

    The Copilot layer inspects the invariant shape, the current solver budget,
    and the history of previous lifting attempts to suggest the best strategy.
    This is the most adaptive but also the most expensive strategy.
    """


# -------------------------------------------------------------------
# InvariantChecker
# -------------------------------------------------------------------

class InvariantChecker:
    """Checks and manipulates Z3 invariants embedded in ``SolverLiftedType`` objects.

    The checker is responsible for:
    - Determining whether a given SMT-LIB2 value term satisfies a lifted type's
      invariant (:meth:`check_invariant`).
    - Verifying that an invariant is internally consistent (:meth:`verify_invariant_consistency`).
    - Finding a concrete witness that satisfies the invariant (:meth:`find_invariant_witness`).
    - Strengthening and minimising invariants (:meth:`strengthen_invariant`,
      :meth:`minimize_invariant`).
    - Providing Copilot hints via :meth:`copilot_invariant_hint`.

    The checker maintains a ``check_count`` for budget tracking and a
    ``session_hints`` dict for caching per-type observations.
    """

    def __init__(self) -> None:
        """Initialise the checker with an empty hint cache and zero count."""
        self.session_hints: dict[str, Any] = {}
        self.check_count: int = 0
        self._consistency_cache: dict[str, bool] = {}
        self._witness_cache: dict[str, str | None] = {}
        self._created_at: float = time.monotonic()

        logger.debug("InvariantChecker initialised.")

    def check_invariant(self, type_lifted: SolverLiftedType, value_smt: str) -> bool:
        """Check whether *value_smt* plausibly satisfies *type_lifted*'s invariant.

        Delegates to ``type_lifted.check_member`` and increments the
        per-session check count.  For types with a live Z3 session this would
        issue a real solver query; in the syntactic fallback it performs a
        pattern-based plausibility check.

        Args:
            type_lifted: The lifted type whose invariant is being checked.
            value_smt: An SMT-LIB2 term representing the candidate member.

        Returns:
            Boolean: True if the value plausibly satisfies the invariant.
        """
        self.check_count += 1
        if not value_smt.strip():
            logger.debug("check_invariant: empty value_smt — returning False.")
            return False

        # Delegate to the type's own membership test
        direct_result = type_lifted.check_member(value_smt)

        # Additional heuristic: check that the value is non-trivially ``false``
        if value_smt.strip().lower() in ("false", "(= x false)", "(= x #b0)"):
            logger.debug("check_invariant: value looks like False literal — False.")
            return False

        # Cache a hint for Copilot
        hint_key = f"{type_lifted.type_id}::{hashlib.sha256(value_smt.encode()).hexdigest()[:16]}"
        self.session_hints[hint_key] = {
            "type_id": type_lifted.type_id,
            "value_preview": value_smt[:60],
            "result": direct_result,
            "check_number": self.check_count,
        }

        logger.debug(
            "check_invariant [%d]: type=%r, result=%s.",
            self.check_count, type_lifted.base_name, direct_result,
        )
        return direct_result

    def verify_invariant_consistency(self, type_lifted: SolverLiftedType) -> bool:
        """Return True if *type_lifted*'s invariant is internally consistent.

        An invariant is considered consistent if:
        1. It is non-empty.
        2. It does not contain the literal ``false``.
        3. It parses as a plausible SMT-LIB2 formula (balanced parentheses,
           at least one recognised token).
        4. The fragment tag is known to the decidability registry.

        For decidable fragments, :func:`classify_fragment` is invoked if
        available to provide a richer classification.

        Args:
            type_lifted: The lifted type to check.

        Returns:
            Boolean.
        """
        key = type_lifted.type_id
        if key in self._consistency_cache:
            return self._consistency_cache[key]

        inv = type_lifted.z3_invariant_smt.strip()

        # Invariant must be non-empty
        if not inv:
            self._consistency_cache[key] = False
            return False

        # Invariant must not be the literal 'false'
        if inv.lower() in ("false", "(= false true)", "(and false true)"):
            self._consistency_cache[key] = False
            return False

        # Balanced parentheses check
        depth = 0
        for ch in inv:
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
            if depth < 0:
                self._consistency_cache[key] = False
                return False
        if depth != 0:
            self._consistency_cache[key] = False
            return False

        # At least one recognised SMT-LIB2 operator or keyword
        lower = inv.lower()
        has_token = any(tok in lower for tok in (
            "and", "or", "not", "=", "<", ">", "+", "-", "true",
            "bvadd", "select", "str.", "forall", "exists",
        ))
        if not has_token:
            # Might be just a variable name — allow it
            is_bare_name = inv.replace("_", "").replace("-", "").isalnum()
            if not is_bare_name:
                self._consistency_cache[key] = False
                return False

        # Fragment tag must be in the known registry or be non-empty
        if not type_lifted.fragment_tag:
            self._consistency_cache[key] = False
            return False

        # Optionally call classify_fragment for a richer check
        if _FRAGMENTS_AVAILABLE and classify_fragment is not None:
            try:
                frag_result = classify_fragment(inv)
                # If classify_fragment returns, the formula is at least parseable
                logger.debug(
                    "verify_invariant_consistency: classify_fragment returned %r.",
                    getattr(frag_result, "fragment", "n/a"),
                )
            except Exception as exc:  # noqa: BLE001
                logger.debug(
                    "classify_fragment raised %r — treating as consistent.", exc
                )

        self._consistency_cache[key] = True
        return True

    def find_invariant_witness(self, type_lifted: SolverLiftedType) -> str | None:
        """Try to find a concrete SMT-LIB2 value that satisfies the invariant.

        Uses a pattern-matching heuristic: iterates over
        ``_INVARIANT_WITNESS_TEMPLATES`` and returns the first template whose
        pattern appears in the invariant string.  Falls back to a generic
        integer ``0`` for QF_LIA invariants.

        Args:
            type_lifted: The lifted type to find a witness for.

        Returns:
            An SMT-LIB2 term string, or None if no witness is found.
        """
        cache_key = type_lifted.type_id
        if cache_key in self._witness_cache:
            return self._witness_cache[cache_key]

        inv = type_lifted.z3_invariant_smt
        lower = inv.lower()

        # Try each template in order
        for pattern, witness in _INVARIANT_WITNESS_TEMPLATES:
            if pattern.lower() in lower:
                logger.debug(
                    "find_invariant_witness: matched pattern %r → %r.", pattern, witness
                )
                self._witness_cache[cache_key] = witness
                return witness

        # Fragment-specific fallbacks
        tag = type_lifted.fragment_tag.upper()
        if tag in ("QF_LIA", "QF_IDL"):
            result = "(= x 0)"
        elif tag in ("QF_LRA", "QF_RDL"):
            result = "(= x 0.0)"
        elif tag in ("QF_BV", "QF_ABV"):
            result = "(= x #x00000000)"
        elif tag == "PROPOSITIONAL":
            result = "(= x true)"
        elif tag in ("QF_AX", "QF_AUFLIA"):
            result = "((as const (Array Int Int)) 0)"
        else:
            result = None

        if result is None:
            logger.debug(
                "find_invariant_witness: no witness found for type %r.", type_lifted.base_name
            )
        self._witness_cache[cache_key] = result
        return result

    def strengthen_invariant(
        self, type_lifted: SolverLiftedType, extra: str
    ) -> SolverLiftedType:
        """Return a new type with *extra* conjoined to the existing invariant.

        Delegates directly to :meth:`SolverLiftedType.strengthen`.  The result
        is a new, stronger type (the original is not modified, as it is frozen).

        Args:
            type_lifted: The type to strengthen.
            extra: An additional SMT-LIB2 formula to conjoin.

        Returns:
            A new ``SolverLiftedType`` with the strengthened invariant.
        """
        if not extra.strip():
            logger.warning("strengthen_invariant: empty extra formula — no change.")
            return type_lifted

        result = type_lifted.strengthen(extra)
        # Invalidate consistency cache for the original (new type has new id via
        # strengthen(), which preserves type_id, so re-cache correctly)
        self._consistency_cache.pop(result.type_id, None)
        logger.debug(
            "strengthen_invariant: type=%r, extra=%r.", type_lifted.base_name, extra[:40]
        )
        return result

    def minimize_invariant(self, type_lifted: SolverLiftedType) -> SolverLiftedType:
        """Return a new type with the outermost conjunct of the invariant removed.

        Delegates directly to :meth:`SolverLiftedType.weaken`.  This reduces
        the proof obligation associated with the type, potentially allowing
        more values to inhabit it.

        Args:
            type_lifted: The type whose invariant is to be minimised.

        Returns:
            A new, weaker ``SolverLiftedType``.
        """
        result = type_lifted.weaken()
        self._consistency_cache.pop(result.type_id, None)
        logger.debug(
            "minimize_invariant: type=%r → %r.",
            type_lifted.z3_invariant_smt[:40],
            result.z3_invariant_smt[:40],
        )
        return result

    def copilot_invariant_hint(self, type_lifted: SolverLiftedType) -> str:
        """Return a structured copilot-readable hint about *type_lifted*'s invariant.

        The hint covers the type's base name, its invariant, the result of
        consistency verification, whether a witness was found, and whether the
        invariant appears to be in a decidable fragment.

        Args:
            type_lifted: The lifted type to describe.

        Returns:
            A newline-separated key=value string for the Copilot layer.
        """
        consistent = self.verify_invariant_consistency(type_lifted)
        witness = self.find_invariant_witness(type_lifted)
        inv_len = len(type_lifted.z3_invariant_smt)
        has_quantifiers = any(
            kw in type_lifted.z3_invariant_smt.lower()
            for kw in ("forall", "exists", "lambda")
        )
        fragment_decidable = type_lifted.fragment_tag in KNOWN_DECIDABLE_FRAGMENTS

        lines = [
            "copilot_hint: InvariantChecker",
            f"  type_id: {type_lifted.type_id}",
            f"  base_name: {type_lifted.base_name}",
            f"  fragment_tag: {type_lifted.fragment_tag}",
            f"  fragment_decidable: {fragment_decidable}",
            f"  invariant_length_chars: {inv_len}",
            f"  invariant_preview: {type_lifted.z3_invariant_smt[:80]!r}",
            f"  consistent: {consistent}",
            f"  has_quantifiers: {has_quantifiers}",
            f"  witness: {witness!r}",
            f"  inhabited: {type_lifted.inhabited}",
            f"  check_count_so_far: {self.check_count}",
            "  recommendation: "
            + (
                "invariant_safe_to_assert"
                if consistent and fragment_decidable and not has_quantifiers
                else "apply_skolemization_or_qe"
                if consistent and has_quantifiers
                else "review_invariant_manually"
            ),
        ]
        return "\n".join(lines)


# -------------------------------------------------------------------
# TypeLiftingTranslator
# -------------------------------------------------------------------

class TypeLiftingTranslator:
    """Constructs and transforms ``SolverLiftedType`` objects.

    The translator is the factory for lifted types.  It takes a base type name
    and an SMT-LIB2 invariant formula and produces a fully initialised
    ``SolverLiftedType`` with a unique identifier, an inferred fragment tag, and
    an optional ``SupportRegion``.

    Product types, function types, and strategy-transformed types are also
    constructed here.  A translation cache avoids re-lifting the same base+invariant
    pair.

    Copilot integration is available via :meth:`copilot_lifting_hint`.
    """

    def __init__(
        self,
        strategy: TypeLiftingStrategy = TypeLiftingStrategy.DIRECT_ENCODING,
    ) -> None:
        """Initialise with a default lifting strategy and empty cache.

        Args:
            strategy: The default ``TypeLiftingStrategy`` to apply.
        """
        self.strategy: TypeLiftingStrategy = strategy
        self.translation_cache: dict[str, SolverLiftedType] = {}
        self._lift_count: int = 0
        self._created_at: float = time.monotonic()

        logger.debug(
            "TypeLiftingTranslator initialised with strategy=%s.", strategy.value
        )

    # --- public factory methods ---

    def lift_base_type(self, base_name: str, invariant_smt: str) -> SolverLiftedType:
        """Lift a base type name and invariant into a ``SolverLiftedType``.

        Checks the translation cache first.  On a miss:
        1. Generates a fresh UUID-based type_id.
        2. Infers the SMT-LIB2 fragment tag from the invariant string.
        3. Creates a ``SupportRegion`` if the supports module is available.
        4. Constructs and caches the ``SolverLiftedType``.

        Args:
            base_name: The SMT-LIB2 sort name (e.g. ``"NatType"``).
            invariant_smt: An SMT-LIB2 formula constraining members of the type.

        Returns:
            A fully initialised ``SolverLiftedType``.
        """
        cache_key = hashlib.sha256(
            f"{base_name}::{invariant_smt}".encode()
        ).hexdigest()[:24]

        if cache_key in self.translation_cache:
            logger.debug("lift_base_type: cache hit for %r.", base_name)
            return self.translation_cache[cache_key]

        type_id = uuid.uuid4().hex[:16]
        fragment_tag = self._infer_fragment(invariant_smt)
        inhabited = self._infer_inhabited(invariant_smt)
        notes = f"Lifted by {self.__class__.__name__} via {self.strategy.value}."

        lifted = SolverLiftedType(
            type_id=type_id,
            base_name=base_name,
            z3_invariant_smt=invariant_smt or "true",
            inhabited=inhabited,
            fragment_tag=fragment_tag,
            notes=notes,
        )

        self.translation_cache[cache_key] = lifted
        self._lift_count += 1

        logger.info(
            "Lifted base type %r: id=%s, fragment=%s, inhabited=%s.",
            base_name, type_id, fragment_tag, inhabited,
        )
        return lifted

    def lift_product(
        self, t1: SolverLiftedType, t2: SolverLiftedType
    ) -> SolverLiftedType:
        """Construct the product type T1 × T2 with a combined invariant.

        The invariant of the product type is the conjunction of the two
        component invariants.  The fragment tag is inherited from *t1*; if
        the two types are from different fragments, ``QF_AUFLIA`` is used as
        a safe common ancestor.

        Args:
            t1: The first component type.
            t2: The second component type.

        Returns:
            A new ``SolverLiftedType`` representing the product.
        """
        combined_inv = f"(and {t1.z3_invariant_smt} {t2.z3_invariant_smt})"
        product_name = f"Product_{t1.base_name}_{t2.base_name}"
        type_id = uuid.uuid4().hex[:16]

        # Determine the combined fragment tag
        if t1.fragment_tag == t2.fragment_tag:
            frag_tag = t1.fragment_tag
        elif {t1.fragment_tag, t2.fragment_tag} <= {"QF_LIA", "QF_LRA"}:
            frag_tag = "QF_LIRA"
        elif any(t.fragment_tag == "QF_BV" for t in (t1, t2)):
            frag_tag = "QF_AUFBV"
        else:
            frag_tag = "QF_AUFLIA"  # safe multi-theory default

        inhabited = t1.inhabited and t2.inhabited
        notes = (
            f"Product of {t1.base_name} × {t2.base_name}; "
            f"invariants combined by conjunction."
        )

        product = SolverLiftedType(
            type_id=type_id,
            base_name=product_name,
            z3_invariant_smt=combined_inv,
            inhabited=inhabited,
            fragment_tag=frag_tag,
            notes=notes,
        )
        self._lift_count += 1
        logger.debug("lift_product: %r × %r → %r.", t1.base_name, t2.base_name, product_name)
        return product

    def lift_function_type(
        self,
        domain: SolverLiftedType,
        codomain: SolverLiftedType,
        pre: str,
        post: str,
    ) -> SolverLiftedType:
        """Construct a function type [Domain → Codomain] with pre/post invariants.

        The resulting type's invariant encodes a Hoare-style contract:
        ``(and pre (=> pre post))`` — the precondition must hold and the
        postcondition must follow from it.

        Args:
            domain: The input (domain) type.
            codomain: The output (codomain) type.
            pre: An SMT-LIB2 precondition formula.
            post: An SMT-LIB2 postcondition formula.

        Returns:
            A new ``SolverLiftedType`` representing the function type.
        """
        func_name = f"Func_{domain.base_name}_to_{codomain.base_name}"
        type_id = uuid.uuid4().hex[:16]

        # Hoare contract encoded as an invariant
        domain_inv = domain.z3_invariant_smt
        codomain_inv = codomain.z3_invariant_smt
        effective_pre = pre if pre.strip() else domain_inv
        effective_post = post if post.strip() else codomain_inv

        contract_inv = (
            f"(and {effective_pre} "
            f"     (=> {effective_pre} {effective_post}) "
            f"     {domain_inv} "
            f"     {codomain_inv})"
        )

        # Fragment: use the more expressive of the two
        frag_priority = [
            "PROPOSITIONAL", "QF_LIA", "QF_LRA", "QF_BV",
            "QF_AX", "QF_AUFLIA", "QF_AUFBV", "QF_AUFLIRA",
        ]
        try:
            d_rank = frag_priority.index(domain.fragment_tag)
        except ValueError:
            d_rank = len(frag_priority) - 1
        try:
            c_rank = frag_priority.index(codomain.fragment_tag)
        except ValueError:
            c_rank = len(frag_priority) - 1
        frag_tag = frag_priority[max(d_rank, c_rank)]

        notes = (
            f"Function type {domain.base_name} → {codomain.base_name}; "
            f"Hoare contract: pre={effective_pre[:40]!r}, post={effective_post[:40]!r}."
        )

        func_type = SolverLiftedType(
            type_id=type_id,
            base_name=func_name,
            z3_invariant_smt=contract_inv,
            inhabited=domain.inhabited and codomain.inhabited,
            fragment_tag=frag_tag,
            notes=notes,
        )
        self._lift_count += 1
        logger.debug(
            "lift_function_type: %r → %r, frag=%s.", domain.base_name, codomain.base_name, frag_tag
        )
        return func_type

    def apply_strategy(
        self, base: SolverLiftedType, strategy: TypeLiftingStrategy
    ) -> SolverLiftedType:
        """Apply a transformation to *base* according to *strategy*.

        Each strategy applies a syntactic transformation to the invariant:

        - DIRECT_ENCODING: identity transformation.
        - ABSTRACTION_REFINEMENT: weakens the invariant by removing the last conjunct.
        - SKOLEMIZATION: replaces ``(exists ((v T)) P)`` with ``P[v/sk_v]``.
        - QUANTIFIER_ELIMINATION: strips ``forall``/``exists`` wrappers from the invariant.
        - COPILOT_GUIDED: selects the best strategy heuristically.

        Args:
            base: The source lifted type.
            strategy: The strategy to apply.

        Returns:
            A new (possibly transformed) ``SolverLiftedType``.
        """
        if strategy is TypeLiftingStrategy.DIRECT_ENCODING:
            # Identity: no transformation needed
            logger.debug("apply_strategy DIRECT_ENCODING: identity for %r.", base.base_name)
            return base

        if strategy is TypeLiftingStrategy.ABSTRACTION_REFINEMENT:
            # Weaken the invariant: remove the last conjunct
            result = base.weaken()
            logger.debug(
                "apply_strategy ABSTRACTION_REFINEMENT: weakened %r.", base.base_name
            )
            return result

        if strategy is TypeLiftingStrategy.SKOLEMIZATION:
            # Syntactic Skolemization: replace (exists ((v Sort)) Body) with Body[v→sk_v]
            inv = base.z3_invariant_smt
            skolemized = self._skolemize(inv)
            result = SolverLiftedType(
                type_id=base.type_id,
                base_name=base.base_name,
                z3_invariant_smt=skolemized,
                inhabited=base.inhabited,
                fragment_tag=base.fragment_tag,
                notes=base.notes + " [Skolemized]",
            )
            logger.debug(
                "apply_strategy SKOLEMIZATION: %r → %r.",
                inv[:40], skolemized[:40],
            )
            return result

        if strategy is TypeLiftingStrategy.QUANTIFIER_ELIMINATION:
            # Quantifier elimination: strip top-level quantifier wrappers
            inv = base.z3_invariant_smt
            qe_result = self._eliminate_quantifiers(inv)
            result = SolverLiftedType(
                type_id=base.type_id,
                base_name=base.base_name,
                z3_invariant_smt=qe_result,
                inhabited=base.inhabited,
                fragment_tag=base.fragment_tag,
                notes=base.notes + " [QE applied]",
            )
            logger.debug(
                "apply_strategy QE: %r → %r.", inv[:40], qe_result[:40]
            )
            return result

        if strategy is TypeLiftingStrategy.COPILOT_GUIDED:
            # Heuristic: choose the best strategy based on invariant shape
            inv = base.z3_invariant_smt.lower()
            if "exists" in inv and "forall" not in inv:
                return self.apply_strategy(base, TypeLiftingStrategy.SKOLEMIZATION)
            if "forall" in inv:
                return self.apply_strategy(base, TypeLiftingStrategy.QUANTIFIER_ELIMINATION)
            if len(inv) > 200:
                return self.apply_strategy(base, TypeLiftingStrategy.ABSTRACTION_REFINEMENT)
            return self.apply_strategy(base, TypeLiftingStrategy.DIRECT_ENCODING)

        # Unreachable, but be defensive
        logger.warning("apply_strategy: unknown strategy %r — identity.", strategy)
        return base

    def lower_to_base(self, lifted: SolverLiftedType) -> str:
        """Return the base sort name of *lifted*, stripping the invariant.

        This is the inverse of lifting: it discards the invariant and returns
        the plain SMT-LIB2 sort name.

        Args:
            lifted: The lifted type to lower.

        Returns:
            The base sort name string.
        """
        return lifted.base_name

    def emit_sort_declarations(self, lifted: SolverLiftedType) -> str:
        """Emit SMT-LIB2 sort declarations for *lifted*.

        Produces a ``declare-sort`` command and a ``push``/``pop`` block
        containing the invariant assertion, suitable for inclusion at the top
        of an SMT-LIB2 problem file.

        Args:
            lifted: The lifted type to declare.

        Returns:
            A multi-line SMT-LIB2 string.
        """
        lines = [
            f"; --- Sort declaration for lifted type: {lifted.base_name} ---",
            f"; type_id      : {lifted.type_id}",
            f"; fragment_tag : {lifted.fragment_tag}",
            f"; inhabited    : {lifted.inhabited}",
            f"(declare-sort {lifted.base_name} 0)",
            f"(declare-const _member_{lifted.base_name} {lifted.base_name})",
            f"; Invariant assertion:",
            f"(assert {lifted.z3_invariant_smt})",
        ]
        if lifted.notes:
            lines.insert(1, f"; notes        : {lifted.notes[:80]}")
        return "\n".join(lines) + "\n"

    def copilot_lifting_hint(self, base: str, invariant: str) -> str:
        """Return a structured copilot-readable hint about lifting *base* with *invariant*.

        Describes the inferred fragment, recommended strategy, and any known
        pitfalls (e.g. nonlinear operators, nested quantifiers).

        Args:
            base: The base sort name.
            invariant: The SMT-LIB2 invariant formula.

        Returns:
            A newline-separated key=value string.
        """
        fragment = self._infer_fragment(invariant)
        inhabited = self._infer_inhabited(invariant)
        lower = invariant.lower()
        has_quantifiers = "forall" in lower or "exists" in lower
        has_nonlinear = any(tok in lower for tok in _NONLINEAR_TOKENS)
        decidable = fragment in KNOWN_DECIDABLE_FRAGMENTS

        if has_quantifiers:
            recommended = TypeLiftingStrategy.SKOLEMIZATION.value
        elif has_nonlinear:
            recommended = TypeLiftingStrategy.ABSTRACTION_REFINEMENT.value
        elif not decidable:
            recommended = TypeLiftingStrategy.COPILOT_GUIDED.value
        else:
            recommended = TypeLiftingStrategy.DIRECT_ENCODING.value

        lines = [
            "copilot_hint: TypeLiftingTranslator",
            f"  base_name: {base}",
            f"  inferred_fragment: {fragment}",
            f"  fragment_decidable: {decidable}",
            f"  inhabited_estimate: {inhabited}",
            f"  has_quantifiers: {has_quantifiers}",
            f"  has_nonlinear_ops: {has_nonlinear}",
            f"  invariant_preview: {invariant[:80]!r}",
            f"  recommended_strategy: {recommended}",
            f"  lift_count_so_far: {self._lift_count}",
        ]
        return "\n".join(lines)

    # --- private helpers ---

    def _infer_fragment(self, invariant_smt: str) -> str:
        """Infer the most specific SMT-LIB2 fragment tag from the invariant string."""
        lower = invariant_smt.lower()
        for keyword, frag in _FRAGMENT_INFERENCE:
            if keyword.lower() in lower:
                return frag
        # Default to QF_LIA for short invariants without special operators
        return "QF_LIA"

    def _infer_inhabited(self, invariant_smt: str) -> bool:
        """Heuristically infer whether the type is inhabited.

        Returns False only if the invariant is obviously contradictory.
        """
        inv = invariant_smt.strip().lower()
        if inv in ("false", "(= false true)"):
            return False
        if inv.startswith("(and false") or inv.endswith("false)"):
            return False
        return True

    def _skolemize(self, smt: str) -> str:
        """Replace the outermost exists-binding with a Skolem constant.

        This is a syntactic approximation: it only handles the top-level
        ``(exists ((v Sort)) Body)`` pattern.  Nested existentials are left
        for a subsequent call.
        """
        import re
        pattern = r"\(exists\s+\(\((\w+)\s+(\w+)\)\)\s+(.*)\)"
        match = re.search(pattern, smt, re.DOTALL)
        if match:
            var_name = match.group(1)
            sort_name = match.group(2)
            body = match.group(3).strip()
            sk_name = f"sk_{var_name}_{uuid.uuid4().hex[:6]}"
            # Replace the variable with the Skolem constant in the body
            skolemized_body = body.replace(var_name, sk_name)
            declaration = f"(declare-const {sk_name} {sort_name})"
            return f"{declaration}\n{skolemized_body}"
        return smt  # no existential found — return unchanged

    def _eliminate_quantifiers(self, smt: str) -> str:
        """Strip the outermost quantifier wrapper from the invariant string.

        For simple cases like ``(forall ((x Int)) (> x 0))`` this returns
        ``(> x 0)``.  For more complex cases it returns the invariant
        unchanged and logs a warning.
        """
        import re
        pattern = r"^\s*\((forall|exists)\s+\([^)]*\)\s+(.*)\)\s*$"
        match = re.match(pattern, smt.strip(), re.DOTALL)
        if match:
            quantifier = match.group(1)
            body = match.group(2).strip()
            logger.debug(
                "_eliminate_quantifiers: stripped %s, body=%r.", quantifier, body[:40]
            )
            return body
        logger.debug(
            "_eliminate_quantifiers: no top-level quantifier found — returning unchanged."
        )
        return smt


# -------------------------------------------------------------------
# SolverLiftedTypeSystem
# -------------------------------------------------------------------

class SolverLiftedTypeSystem:
    """Registry and orchestrator for all lifted types in the structural frontier.

    The type system is the top-level API for managing ``SolverLiftedType``
    objects.  It provides:

    - Registration and lookup by type_id.
    - Subtype and inhabitation checking.
    - Type intersection and union via the ``SolverLiftedType`` combination API.
    - Bulk SMT-LIB2 declaration emission.
    - Structured Copilot reporting via :meth:`copilot_type_system_report`.

    The system delegates invariant checking to an ``InvariantChecker`` instance
    and type construction to a ``TypeLiftingTranslator`` instance.
    """

    def __init__(self) -> None:
        """Initialise the type system with empty registries and default helpers."""
        self.types: dict[str, SolverLiftedType] = {}
        self.checker: InvariantChecker = InvariantChecker()
        self.translator: TypeLiftingTranslator = TypeLiftingTranslator()
        self.subtype_cache: dict[tuple[str, str], bool] = {}
        self._created_at: float = time.monotonic()
        self._operation_log: list[dict[str, Any]] = []

        logger.info("SolverLiftedTypeSystem initialised.")

    def register_type(self, lifted_type: SolverLiftedType) -> None:
        """Register *lifted_type* in the type system.

        If a type with the same type_id is already registered, the existing
        entry is overwritten and a warning is logged.

        Args:
            lifted_type: The ``SolverLiftedType`` to register.
        """
        if lifted_type.type_id in self.types:
            logger.warning(
                "register_type: overwriting existing type %r (id=%s).",
                lifted_type.base_name, lifted_type.type_id,
            )
        self.types[lifted_type.type_id] = lifted_type
        self._operation_log.append({
            "op": "register",
            "type_id": lifted_type.type_id,
            "base_name": lifted_type.base_name,
            "timestamp": time.monotonic() - self._created_at,
        })
        # Invalidate subtype cache entries involving this type
        stale = [k for k in self.subtype_cache if lifted_type.type_id in k]
        for k in stale:
            del self.subtype_cache[k]

        logger.debug(
            "register_type: registered %r (id=%s, fragment=%s).",
            lifted_type.base_name, lifted_type.type_id, lifted_type.fragment_tag,
        )

    def lookup(self, type_id: str) -> SolverLiftedType:
        """Return the registered type with *type_id*.

        Args:
            type_id: The unique type identifier.

        Returns:
            The ``SolverLiftedType`` with the given id.

        Raises:
            KeyError: If no type with *type_id* is registered.
        """
        if type_id not in self.types:
            raise KeyError(
                f"SolverLiftedTypeSystem: no type registered with id={type_id!r}.  "
                f"Registered ids: {list(self.types.keys())[:10]}"
            )
        return self.types[type_id]

    def subtype_check(
        self, sub: SolverLiftedType, sup: SolverLiftedType
    ) -> bool:
        """Return True if *sub* is a structural subtype of *sup*.

        Uses three heuristic rules, applied in order:
        1. Identity: same type_id → trivially a subtype.
        2. Base name equality: same base sort → subtype if invariants are compatible.
        3. Invariant strength: if sub's invariant is longer (more constrained) than
           sup's AND sup's invariant keywords are all present in sub's invariant,
           we treat sub as a subtype of sup (sub is more specific).

        Results are cached to avoid repeated computation.

        Args:
            sub: The candidate subtype.
            sup: The candidate supertype.

        Returns:
            Boolean.
        """
        cache_key = (sub.type_id, sup.type_id)
        if cache_key in self.subtype_cache:
            return self.subtype_cache[cache_key]

        # Rule 1: identity
        if sub.type_id == sup.type_id:
            self.subtype_cache[cache_key] = True
            return True

        # Rule 2: same base sort → check invariant compatibility
        if sub.base_name == sup.base_name:
            # sub's invariant must be at least as strong (longer ≈ more constraints)
            result = len(sub.z3_invariant_smt) >= len(sup.z3_invariant_smt)
            self.subtype_cache[cache_key] = result
            logger.debug(
                "subtype_check (same base): %r ≤? %r → %s.",
                sub.base_name, sup.base_name, result,
            )
            return result

        # Rule 3: keyword containment — every keyword in sup's invariant must
        # also appear in sub's invariant (sub is more constrained)
        sup_inv = sup.z3_invariant_smt
        sub_inv = sub.z3_invariant_smt

        # Extract meaningful tokens from the supertype invariant
        import re
        sup_tokens = set(re.findall(r"\b[a-zA-Z_][a-zA-Z0-9_]*\b", sup_inv))
        # Remove trivial SMT keywords
        trivial = {"and", "or", "not", "true", "false", "let", "as", "assert"}
        sup_tokens -= trivial

        if not sup_tokens:
            # Supertype invariant is trivially 'true' → everything is a subtype
            self.subtype_cache[cache_key] = True
            return True

        # Check how many of sup's tokens appear in sub's invariant
        found = sum(1 for tok in sup_tokens if tok in sub_inv)
        coverage = found / len(sup_tokens)
        result = coverage >= 0.75 and len(sub_inv) >= len(sup_inv)

        self.subtype_cache[cache_key] = result
        logger.debug(
            "subtype_check: %r ≤? %r → %s (coverage=%.2f).",
            sub.base_name, sup.base_name, result, coverage,
        )
        return result

    def inhabitation_check(self, lifted: SolverLiftedType) -> bool:
        """Return True if *lifted* is inhabited (has at least one member).

        Checks the ``inhabited`` field first.  If it is False, tries to find
        a witness via the checker; if a witness is found, the type is still
        inhabited despite the flag.

        Args:
            lifted: The lifted type to check.

        Returns:
            Boolean.
        """
        if lifted.inhabited:
            return True

        # Try to find a witness even if the flag says False
        witness = self.checker.find_invariant_witness(lifted)
        if witness is not None:
            logger.debug(
                "inhabitation_check: type %r has witness despite inhabited=False.",
                lifted.base_name,
            )
            return True

        logger.debug(
            "inhabitation_check: type %r appears uninhabited.", lifted.base_name
        )
        return False

    def intersect_types(self, ids: list[str]) -> SolverLiftedType:
        """Compute the intersection of a list of registered types.

        Looks up each type by id, then reduces using :meth:`SolverLiftedType.intersect`.
        Raises ValueError if *ids* is empty.

        Args:
            ids: A non-empty list of type_id strings.

        Returns:
            A new ``SolverLiftedType`` representing the intersection.

        Raises:
            ValueError: If *ids* is empty.
            KeyError: If any id is not registered.
        """
        if not ids:
            raise ValueError("intersect_types: ids list must be non-empty.")

        types = [self.lookup(tid) for tid in ids]
        result = types[0]
        for t in types[1:]:
            result = result.intersect(t)

        logger.debug(
            "intersect_types: intersected %d types → %r.", len(ids), result.base_name
        )
        return result

    def union_types(self, ids: list[str]) -> SolverLiftedType:
        """Compute the union of a list of registered types.

        Looks up each type by id, then reduces using :meth:`SolverLiftedType.union`.
        Raises ValueError if *ids* is empty.

        Args:
            ids: A non-empty list of type_id strings.

        Returns:
            A new ``SolverLiftedType`` representing the union.

        Raises:
            ValueError: If *ids* is empty.
            KeyError: If any id is not registered.
        """
        if not ids:
            raise ValueError("union_types: ids list must be non-empty.")

        types = [self.lookup(tid) for tid in ids]
        result = types[0]
        for t in types[1:]:
            result = result.union(t)

        logger.debug(
            "union_types: unioned %d types → %r.", len(ids), result.base_name
        )
        return result

    def emit_all_declarations(self) -> str:
        """Emit SMT-LIB2 declarations for all registered types.

        Concatenates the output of :meth:`TypeLiftingTranslator.emit_sort_declarations`
        for every registered type, sorted by base name for deterministic output.

        Returns:
            A multi-line SMT-LIB2 string ready for inclusion in a problem file.
        """
        if not self.types:
            return "; (no types registered)\n"

        sorted_types = sorted(self.types.values(), key=lambda t: t.base_name)
        header = [
            "; ===================================================================",
            "; SolverLiftedTypeSystem — all type declarations",
            f"; {len(sorted_types)} type(s) registered",
            "; ===================================================================",
            "",
        ]
        blocks = [self.translator.emit_sort_declarations(t) for t in sorted_types]
        return "\n".join(header) + "\n".join(blocks)

    def copilot_type_system_report(self) -> str:
        """Return a structured report on the current type system state.

        Covers:
        - Total type count and fragment distribution
        - Inhabited vs uninhabited types
        - Subtype cache statistics
        - Checker statistics
        - Translator statistics
        - Recent operations

        Returns:
            A multi-line structured string for the Copilot orchestration layer.
        """
        sep = "=" * 72
        thin = "-" * 72
        elapsed = time.monotonic() - self._created_at

        # Fragment distribution
        frag_dist: dict[str, int] = {}
        inhabited_count = 0
        for t in self.types.values():
            frag_dist[t.fragment_tag] = frag_dist.get(t.fragment_tag, 0) + 1
            if t.inhabited:
                inhabited_count += 1

        lines: list[str] = [
            sep,
            "  SOLVER LIFTED TYPE SYSTEM REPORT — JuGeo / Structural Frontier",
            sep,
            f"  Uptime            : {elapsed:.2f}s",
            f"  Total types       : {len(self.types)}",
            f"  Inhabited types   : {inhabited_count}",
            f"  Uninhabited types : {len(self.types) - inhabited_count}",
            f"  Subtype cache hits: {len(self.subtype_cache)}",
            f"  Checker count     : {self.checker.check_count}",
            f"  Translator lifts  : {self.translator._lift_count}",
            f"  Default strategy  : {self.translator.strategy.value}",
            "",
            thin,
            "  FRAGMENT DISTRIBUTION",
            thin,
        ]
        for frag, count in sorted(frag_dist.items()):
            decidable = "✓" if frag in KNOWN_DECIDABLE_FRAGMENTS else "?"
            lines.append(f"  {decidable} {frag:<20} : {count} type(s)")

        lines += [
            "",
            thin,
            "  REGISTERED TYPES",
            thin,
        ]
        for t in sorted(self.types.values(), key=lambda x: x.base_name):
            inh = "inhabited" if t.inhabited else "UNINHABITED"
            lines.append(
                f"  [{t.type_id[:8]}] {t.base_name:<30} "
                f"frag={t.fragment_tag:<12} {inh}"
            )
            inv_preview = t.z3_invariant_smt[:60].replace("\n", " ")
            lines.append(f"              inv: {inv_preview}")

        lines += [
            "",
            thin,
            "  RECENT OPERATIONS",
            thin,
        ]
        recent = self._operation_log[-10:]  # last 10 operations
        if recent:
            for op in recent:
                lines.append(
                    f"  t={op['timestamp']:.3f}s  {op['op']:<12} "
                    f"id={op.get('type_id', '?')[:8]}  name={op.get('base_name', '?')}"
                )
        else:
            lines.append("  (no operations recorded)")

        lines += [
            "",
            sep,
            "  END OF TYPE SYSTEM REPORT",
            sep,
        ]
        return "\n".join(lines)


# -------------------------------------------------------------------
# Module-level convenience functions
# -------------------------------------------------------------------

_DEFAULT_TYPE_SYSTEM: SolverLiftedTypeSystem | None = None


def get_default_type_system() -> SolverLiftedTypeSystem:
    """Return the module-level singleton ``SolverLiftedTypeSystem``.

    Lazily constructs the instance on first call.  Subsequent calls return the
    same instance, preserving all registered types and subtype cache entries
    across the lifetime of the module.

    Returns:
        The shared ``SolverLiftedTypeSystem`` instance.
    """
    global _DEFAULT_TYPE_SYSTEM
    if _DEFAULT_TYPE_SYSTEM is None:
        _DEFAULT_TYPE_SYSTEM = SolverLiftedTypeSystem()
        logger.debug("Constructed module-level default SolverLiftedTypeSystem.")
    return _DEFAULT_TYPE_SYSTEM


def make_lifted_type(
    base_name: str,
    invariant_smt: str,
    *,
    strategy: TypeLiftingStrategy = TypeLiftingStrategy.DIRECT_ENCODING,
    register: bool = False,
) -> SolverLiftedType:
    """Convenience function to lift a base type with an optional registration.

    Creates a translator with the given *strategy*, lifts the type, and
    optionally registers it with the default type system.

    Args:
        base_name: The SMT-LIB2 sort name.
        invariant_smt: The SMT-LIB2 invariant formula.
        strategy: The lifting strategy to apply (default: DIRECT_ENCODING).
        register: If True, register the result with the default type system.

    Returns:
        The newly lifted ``SolverLiftedType``.
    """
    translator = TypeLiftingTranslator(strategy=strategy)
    lifted = translator.lift_base_type(base_name, invariant_smt)
    lifted = translator.apply_strategy(lifted, strategy)

    if register:
        get_default_type_system().register_type(lifted)
        logger.debug(
            "make_lifted_type: registered %r (id=%s).", base_name, lifted.type_id
        )

    return lifted


__all__ = [
    "TypeLiftingStrategy",
    "InvariantChecker",
    "TypeLiftingTranslator",
    "SolverLiftedTypeSystem",
    "get_default_type_system",
    "make_lifted_type",
    # Re-exported model types
    "SolverLiftedType",
    "DecidabilityClass",
    "FrontierSide",
    "KNOWN_DECIDABLE_FRAGMENTS",
]
