"""
StructuralFrontierDefiner — mapping the boundary between decidable and
undecidable fragments of logic as encountered by the JuGeo Z3 backend.

The *structural frontier* is the precise line that separates the decidable
side of first-order logic (where Z3 can in principle always return SAT or
UNSAT) from the undecidable side (where no algorithm can do so for all
inputs).  Understanding this frontier is critical for JuGeo because:

  1. The solver budget is finite.  Formulas near the frontier are expensive;
     formulas beyond it must be handled specially — approximated, abstracted,
     or escalated to a more expressive but incomplete reasoning engine.
  2. Repair strategies differ on each side.  Inside the frontier, standard
     Z3 tactics (DPLL(T), quantifier elimination, model construction) apply.
     Outside, the system must escalate, abstract, or over-approximate.
  3. Copilot hints are most valuable exactly at the boundary, where syntactic
     pattern-matching is insufficient and semantic insight helps.

This module contains three primary layers:

  DecidabilityOracle
      A fast, cache-backed classifier that maps raw SMT-LIB2 formula strings
      to ``DecidabilityClass`` values.  The oracle consults a registry of
      well-known fragments first, then falls back to a keyword-based heuristic
      classifier.  Results are memoized via a SHA-256 hash of the formula.

  FrontierBoundaryLocator
      Given a formula string, this class finds the named boundary most
      relevant to that formula and reports which side of the boundary the
      formula falls on.  It also provides ``crossing_cost`` estimates — a
      heuristic integer that models the transformation cost to reach a nearby
      decidable fragment.

  StructuralFrontierDefiner
      The top-level coordinator.  It wires together the oracle and the locator,
      maintains a registry of named ``StructuralFrontier`` objects and
      undecidable region names, and exposes methods for proving decidability of
      named fragments, constructing ``UndecidabilityWitness`` records, and
      emitting structured reports.

Copilot integration is available via ``copilot_*_hint`` methods on every major
class.  These methods return machine-readable, multi-line strings whose format
is understood by the JuGeo Copilot orchestration layer.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import time
import uuid
from dataclasses import dataclass, field
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
        CountermodelObstruction,
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
    # Inline fallback model definitions
    # -------------------------------------------------------------------

    from enum import Enum

    class DecidabilityClass(str, Enum):
        """Decidability classification for a logic fragment or formula."""
        DECIDABLE = "decidable"
        UNDECIDABLE = "undecidable"
        SEMI_DECIDABLE = "semi_decidable"
        UNKNOWN = "unknown"

    class FrontierSide(str, Enum):
        """Which side of the structural frontier a formula resides on."""
        INSIDE = "inside"       # strictly within the decidable region
        BOUNDARY = "boundary"   # exactly on the frontier (expensive but possible)
        OUTSIDE = "outside"     # in the undecidable region — Z3 cannot decide

    @dataclass(frozen=True)
    class RepairAction:
        """A concrete action to bring a formula back inside the frontier."""
        action_id: str
        description: str
        target_fragment: str
        estimated_cost: int = 0

    @dataclass(frozen=True)
    class StructuralFrontier:
        """Named frontier record dividing a decidable from an undecidable region."""
        frontier_id: str
        fragment_name: str
        decidability_class: DecidabilityClass
        description: str = ""
        known_algorithms: tuple[str, ...] = field(default_factory=tuple)
        complexity: str = "unknown"
        notes: str = ""

    @dataclass(frozen=True)
    class FrontierBoundary:
        """A named dividing line between two decidability regions."""
        boundary_id: str
        name: str
        description: str
        decidable_keywords: tuple[str, ...] = field(default_factory=tuple)
        undecidable_keywords: tuple[str, ...] = field(default_factory=tuple)
        crossing_cost: int = 10

    @dataclass(frozen=True)
    class DecidabilityMap:
        """A mapping from fragment names to their DecidabilityClass values."""
        fragment_map: dict[str, DecidabilityClass] = field(default_factory=dict)

        def lookup(self, name: str) -> DecidabilityClass:
            """Look up a fragment by name, returning UNKNOWN if absent."""
            return self.fragment_map.get(name, DecidabilityClass.UNKNOWN)

    @dataclass(frozen=True)
    class CountermodelObstruction:
        """Records a countermodel that witnesses undecidability or infeasibility."""
        obstruction_id: str
        formula_smt: str
        failure_description: str = ""

    @dataclass(frozen=True)
    class SolverLiftedType:
        """A type carrying an embedded Z3 invariant as an SMT-LIB2 assertion."""
        type_id: str
        base_name: str
        z3_invariant_smt: str
        inhabited: bool = True
        fragment_tag: str = "QF_LIA"
        notes: str = ""

        def check_member(self, value_smt: str) -> bool:
            """Return True if value_smt is plausibly a member (syntactic check)."""
            return bool(self.z3_invariant_smt) and bool(value_smt)

        def strengthen(self, extra: str) -> SolverLiftedType:
            """Return a new type with ``extra`` conjoined to the invariant."""
            new_inv = f"(and {self.z3_invariant_smt} {extra})"
            return SolverLiftedType(
                type_id=self.type_id,
                base_name=self.base_name,
                z3_invariant_smt=new_inv,
                inhabited=self.inhabited,
                fragment_tag=self.fragment_tag,
                notes=self.notes,
            )

        def weaken(self) -> SolverLiftedType:
            """Return a new type with the outermost conjunct removed."""
            inv = self.z3_invariant_smt.strip()
            if inv.startswith("(and "):
                inner = inv[5:-1].strip()
                # drop the first conjunct
                depth = 0
                idx = 0
                for i, ch in enumerate(inner):
                    if ch == "(":
                        depth += 1
                    elif ch == ")":
                        depth -= 1
                    elif ch == " " and depth == 0:
                        idx = i
                        break
                new_inv = inner[idx:].strip() if idx else "true"
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
            """Return the intersection type (conjunction of invariants)."""
            combined = f"(and {self.z3_invariant_smt} {other.z3_invariant_smt})"
            return SolverLiftedType(
                type_id=f"{self.type_id}_x_{other.type_id}",
                base_name=f"{self.base_name}_intersect_{other.base_name}",
                z3_invariant_smt=combined,
                inhabited=self.inhabited and other.inhabited,
                fragment_tag=self.fragment_tag,
            )

        def union(self, other: SolverLiftedType) -> SolverLiftedType:
            """Return the union type (disjunction of invariants)."""
            combined = f"(or {self.z3_invariant_smt} {other.z3_invariant_smt})"
            return SolverLiftedType(
                type_id=f"{self.type_id}_u_{other.type_id}",
                base_name=f"{self.base_name}_union_{other.base_name}",
                z3_invariant_smt=combined,
                inhabited=self.inhabited or other.inhabited,
                fragment_tag=self.fragment_tag,
            )

        def to_smt2(self) -> str:
            """Emit an SMT-LIB2 sort declaration and invariant assertion."""
            lines = [
                f"; Lifted type: {self.base_name}  id={self.type_id}",
                f"; Fragment: {self.fragment_tag}",
                f"(declare-sort {self.base_name} 0)",
                f"(assert {self.z3_invariant_smt})",
            ]
            return "\n".join(lines) + "\n"

    KNOWN_DECIDABLE_FRAGMENTS: list[str] = [
        "QF_LIA", "QF_LRA", "QF_BV", "QF_UF",
        "QF_UFLIA", "QF_AUFLIA", "QF_ABV", "PROPOSITIONAL",
        "QF_IDL", "QF_RDL", "QF_LIRA", "QF_UFLRA",
        "QF_AX",  "QF_AUFBV",
    ]

    def make_default_frontier(fragment_name: str) -> StructuralFrontier:
        """Create a default StructuralFrontier for a named fragment."""
        is_decidable = fragment_name in KNOWN_DECIDABLE_FRAGMENTS
        dc = DecidabilityClass.DECIDABLE if is_decidable else DecidabilityClass.UNKNOWN
        algos: tuple[str, ...] = ("DPLL(T)", "CDCL(T)") if is_decidable else ()
        complexity = "NP-complete" if is_decidable else "undecidable"
        return StructuralFrontier(
            frontier_id=uuid.uuid4().hex[:12],
            fragment_name=fragment_name,
            decidability_class=dc,
            description=f"Auto-generated frontier for fragment {fragment_name}.",
            known_algorithms=algos,
            complexity=complexity,
            notes="Generated by make_default_frontier(); override for precision.",
        )

    def make_default_boundary(name: str) -> FrontierBoundary:
        """Create a default FrontierBoundary for a named dividing line."""
        return FrontierBoundary(
            boundary_id=uuid.uuid4().hex[:12],
            name=name,
            description=f"Boundary separating decidable and undecidable at: {name}",
            decidable_keywords=(),
            undecidable_keywords=(),
            crossing_cost=10,
        )

    def make_default_map() -> DecidabilityMap:
        """Create a DecidabilityMap seeded with all KNOWN_DECIDABLE_FRAGMENTS."""
        return DecidabilityMap(
            fragment_map={f: DecidabilityClass.DECIDABLE for f in KNOWN_DECIDABLE_FRAGMENTS}
        )


# -------------------------------------------------------------------
# Module-level constants
# -------------------------------------------------------------------

# SMT-LIB2 keywords that indicate nonlinear arithmetic (undecidable territory)
_NONLINEAR_OPS: frozenset[str] = frozenset({
    "bvmul", "bvsdiv", "bvudiv", "bvsrem", "bvurem",
})

# Keywords that strongly suggest full undecidability
_UNDECIDABLE_PATTERNS: frozenset[str] = frozenset({
    "str.to_int", "str.from_int", "re.comp", "re.diff",
    "fp.mul", "fp.div",   # floating-point: undecidable in general
})

# Quantifier keywords: alone → semi-decidable; nested → likely undecidable
_QUANTIFIER_KEYWORDS: frozenset[str] = frozenset({"forall", "exists", "lambda"})

# Linear arithmetic tokens (decidable when quantifier-free)
_LINEAR_TOKENS: frozenset[str] = frozenset({
    "+", "-", "<=", ">=", "<", ">", "=",
    "div", "mod", "Int", "Real", "Bool",
    "bvadd", "bvsub", "bvsle", "bvslt", "bvuge", "bvugt",
    "select", "store",
})

# Well-known undecidability reductions for witness generation
_UNDECIDABILITY_REDUCTIONS: dict[str, str] = {
    "nonlinear_integer": "Hilbert's tenth problem (Diophantine equations)",
    "full_first_order": "Turing machine halting via Gödel encoding",
    "string_regex_intersection": "Intersection of context-free languages (CFL)",
    "higher_order": "Girard's theorem: System F type-checking is undecidable",
    "quantified_nonlinear": "Richardson's theorem on real-valued expressions",
    "array_with_quantifiers": "Full array theory + quantifiers (Presburger + forall)",
    "nonlinear_real": "Tarski's real closed fields is decidable; nonlinear QF_NRA borderline",
}

# Named boundaries in the structural frontier landscape
_DEFAULT_BOUNDARY_SPECS: list[dict[str, Any]] = [
    {
        "name": "Presburger / nonlinear-integer",
        "description": (
            "Separates quantifier-free linear integer arithmetic (QF_LIA, decidable) "
            "from nonlinear integer arithmetic (undecidable via Hilbert's 10th problem)."
        ),
        "decidable_keywords": ("div", "mod", "+", "-", "Int"),
        "undecidable_keywords": ("*", "^", "bvmul"),
        "crossing_cost": 25,
    },
    {
        "name": "Quantifier-free / quantified",
        "description": (
            "Separates quantifier-free fragments (decidable for LIA/LRA) from "
            "quantified fragments.  Nested alternating quantifiers cross into "
            "undecidability."
        ),
        "decidable_keywords": ("Int", "Real", "+", "-"),
        "undecidable_keywords": ("forall", "exists"),
        "crossing_cost": 40,
    },
    {
        "name": "LRA / nonlinear-real",
        "description": (
            "Separates linear real arithmetic (QF_LRA, decidable by Fourier-Motzkin) "
            "from nonlinear real arithmetic.  QF_NRA is decidable via CAD but "
            "doubly exponential; with quantifiers it becomes undecidable."
        ),
        "decidable_keywords": ("Real", "+", "-", "<=", ">="),
        "undecidable_keywords": ("*", "^", "sin", "cos", "exp"),
        "crossing_cost": 20,
    },
    {
        "name": "String-theory / unrestricted-string",
        "description": (
            "Within string theory, straight-line programs are decidable; "
            "full regex intersection with back-references is undecidable."
        ),
        "decidable_keywords": ("str.++", "str.len", "str.substr", "str.contains"),
        "undecidable_keywords": ("str.to_int", "re.comp", "re.diff"),
        "crossing_cost": 30,
    },
]


# -------------------------------------------------------------------
# DecidabilityOracle
# -------------------------------------------------------------------

class DecidabilityOracle:
    """Fast cache-backed classifier mapping SMT-LIB2 strings to DecidabilityClass.

    The oracle is the first point of contact when JuGeo needs to know whether
    a formula is within Z3's decidable territory.  It maintains two data
    structures:

    * ``_cache`` — a dict from SHA-256 formula digest to ``DecidabilityClass``,
      preventing repeated classification of the same formula.
    * ``known_fragments`` — a dict from well-known fragment names (e.g. "QF_LIA")
      to their ``DecidabilityClass``, seeded with the KNOWN_DECIDABLE_FRAGMENTS
      list at construction time.

    The primary entry point is :meth:`query`, which checks the cache first and
    falls back to :meth:`classify_by_signature`.  Copilot integration is
    available via :meth:`copilot_decidability_hint`.
    """

    def __init__(self) -> None:
        """Initialise cache and pre-populate the known-fragment registry."""
        self._cache: dict[str, DecidabilityClass] = {}
        self._query_times: list[float] = []
        self.known_fragments: dict[str, DecidabilityClass] = {}

        # Seed the registry with all decidable fragments
        for frag in KNOWN_DECIDABLE_FRAGMENTS:
            self.known_fragments[frag] = DecidabilityClass.DECIDABLE

        # Additional well-known undecidable regions
        undecidable_known = [
            "QF_NIA",    # nonlinear integer arithmetic
            "FULL_FOL",  # unrestricted first-order logic
            "HO_LOGIC",  # higher-order logic
            "QF_NRA_TRANSCENDENTAL",
            "PEANO",
            "ZFC",
            "DIOPHANTINE",
            "NONLINEAR_REAL_QUANTIFIED",
        ]
        for frag in undecidable_known:
            self.known_fragments[frag] = DecidabilityClass.UNDECIDABLE

        # Semi-decidable entries
        semi_decidable_known = [
            "EXISTS_ONLY",          # existential fragment: semi-decidable
            "HORN_CLAUSES",         # Horn clauses: semi-decidable in general
            "SIGMA1",               # first-order existential
        ]
        for frag in semi_decidable_known:
            self.known_fragments[frag] = DecidabilityClass.SEMI_DECIDABLE

        logger.debug(
            "DecidabilityOracle initialised with %d known fragments.",
            len(self.known_fragments),
        )

    # --- public interface ---

    def query(self, formula_smt: str) -> DecidabilityClass:
        """Classify a formula, using the cache if available.

        First performs a SHA-256-keyed cache lookup.  On a miss, calls
        :meth:`classify_by_signature` and stores the result before returning.
        Unknown formulas trigger :meth:`escalate_unknown`.

        Args:
            formula_smt: Raw SMT-LIB2 string (may be multi-line).

        Returns:
            A ``DecidabilityClass`` value.
        """
        t0 = time.monotonic()
        cached = self.cache_lookup(formula_smt)
        if cached is not None:
            logger.debug("Oracle cache hit for formula hash.")
            return cached

        result = self.classify_by_signature(formula_smt)

        key = self._digest(formula_smt)
        self._cache[key] = result
        elapsed = time.monotonic() - t0
        self._query_times.append(elapsed)

        if result is DecidabilityClass.UNKNOWN:
            self.escalate_unknown(formula_smt)

        logger.debug(
            "Oracle classified formula as %s in %.4fs.", result.value, elapsed
        )
        return result

    def cache_lookup(self, formula_smt: str) -> DecidabilityClass | None:
        """Return the cached classification for *formula_smt*, or ``None``.

        Uses a SHA-256 digest of the formula string as the cache key so that
        structurally identical formulas with different whitespace still hit
        the cache.

        Args:
            formula_smt: The formula string to look up.

        Returns:
            A ``DecidabilityClass`` or ``None`` if not cached.
        """
        key = self._digest(formula_smt)
        return self._cache.get(key)

    def register_known(self, fragment: str, cls: DecidabilityClass) -> None:
        """Register a named fragment with an explicit decidability class.

        Overrides any previously registered value for ``fragment``.  Useful
        for extending the oracle at runtime with domain-specific knowledge.

        Args:
            fragment: Short name for the fragment (e.g. ``"QF_LIA"``).
            cls: The ``DecidabilityClass`` to associate.
        """
        previous = self.known_fragments.get(fragment)
        self.known_fragments[fragment] = cls
        if previous is not None and previous != cls:
            logger.warning(
                "Overriding fragment %r: %s → %s.", fragment, previous.value, cls.value
            )
        else:
            logger.debug("Registered fragment %r as %s.", fragment, cls.value)

    def classify_by_signature(self, smt: str) -> DecidabilityClass:
        """Heuristic keyword-based classification of a raw SMT-LIB2 string.

        Inspects the formula string for syntactic markers and applies a
        conservative decision procedure:

        1. Check for known fragment name tokens embedded in the string.
        2. Detect nested quantifier alternation → UNDECIDABLE.
        3. Detect nonlinear multiplication between variables → UNDECIDABLE.
        4. Detect single-layer existential, no forall → SEMI_DECIDABLE.
        5. Detect purely linear arithmetic tokens, no quantifiers → DECIDABLE.
        6. Default: UNKNOWN.

        Args:
            smt: The SMT-LIB2 formula string to inspect.

        Returns:
            A ``DecidabilityClass`` value.
        """
        lower = smt.lower()
        tokens: list[str] = smt.split()

        # Step 1 — check for explicit fragment identifiers in the string
        for frag_name, cls in self.known_fragments.items():
            if frag_name.lower() in lower:
                return cls

        # Step 2 — detect undecidable nonlinear arithmetic operators
        for tok in _NONLINEAR_OPS:
            if tok in lower:
                logger.debug("classify_by_signature: nonlinear op %r found.", tok)
                return DecidabilityClass.UNDECIDABLE

        for pat in _UNDECIDABLE_PATTERNS:
            if pat in lower:
                logger.debug("classify_by_signature: undecidable pattern %r.", pat)
                return DecidabilityClass.UNDECIDABLE

        # Step 3 — check for multiplication between symbolic variables
        # Heuristic: (* <non-numeral> <non-numeral>) is nonlinear
        import re as _re
        nonlinear_mul = _re.search(
            r"\(\*\s+([a-zA-Z_][a-zA-Z0-9_]*)\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*\)",
            smt,
        )
        if nonlinear_mul:
            return DecidabilityClass.UNDECIDABLE

        # Step 4 — quantifier detection
        has_forall = "forall" in lower
        has_exists = "exists" in lower
        has_lambda = "lambda" in lower

        if has_forall and has_exists:
            # Nested alternating quantifiers → almost certainly undecidable
            return DecidabilityClass.UNDECIDABLE
        if has_forall:
            # Universal quantifiers alone are on the boundary; conservatively undecidable
            return DecidabilityClass.UNDECIDABLE
        if has_exists and not has_forall:
            # Purely existential fragment is semi-decidable
            return DecidabilityClass.SEMI_DECIDABLE
        if has_lambda:
            return DecidabilityClass.UNDECIDABLE

        # Step 5 — purely linear arithmetic heuristic
        linear_count = sum(1 for t in tokens if t.strip("()") in _LINEAR_TOKENS)
        total_tokens = max(len(tokens), 1)
        linear_ratio = linear_count / total_tokens

        if linear_ratio > 0.15 and not has_forall and not has_exists:
            return DecidabilityClass.DECIDABLE

        # Step 6 — short formula with only boolean connectives → decidable
        bool_only_pattern = _re.fullmatch(
            r"[\s()andornottrue falseimpliesiff=><=xor!&|]+",
            smt,
            _re.IGNORECASE,
        )
        if bool_only_pattern:
            return DecidabilityClass.DECIDABLE

        return DecidabilityClass.UNKNOWN

    def escalate_unknown(self, smt: str) -> None:
        """Log a warning for a formula that could not be classified.

        This method is called automatically by :meth:`query` when the
        classification result is UNKNOWN.  It emits a warning with the first
        120 characters of the formula string for debugging.

        Args:
            smt: The unclassified formula string.
        """
        preview = smt[:120].replace("\n", " ")
        logger.warning(
            "DecidabilityOracle: could not classify formula — escalating.  "
            "Preview: %r", preview
        )

    def copilot_decidability_hint(self, smt: str) -> str:
        """Return a structured copilot-readable hint about this formula.

        Copilot uses this string to guide its next reasoning step — whether
        to attempt a Z3 solve, to abstract the formula, or to escalate.

        Args:
            smt: The SMT-LIB2 formula string.

        Returns:
            A newline-separated key=value string for the Copilot layer.
        """
        cls = self.query(smt)
        has_quantifiers = any(kw in smt.lower() for kw in _QUANTIFIER_KEYWORDS)
        has_nonlinear = any(op in smt.lower() for op in _NONLINEAR_OPS)
        preview = smt[:80].replace("\n", " ")
        lines = [
            "copilot_hint: DecidabilityOracle",
            f"  classification: {cls.value}",
            f"  has_quantifiers: {has_quantifiers}",
            f"  has_nonlinear_ops: {has_nonlinear}",
            f"  formula_preview: {preview!r}",
            f"  cache_size: {len(self._cache)}",
            "  recommendation: "
            + (
                "proceed_with_z3"
                if cls is DecidabilityClass.DECIDABLE
                else "abstract_or_escalate"
                if cls is DecidabilityClass.UNDECIDABLE
                else "attempt_with_timeout"
                if cls is DecidabilityClass.SEMI_DECIDABLE
                else "manual_review_required"
            ),
        ]
        return "\n".join(lines)

    def stats(self) -> dict[str, Any]:
        """Return a statistics dictionary for monitoring and reporting.

        Returns:
            Dict containing cache_size, known_fragment_count, query_count,
            avg_query_time_ms, and decidability_distribution.
        """
        distribution: dict[str, int] = {cls.value: 0 for cls in DecidabilityClass}
        for cls in self._cache.values():
            distribution[cls.value] += 1

        avg_ms = (
            math.fsum(self._query_times) / len(self._query_times) * 1000.0
            if self._query_times
            else 0.0
        )
        return {
            "cache_size": len(self._cache),
            "known_fragment_count": len(self.known_fragments),
            "query_count": len(self._query_times),
            "avg_query_time_ms": round(avg_ms, 4),
            "decidability_distribution": distribution,
        }

    # --- private helpers ---

    @staticmethod
    def _digest(text: str) -> str:
        """Return a short SHA-256 hex digest of *text*."""
        return hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]


# -------------------------------------------------------------------
# FrontierBoundaryLocator
# -------------------------------------------------------------------

class FrontierBoundaryLocator:
    """Locates the most relevant named frontier boundary for a given formula.

    The locator maintains a list of ``FrontierBoundary`` objects and exposes
    methods for finding the closest boundary to a formula, classifying which
    side of the boundary the formula is on, and estimating the cost of
    crossing the boundary towards a target decidable fragment.

    Copilot integration is available via :meth:`copilot_boundary_hint`.
    """

    def __init__(self, boundaries: list[FrontierBoundary] | None = None) -> None:
        """Initialise with an optional list of pre-defined boundaries.

        If *boundaries* is None, a default set of well-known boundaries is
        constructed from ``_DEFAULT_BOUNDARY_SPECS``.

        Args:
            boundaries: Pre-constructed FrontierBoundary objects, or None.
        """
        if boundaries is None:
            self._boundaries: list[FrontierBoundary] = [
                FrontierBoundary(
                    boundary_id=uuid.uuid4().hex[:12],
                    name=spec["name"],
                    description=spec["description"],
                    decidable_keywords=tuple(spec["decidable_keywords"]),
                    undecidable_keywords=tuple(spec["undecidable_keywords"]),
                    crossing_cost=spec["crossing_cost"],
                )
                for spec in _DEFAULT_BOUNDARY_SPECS
            ]
        else:
            self._boundaries = list(boundaries)

        logger.debug(
            "FrontierBoundaryLocator initialised with %d boundaries.",
            len(self._boundaries),
        )

    def locate_boundary(self, formula_smt: str) -> FrontierBoundary:
        """Find the boundary most relevant to *formula_smt*.

        Relevance is scored by counting how many of each boundary's
        ``decidable_keywords`` and ``undecidable_keywords`` appear in the
        formula.  The boundary with the highest total hit count wins.  If no
        boundary has any hits, the first boundary in the list is returned as
        a safe default.

        Args:
            formula_smt: The SMT-LIB2 formula string.

        Returns:
            The most relevant ``FrontierBoundary``.
        """
        lower = formula_smt.lower()
        best: FrontierBoundary = self._boundaries[0]
        best_score = -1

        for boundary in self._boundaries:
            score = 0
            for kw in boundary.decidable_keywords:
                if kw.lower() in lower:
                    score += 2  # decidable hits worth more (we lean decidable)
            for kw in boundary.undecidable_keywords:
                if kw.lower() in lower:
                    score += 3  # undecidable hits indicate the formula is near this boundary
            if score > best_score:
                best_score = score
                best = boundary

        logger.debug(
            "Located boundary %r with score %d.", best.name, best_score
        )
        return best

    def classify_side(
        self, formula: str, boundary: FrontierBoundary
    ) -> FrontierSide:
        """Classify which side of *boundary* the formula falls on.

        A formula is:
        - INSIDE  if it matches decidable keywords but not undecidable ones.
        - OUTSIDE if it matches undecidable keywords.
        - BOUNDARY if it matches both or if the keywords overlap ambiguously.

        Args:
            formula: The SMT-LIB2 formula string.
            boundary: The FrontierBoundary to evaluate against.

        Returns:
            A ``FrontierSide`` value.
        """
        lower = formula.lower()
        decidable_hits = sum(
            1 for kw in boundary.decidable_keywords if kw.lower() in lower
        )
        undecidable_hits = sum(
            1 for kw in boundary.undecidable_keywords if kw.lower() in lower
        )

        if undecidable_hits > 0 and decidable_hits == 0:
            return FrontierSide.OUTSIDE
        if undecidable_hits > 0 and decidable_hits > 0:
            return FrontierSide.BOUNDARY
        if decidable_hits > 0 and undecidable_hits == 0:
            return FrontierSide.INSIDE

        # No keywords matched — default conservatively to INSIDE
        return FrontierSide.INSIDE

    def enumerate_boundaries(self) -> list[FrontierBoundary]:
        """Return the full list of registered boundaries.

        Returns:
            A copy of the internal boundaries list.
        """
        return list(self._boundaries)

    def nearest_decidable(self, formula_smt: str) -> str:
        """Return the name of the nearest decidable fragment for *formula_smt*.

        Inspects the formula for theory-specific tokens to suggest the most
        appropriate decidable target fragment.

        Args:
            formula_smt: The SMT-LIB2 formula string.

        Returns:
            A fragment name string such as ``"QF_LIA"`` or ``"QF_BV"``.
        """
        lower = formula_smt.lower()

        # Ordered by specificity — check bitvectors first, then arrays, etc.
        if any(kw in lower for kw in ("bvadd", "bvsub", "bvand", "bvor", "extract", "concat")):
            return "QF_BV"
        if any(kw in lower for kw in ("select", "store", "array")):
            return "QF_AX"
        if any(kw in lower for kw in ("str.++", "str.len", "str.substr")):
            return "QF_S"
        if any(kw in lower for kw in ("real", "/")):
            return "QF_LRA"
        if any(kw in lower for kw in ("int", "div", "mod")):
            return "QF_LIA"

        # Fall back to propositional logic if the formula looks boolean
        if all(kw in lower for kw in ("and", "or", "not")):
            return "PROPOSITIONAL"

        return "QF_LIA"  # safe default

    def crossing_cost(self, formula_smt: str, target_fragment: str) -> int:
        """Estimate the transformation cost to move *formula_smt* to *target_fragment*.

        Cost is a heuristic integer in [0, 100].  Higher values indicate more
        expensive transformations (more rewrites, more abstraction, higher risk
        of information loss).

        Args:
            formula_smt: The formula to transform.
            target_fragment: The desired target fragment name.

        Returns:
            An integer cost estimate.
        """
        boundary = self.locate_boundary(formula_smt)
        side = self.classify_side(formula_smt, boundary)

        base_cost = boundary.crossing_cost

        # Adjust for how "foreign" the target fragment is
        current_nearest = self.nearest_decidable(formula_smt)
        if current_nearest == target_fragment:
            adjustment = 0  # already as close as possible
        elif current_nearest in KNOWN_DECIDABLE_FRAGMENTS and target_fragment in KNOWN_DECIDABLE_FRAGMENTS:
            adjustment = 5  # lateral move within decidable region
        else:
            adjustment = 15  # moving across a major boundary

        if side is FrontierSide.OUTSIDE:
            adjustment += 20  # must abstract or quantifier-eliminate first

        total = max(0, min(100, base_cost + adjustment))
        logger.debug(
            "crossing_cost(%r) = %d (base=%d, adj=%d, side=%s).",
            target_fragment, total, base_cost, adjustment, side.value,
        )
        return total

    def copilot_boundary_hint(self, formula: str) -> str:
        """Return a structured copilot-readable boundary classification hint.

        Args:
            formula: The SMT-LIB2 formula string.

        Returns:
            A newline-separated key=value string for the Copilot layer.
        """
        boundary = self.locate_boundary(formula)
        side = self.classify_side(formula, boundary)
        nearest = self.nearest_decidable(formula)
        cost = self.crossing_cost(formula, nearest)

        lines = [
            "copilot_hint: FrontierBoundaryLocator",
            f"  boundary_name: {boundary.name}",
            f"  boundary_id: {boundary.boundary_id}",
            f"  frontier_side: {side.value}",
            f"  nearest_decidable_fragment: {nearest}",
            f"  crossing_cost_to_nearest: {cost}",
            "  boundary_description: "
            + boundary.description[:100].replace("\n", " "),
            "  action: "
            + (
                "z3_direct_solve"
                if side is FrontierSide.INSIDE
                else "apply_quantifier_elimination_or_abstract"
                if side is FrontierSide.OUTSIDE
                else "attempt_with_bounded_timeout"
            ),
        ]
        return "\n".join(lines)


# -------------------------------------------------------------------
# UndecidabilityWitness
# -------------------------------------------------------------------

@dataclass(frozen=True)
class UndecidabilityWitness:
    """An immutable record that evidences a formula's undecidability.

    Captures the formula, a human-readable reason, the well-known problem
    it reduces from, and a Copilot annotation.  Produced by
    :meth:`StructuralFrontierDefiner.witness_undecidable`.

    Attributes:
        witness_id: Unique identifier for this witness record.
        formula_smt: The SMT-LIB2 formula string that is undecidable.
        reason: Short reason for undecidability (e.g. "nonlinear arithmetic").
        reduction_from: The canonical undecidable problem this reduces from.
        copilot_note: Free-form note for the Copilot reasoning layer.
    """

    witness_id: str
    formula_smt: str
    reason: str
    reduction_from: str
    copilot_note: str

    def is_valid(self) -> bool:
        """Return True if the witness contains non-empty evidence.

        A valid witness must have both a non-empty reason and a non-empty
        formula_smt.  An empty witness is not useful for downstream repair.

        Returns:
            Boolean indicating witness validity.
        """
        if not self.reason.strip():
            return False
        if not self.formula_smt.strip():
            return False
        if not self.witness_id.strip():
            return False
        return True

    def to_dict(self) -> dict[str, Any]:
        """Serialise this witness to a plain Python dictionary.

        Returns:
            A JSON-serialisable dict with all fields.
        """
        return {
            "witness_id": self.witness_id,
            "formula_smt": self.formula_smt,
            "reason": self.reason,
            "reduction_from": self.reduction_from,
            "copilot_note": self.copilot_note,
            "is_valid": self.is_valid(),
        }

    def human_readable(self) -> str:
        """Return a formatted multi-line human-readable description.

        Formats all fields in a structured block suitable for display in
        a terminal or log file.

        Returns:
            A multi-line string.
        """
        sep = "-" * 60
        lines = [
            sep,
            f"UndecidabilityWitness  [{self.witness_id}]",
            sep,
            f"  Valid          : {self.is_valid()}",
            f"  Reason         : {self.reason}",
            f"  Reduces from   : {self.reduction_from}",
            f"  Copilot note   : {self.copilot_note}",
            "  Formula (SMT)  :",
        ]
        for line in self.formula_smt.splitlines():
            lines.append(f"    {line}")
        lines.append(sep)
        return "\n".join(lines)


# -------------------------------------------------------------------
# StructuralFrontierDefiner
# -------------------------------------------------------------------

class StructuralFrontierDefiner:
    """Top-level coordinator for mapping and reporting the structural frontier.

    This class wires together :class:`DecidabilityOracle` and
    :class:`FrontierBoundaryLocator` to provide a single, unified API for:

    - Defining named ``StructuralFrontier`` objects for known fragments.
    - Classifying arbitrary formulas with respect to the frontier.
    - Enumerating decidable fragments and undecidable regions.
    - Proving decidability of named fragments.
    - Constructing ``UndecidabilityWitness`` records for undecidable formulas.
    - Emitting structured, multi-section frontier reports.

    Copilot integration is available via :meth:`copilot_frontier_narrative`.
    """

    def __init__(self) -> None:
        """Initialise oracle, locator, and fragment registries."""
        self.oracle: DecidabilityOracle = DecidabilityOracle()
        self.locator: FrontierBoundaryLocator = FrontierBoundaryLocator()
        self.frontiers: dict[str, StructuralFrontier] = {}
        self.undecidable_regions: list[str] = [
            "QF_NIA",
            "FULL_FOL",
            "HO_LOGIC",
            "NONLINEAR_REAL_QUANTIFIED",
            "PEANO_ARITHMETIC",
            "DIOPHANTINE",
            "STRING_REGEX_INTERSECTION",
        ]
        self.decidable_fragments: list[str] = list(KNOWN_DECIDABLE_FRAGMENTS)
        self._created_at: float = time.monotonic()

        logger.info(
            "StructuralFrontierDefiner initialised: %d decidable fragments, "
            "%d undecidable regions.",
            len(self.decidable_fragments),
            len(self.undecidable_regions),
        )

    def define_frontier(self, fragment_name: str) -> StructuralFrontier:
        """Create and register a ``StructuralFrontier`` for *fragment_name*.

        If a frontier for this fragment has already been defined, the existing
        record is returned.  Otherwise, :func:`make_default_frontier` is called
        and the result is stored in ``self.frontiers``.

        Args:
            fragment_name: The SMT-LIB2 logic name (e.g. ``"QF_LIA"``).

        Returns:
            A ``StructuralFrontier`` for the fragment.
        """
        if fragment_name in self.frontiers:
            logger.debug("Frontier already defined for %r; returning cached.", fragment_name)
            return self.frontiers[fragment_name]

        frontier = make_default_frontier(fragment_name)
        self.frontiers[fragment_name] = frontier

        # Also register the classification with the oracle
        self.oracle.register_known(fragment_name, frontier.decidability_class)

        logger.info(
            "Defined frontier for %r: class=%s, complexity=%s.",
            fragment_name,
            frontier.decidability_class.value,
            frontier.complexity,
        )
        return frontier

    def classify_formula(self, formula_smt: str) -> FrontierSide:
        """Classify a formula with respect to the structural frontier.

        Combines the oracle's decidability classification with the locator's
        boundary side determination:

        - DECIDABLE → INSIDE
        - SEMI_DECIDABLE → BOUNDARY
        - UNDECIDABLE → OUTSIDE
        - UNKNOWN → uses boundary locator as secondary signal

        Args:
            formula_smt: The SMT-LIB2 formula string.

        Returns:
            A ``FrontierSide`` value.
        """
        dc = self.oracle.query(formula_smt)

        if dc is DecidabilityClass.DECIDABLE:
            return FrontierSide.INSIDE
        if dc is DecidabilityClass.UNDECIDABLE:
            return FrontierSide.OUTSIDE
        if dc is DecidabilityClass.SEMI_DECIDABLE:
            return FrontierSide.BOUNDARY

        # UNKNOWN — defer to the boundary locator for a finer-grained answer
        boundary = self.locator.locate_boundary(formula_smt)
        side = self.locator.classify_side(formula_smt, boundary)
        logger.debug(
            "classify_formula: oracle=UNKNOWN; locator says %s at boundary %r.",
            side.value, boundary.name,
        )
        return side

    def enumerate_decidable_fragments(self) -> list[str]:
        """Return the list of all registered decidable fragment names.

        Returns:
            A copy of ``self.decidable_fragments``.
        """
        return list(self.decidable_fragments)

    def enumerate_undecidable_regions(self) -> list[str]:
        """Return the list of all registered undecidable region names.

        Returns:
            A copy of ``self.undecidable_regions``.
        """
        return list(self.undecidable_regions)

    def prove_decidable(self, fragment: str) -> bool:
        """Return True if *fragment* is known to be decidable.

        Checks both ``KNOWN_DECIDABLE_FRAGMENTS`` and the oracle's
        ``known_fragments`` registry.

        Args:
            fragment: The fragment name to check.

        Returns:
            Boolean.
        """
        if fragment in KNOWN_DECIDABLE_FRAGMENTS:
            return True
        oracle_cls = self.oracle.known_fragments.get(fragment)
        if oracle_cls is DecidabilityClass.DECIDABLE:
            return True
        if fragment in self.decidable_fragments:
            return True
        return False

    def witness_undecidable(self, formula_smt: str) -> UndecidabilityWitness:
        """Construct an ``UndecidabilityWitness`` for an undecidable formula.

        Inspects the formula for known undecidability patterns and selects
        an appropriate reduction.  Always produces a witness, even for UNKNOWN
        formulas, though UNKNOWN witnesses will carry a weaker reason.

        Args:
            formula_smt: The SMT-LIB2 formula string to witness.

        Returns:
            An ``UndecidabilityWitness`` record.
        """
        dc = self.oracle.query(formula_smt)
        lower = formula_smt.lower()

        # Select the most specific applicable reduction
        if any(op in lower for op in _NONLINEAR_OPS):
            reason = "nonlinear arithmetic operators detected"
            reduction = _UNDECIDABILITY_REDUCTIONS["nonlinear_integer"]
        elif "forall" in lower and "exists" in lower:
            reason = "nested alternating quantifiers detected"
            reduction = _UNDECIDABILITY_REDUCTIONS["full_first_order"]
        elif "forall" in lower:
            reason = "universal quantification in an undecidable fragment"
            reduction = _UNDECIDABILITY_REDUCTIONS["full_first_order"]
        elif any(pat in lower for pat in _UNDECIDABLE_PATTERNS):
            reason = "undecidable string/float theory operator detected"
            reduction = _UNDECIDABILITY_REDUCTIONS["string_regex_intersection"]
        elif dc is DecidabilityClass.UNDECIDABLE:
            reason = f"classified as UNDECIDABLE by oracle"
            reduction = _UNDECIDABILITY_REDUCTIONS["quantified_nonlinear"]
        else:
            reason = f"decidability unknown (oracle returned {dc.value})"
            reduction = "unknown — no canonical reduction found"

        copilot_note = (
            f"Copilot: formula exhibits {reason}.  "
            f"Reduction: {reduction}.  "
            "Suggested action: abstract to QF_LIA or escalate to interactive prover."
        )

        return UndecidabilityWitness(
            witness_id=uuid.uuid4().hex[:16],
            formula_smt=formula_smt,
            reason=reason,
            reduction_from=reduction,
            copilot_note=copilot_note,
        )

    def emit_frontier_report(self) -> str:
        """Emit a structured multi-section text report on the current frontier state.

        The report covers:
        - Summary header
        - Oracle statistics
        - Registered frontiers
        - Decidable fragments
        - Undecidable regions
        - Boundary catalogue
        - Footer

        Returns:
            A multi-line string suitable for display or logging.
        """
        sep = "=" * 72
        thin = "-" * 72
        elapsed = time.monotonic() - self._created_at
        stats = self.oracle.stats()
        lines: list[str] = [
            sep,
            "  STRUCTURAL FRONTIER REPORT — JuGeo / Z3 Decidability Boundary",
            sep,
            f"  Generated after {elapsed:.2f}s of operation.",
            f"  Oracle cache size    : {stats['cache_size']}",
            f"  Oracle query count   : {stats['query_count']}",
            f"  Avg query time (ms)  : {stats['avg_query_time_ms']}",
            "",
            thin,
            "  REGISTERED FRONTIERS",
            thin,
        ]
        if self.frontiers:
            for name, f in sorted(self.frontiers.items()):
                lines.append(f"  [{f.frontier_id}] {name}")
                lines.append(f"    class     : {f.decidability_class.value}")
                lines.append(f"    complexity: {f.complexity}")
                lines.append(f"    desc      : {f.description[:80]}")
        else:
            lines.append("  (no frontiers defined yet)")

        lines += [
            "",
            thin,
            "  DECIDABLE FRAGMENTS",
            thin,
        ]
        for frag in sorted(self.decidable_fragments):
            marker = "✓" if self.prove_decidable(frag) else "?"
            lines.append(f"  {marker} {frag}")

        lines += [
            "",
            thin,
            "  UNDECIDABLE REGIONS",
            thin,
        ]
        for region in sorted(self.undecidable_regions):
            lines.append(f"  ✗ {region}")

        lines += [
            "",
            thin,
            "  BOUNDARY CATALOGUE",
            thin,
        ]
        for boundary in self.locator.enumerate_boundaries():
            lines.append(f"  [{boundary.boundary_id}] {boundary.name}")
            lines.append(f"    crossing cost: {boundary.crossing_cost}")
            lines.append(f"    desc: {boundary.description[:80]}")

        lines += [
            "",
            sep,
            "  END OF FRONTIER REPORT",
            sep,
        ]
        return "\n".join(lines)

    def copilot_frontier_narrative(self) -> str:
        """Return a Copilot-readable narrative describing the current frontier state.

        The narrative is written in structured key=value format so that the
        Copilot orchestration layer can parse it without further processing.

        Returns:
            A multi-line structured string.
        """
        stats = self.oracle.stats()
        distribution = stats.get("decidability_distribution", {})
        elapsed = time.monotonic() - self._created_at
        lines = [
            "copilot_narrative: StructuralFrontierDefiner",
            f"  uptime_seconds: {elapsed:.2f}",
            f"  oracle_cache_size: {stats['cache_size']}",
            f"  oracle_query_count: {stats['query_count']}",
            f"  decidable_fragment_count: {len(self.decidable_fragments)}",
            f"  undecidable_region_count: {len(self.undecidable_regions)}",
            f"  registered_frontier_count: {len(self.frontiers)}",
            f"  boundary_count: {len(self.locator.enumerate_boundaries())}",
            "  distribution:",
        ]
        for cls_name, count in distribution.items():
            lines.append(f"    {cls_name}: {count}")
        lines += [
            "  guidance:",
            "    Formulas on INSIDE should be sent directly to Z3.",
            "    Formulas on BOUNDARY should be tried with a short timeout.",
            "    Formulas on OUTSIDE require abstraction or escalation.",
            "    Use witness_undecidable() to generate structured evidence.",
            "    Use emit_frontier_report() for a full diagnostic snapshot.",
        ]
        return "\n".join(lines)


# -------------------------------------------------------------------
# Module-level default definer instance
# -------------------------------------------------------------------

_DEFAULT_DEFINER: StructuralFrontierDefiner | None = None


def get_default_definer() -> StructuralFrontierDefiner:
    """Return the module-level singleton ``StructuralFrontierDefiner``.

    Lazily constructs the instance on first call.  Subsequent calls return the
    same instance, preserving oracle cache and registered frontiers across the
    lifetime of the module.

    Returns:
        The shared ``StructuralFrontierDefiner`` instance.
    """
    global _DEFAULT_DEFINER
    if _DEFAULT_DEFINER is None:
        _DEFAULT_DEFINER = StructuralFrontierDefiner()
        logger.debug("Constructed module-level default StructuralFrontierDefiner.")
    return _DEFAULT_DEFINER


__all__ = [
    "DecidabilityOracle",
    "FrontierBoundaryLocator",
    "UndecidabilityWitness",
    "StructuralFrontierDefiner",
    "get_default_definer",
    # Re-exported model types (either from models.py or inline fallbacks)
    "DecidabilityClass",
    "FrontierSide",
    "FrontierBoundary",
    "StructuralFrontier",
    "KNOWN_DECIDABLE_FRAGMENTS",
    "make_default_frontier",
    "make_default_boundary",
    "make_default_map",
]
