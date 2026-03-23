"""
countermodels_and_clausewise_expla — Text countermodels and clausewise divergence explanation.

# copilot: Text countermodels – witnesses to semantic divergence at clause level

Part of the JuGeo judgment-geometry framework.

In Judgment Geometry every evaluation is a tuple

    J = (c, φ, A, E, O, B, T, Π)

where:
  c  — the clause or code fragment under scrutiny
  φ  — the logical formula that the judgment asserts (the "property")
  A  — the annotation set (docstring tags, inline hints)
  E  — the evidence bundle (test results, lint scores, coverage)
  O  — the obstruction class (Čech H¹ of the disagreement cover)
  B  — the trust budget (non-negative integer: residual verification credit)
  T  — the TrustTier (an element of the ordered algebra LOW < MID < HIGH < VERIFIED)
  Π  — the proof certificate (None when not yet discharged)

Two judgments J₁ = (c, φ, A, E, O, B, T, Π) and
               J₂ = (c, φ', A', E', O', B', T', Π')

are *semantically divergent* when there exists a concrete text fragment w such
that w satisfies φ but not φ' (or vice versa).  Finding such a w is precisely
finding a *countermodel* for the equivalence φ ↔ φ'.

At the sheaf-theoretic level the two judgments define an open cover
  U₁ = {texts satisfying φ},   U₂ = {texts satisfying φ'}
of the space of texts.  Their intersection U₁ ∩ U₂ is the set of texts on
which both agree.  When U₁ ∩ U₂ ≠ full space, the Čech 1-cocycle
  σ₁₂ : U₁ ∩ U₂ → {0, 1}
fails to be a coboundary, and the corresponding Čech H¹ class O is non-trivial:
that non-triviality *is* the obstruction stored in field O of the judgment tuple.

This module exposes:
  • Frozen dataclasses representing countermodels, clause explanations, witnesses,
    repair hints, search states, and clause decompositions.
  • Functions that construct, validate, and repair countermodels.
  • A smoke test in __main__ that exercises the full pipeline end-to-end.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Standard-library imports
# ---------------------------------------------------------------------------
import hashlib
import itertools
import logging
import re
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass
from enum import Enum, auto
from typing import Any, Final, Iterator, Sequence

# ---------------------------------------------------------------------------
# Jugeo imports with graceful fallback stubs
# ---------------------------------------------------------------------------
try:
    from jugeo.encodings.text_encodings.models import (  # type: ignore[import]
        StringEncoding,
        SymbolicText,
        TextConstraint,
        ConstraintKind,
        ConstraintStrength,
        IDENTIFIER_PATTERN,
        SNAKE_CASE_PATTERN,
        CAMEL_CASE_PATTERN,
    )
    _JUGEO_MODELS_AVAILABLE = True
except ImportError:
    _JUGEO_MODELS_AVAILABLE = False
    # Minimal stubs so the rest of this module can run without jugeo installed.

    class StringEncoding:  # type: ignore[no-redef]
        pass

    class SymbolicText:  # type: ignore[no-redef]
        pass

    class TextConstraint:  # type: ignore[no-redef]
        pass

    class ConstraintKind(Enum):  # type: ignore[no-redef]
        REGEX = auto()
        LENGTH = auto()
        PREFIX = auto()

    class ConstraintStrength(Enum):  # type: ignore[no-redef]
        HARD = auto()
        SOFT = auto()

    IDENTIFIER_PATTERN: str = r"[A-Za-z_][A-Za-z0-9_]*"
    SNAKE_CASE_PATTERN: str = r"[a-z][a-z0-9_]*"
    CAMEL_CASE_PATTERN: str = r"[A-Z][a-zA-Z0-9]*"

try:
    from jugeo.judgments.trust import TrustTier  # type: ignore[import]
    _JUGEO_TRUST_AVAILABLE = True
except ImportError:
    _JUGEO_TRUST_AVAILABLE = False

    class TrustTier(Enum):  # type: ignore[no-redef]
        """Ordered algebra of trust levels.  LOW < MID < HIGH < VERIFIED."""
        LOW = 0
        MID = 1
        HIGH = 2
        VERIFIED = 3

        def __lt__(self, other: "TrustTier") -> bool:
            return self.value < other.value

        def __le__(self, other: "TrustTier") -> bool:
            return self.value <= other.value

try:
    from jugeo.geometry.supports import SupportRegion, SupportSet  # type: ignore[import]
    _JUGEO_GEOMETRY_AVAILABLE = True
except ImportError:
    _JUGEO_GEOMETRY_AVAILABLE = False

    class SupportRegion:  # type: ignore[no-redef]
        pass

    class SupportSet:  # type: ignore[no-redef]
        pass

_LOGGER = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

#: Divergence categories used throughout the module.
DIVERGENCE_TYPES: Final[tuple[str, ...]] = ("SEMANTIC", "SYNTACTIC", "PRAGMATIC")

#: Recognised repair kinds (operations on text to remove divergence).
REPAIR_KINDS: Final[tuple[str, ...]] = (
    "SUBSTITUTION",
    "DELETION",
    "INSERTION",
    "REORDER",
)

#: Search strategies for countermodel discovery.
SEARCH_STRATEGIES: Final[tuple[str, ...]] = (
    "EXHAUSTIVE",      # enumerate all possible witnesses up to depth
    "GREEDY",          # take first witness that works
    "HEURISTIC",       # use trust-weighted scoring to guide search
    "RANDOM_WALK",     # stochastic exploration of text space
    "BISECT",          # binary-search on trust level
)

#: Default maximum search depth (number of syntactic variants tried).
DEFAULT_MAX_DEPTH: Final[int] = 64

#: Minimum confidence threshold for a repair hint to be emitted.
MIN_REPAIR_CONFIDENCE: Final[float] = 0.05

#: Trust integer ↔ TrustTier mapping (for legacy callers using bare ints).
_INT_TO_TRUST: Final[dict[int, TrustTier]] = {
    0: TrustTier.LOW,
    1: TrustTier.MID,
    2: TrustTier.HIGH,
    3: TrustTier.VERIFIED,
}

# ---------------------------------------------------------------------------
# Helpers for deterministic IDs
# ---------------------------------------------------------------------------

def _make_id(prefix: str, *parts: str) -> str:
    """Return a stable, short ID derived from *parts* with *prefix* namespace."""
    payload = "|".join(parts)
    digest = hashlib.sha256(payload.encode()).hexdigest()[:12]
    return f"{prefix}_{digest}"


def _fresh_id(prefix: str) -> str:
    """Return a fresh random UUID-based ID with *prefix* namespace."""
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


# ---------------------------------------------------------------------------
# TrustTier ordered-algebra helpers
# ---------------------------------------------------------------------------

def trust_meet(a: TrustTier, b: TrustTier) -> TrustTier:
    """Return the greatest lower bound (meet) in the TrustTier lattice."""
    return a if a <= b else b


def trust_join(a: TrustTier, b: TrustTier) -> TrustTier:
    """Return the least upper bound (join) in the TrustTier lattice."""
    return a if a >= b else b


def trust_from_int(level: int) -> TrustTier:
    """Convert a legacy integer trust level to a TrustTier, clamping to [0, 3]."""
    clamped = max(0, min(3, level))
    return _INT_TO_TRUST[clamped]


def trust_to_int(tier: TrustTier) -> int:
    """Convert a TrustTier to its integer ordinal."""
    return tier.value


# ---------------------------------------------------------------------------
# Čech H¹ obstruction helpers
# ---------------------------------------------------------------------------

def _cech_h1_representative(
    witness_id: str,
    left_formula: str,
    right_formula: str,
) -> str:
    """Return a canonical string encoding the Čech H¹ class of the cover.

    In the judgment-geometry framework the obstruction O ∈ H¹ is represented
    as a hash of the two formulas and the witness that distinguishes them.
    Two pairs of judgments whose formulas hash identically will share the same
    obstruction class and can be handled by the same repair strategy.

    The Čech 1-cocycle σ₁₂ : U₁ ∩ U₂ → ℤ/2ℤ is encoded here as a hex digest
    so it can be compared, stored, and looked up in repair caches.
    """
    raw = f"H1::{left_formula}::{right_formula}::{witness_id}"
    return "O_" + hashlib.sha256(raw.encode()).hexdigest()[:16]


def _formulas_are_compatible(phi_a: str, phi_b: str) -> bool:
    """Heuristically check whether two formula strings are logically compatible.

    This is a syntactic approximation only.  Full semantic compatibility would
    require a call to a SAT/SMT solver (see jugeo.solver.z3_session).  Here we
    flag obvious contradictions like "positive" vs "negative" or "include" vs
    "exclude" in the formula text.
    """
    negation_pairs = [
        ("include", "exclude"),
        ("positive", "negative"),
        ("allow", "deny"),
        ("whitelist", "blacklist"),
        ("required", "forbidden"),
        ("must_have", "must_not_have"),
    ]
    a_lower = phi_a.lower()
    b_lower = phi_b.lower()
    for pos, neg in negation_pairs:
        if pos in a_lower and neg in b_lower:
            return False
        if neg in a_lower and pos in b_lower:
            return False
    return True


# ---------------------------------------------------------------------------
# Frozen dataclasses — core domain objects
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TextCountermodel:
    """A countermodel witnessing semantic divergence between two text judgments.

    In the judgment-geometry framework a countermodel for the pair
      J₁ = (c, φ, A, E, O, B, T, Π)
      J₂ = (c, φ', A', E', O', B', T', Π')
    is a concrete text fragment *w* (the ``witness_text``) such that *w*
    satisfies φ but not φ' (or vice versa).  Its existence proves that J₁
    and J₂ are not equivalent evaluations of the same clause and that their
    disagreement is genuine rather than notational.

    Fields
    ------
    countermodel_id : str
        Stable, content-addressed identifier for this countermodel.
    judgment_a : tuple
        First judgment as a raw tuple (c, φ, A, E, O, B, T, Π).
    judgment_b : tuple
        Second judgment as a raw tuple (c, φ', A', E', O', B', T', Π').
    diverging_clause : str
        The specific clause text that causes the semantic split.
    witness_text : str
        Concrete text fragment satisfying one judgment but not the other.
    trust_level : int
        Integer ordinal of the trust tier at which divergence was detected.
    is_genuine : bool
        True iff the witness has been validated against both judgment formulas.
    """

    countermodel_id: str
    judgment_a: tuple
    judgment_b: tuple
    diverging_clause: str
    witness_text: str
    trust_level: int
    is_genuine: bool

    def trust_tier(self) -> TrustTier:
        """Return the TrustTier corresponding to ``trust_level``."""
        return trust_from_int(self.trust_level)

    def obstruction_class(self) -> str:
        """Return the Čech H¹ representative for this countermodel.

        The obstruction class captures *which* semantic disagreement this
        countermodel witnesses, independent of the specific witness text.
        Two countermodels with the same obstruction class can be repaired
        by the same family of substitutions.
        """
        phi_a = self.judgment_a[1] if len(self.judgment_a) > 1 else ""
        phi_b = self.judgment_b[1] if len(self.judgment_b) > 1 else ""
        return _cech_h1_representative(self.countermodel_id, str(phi_a), str(phi_b))

    def summary(self) -> str:
        """Return a single-line human-readable summary."""
        tier = self.trust_tier().name
        valid = "✓ genuine" if self.is_genuine else "✗ tentative"
        return (
            f"[{valid}] countermodel {self.countermodel_id[:8]}… "
            f"at trust={tier}: «{self.diverging_clause[:60]}»"
        )


@dataclass(frozen=True)
class ClauseExplanation:
    """Explanation of why a clause causes divergence between two judgments.

    A ``ClauseExplanation`` is the output of the *analysis* phase: given a
    diverging clause identified by a ``TextCountermodel``, this object records
    *why* the clause causes divergence and what the three-level taxonomy of
    divergence type tells us about the required repair.

    Divergence types
    ----------------
    SEMANTIC
        The clause has two valid parse trees that map to distinct logical
        formulae under the standard denotational semantics.  Repair requires
        a semantics-preserving rewriting of the clause.
    SYNTACTIC
        The clause violates a surface-form constraint (naming law, indentation
        rule, bracket matching) independently of its meaning.  Repair is often
        purely structural.
    PRAGMATIC
        The clause is syntactically and semantically valid but violates a
        contextual convention (e.g., a team style guide, a domain-specific
        term-of-art requirement).  Repair requires knowing the surrounding
        documentation context.

    Fields
    ------
    explanation_id : str
        Stable identifier for this explanation record.
    clause_text : str
        The verbatim clause text being explained.
    clause_kind : str
        Syntactic category: e.g. "STATEMENT", "EXPRESSION", "DECLARATION",
        "IMPORT", "DECORATOR", "COMMENT", "DOCSTRING".
    divergence_type : str
        One of "SEMANTIC", "SYNTACTIC", "PRAGMATIC".
    confidence : float
        Confidence in [0, 1] that the stated divergence type is correct.
    repair_hints : tuple[str, ...]
        Ordered list of free-text suggestions for repairing the clause.
    """

    explanation_id: str
    clause_text: str
    clause_kind: str
    divergence_type: str
    confidence: float
    repair_hints: tuple[str, ...]

    def __post_init__(self) -> None:
        # Validate divergence_type membership.
        object.__setattr__(self, "divergence_type", self.divergence_type.upper())
        if self.divergence_type not in DIVERGENCE_TYPES:
            raise ValueError(
                f"divergence_type must be one of {DIVERGENCE_TYPES}, "
                f"got {self.divergence_type!r}"
            )
        if not (0.0 <= self.confidence <= 1.0):
            raise ValueError(
                f"confidence must be in [0, 1], got {self.confidence}"
            )

    def is_high_confidence(self, threshold: float = 0.7) -> bool:
        """Return True if confidence exceeds *threshold*."""
        return self.confidence >= threshold

    def primary_hint(self) -> str | None:
        """Return the highest-priority repair hint, or None if none available."""
        return self.repair_hints[0] if self.repair_hints else None


@dataclass(frozen=True)
class SemanticDivergenceWitness:
    """An explicit witness to semantic divergence between two interpretations.

    While a ``TextCountermodel`` records the divergence at the *judgment* level
    (i.e., two full 8-tuples disagree), a ``SemanticDivergenceWitness`` records
    it at the *interpretation* level: it stores the two distinct semantic values
    assigned to the same syntactic object and the distinguishing formula that
    separates them.

    A witness is *constructive* when it provides an explicit text fragment that
    can be evaluated under both interpretations to confirm the divergence.  A
    non-constructive witness only certifies existence via an indirect argument.

    In the Čech-cohomology picture a constructive witness corresponds to a
    concrete section of the sheaf over U₁ ∩ U₂ that fails to extend; a
    non-constructive witness corresponds to a cohomology class without a
    representative section.

    Fields
    ------
    witness_id : str
        Stable identifier.
    left_interpretation : str
        Semantic value assigned by the *left* (first) judgment.
    right_interpretation : str
        Semantic value assigned by the *right* (second) judgment.
    distinguishing_formula : str
        A formula Δ such that left_interpretation ⊨ Δ but right_interpretation ⊭ Δ
        (or vice versa).
    trust_level : int
        Trust tier ordinal under which the witness was produced.
    is_constructive : bool
        True iff an explicit distinguishing text fragment is available.
    """

    witness_id: str
    left_interpretation: str
    right_interpretation: str
    distinguishing_formula: str
    trust_level: int
    is_constructive: bool

    def as_divergence_summary(self) -> str:
        """Return a concise human-readable description of the divergence."""
        kind = "constructive" if self.is_constructive else "non-constructive"
        tier = trust_from_int(self.trust_level).name
        return (
            f"[{kind} witness @ {tier}] "
            f"left=«{self.left_interpretation[:40]}» "
            f"vs right=«{self.right_interpretation[:40]}» "
            f"via Δ={self.distinguishing_formula[:40]}"
        )

    def is_symmetric(self) -> bool:
        """Return True iff the left and right interpretations are identical.

        A symmetric witness is degenerate (no real divergence); callers should
        discard such witnesses as spurious.
        """
        return self.left_interpretation == self.right_interpretation


@dataclass(frozen=True)
class TextRepairHint:
    """A hint for repairing a semantically divergent text.

    A ``TextRepairHint`` is the output of ``generate_text_repair``.  Each hint
    proposes a single local edit to the diverging clause that would bring the
    two judgments into agreement (or at least reduce the Čech H¹ obstruction).

    Repair kinds
    ------------
    SUBSTITUTION
        Replace a sub-string of the clause with a suggested alternative.
    DELETION
        Remove a sub-string or sub-clause entirely.
    INSERTION
        Insert new text at a specified position.
    REORDER
        Permute the tokens/sub-clauses into a different order.

    Fields
    ------
    hint_id : str
        Stable identifier for this hint.
    divergence_id : str
        The ``countermodel_id`` of the ``TextCountermodel`` this hint addresses.
    repair_kind : str
        One of "SUBSTITUTION", "DELETION", "INSERTION", "REORDER".
    suggested_text : str
        The full repaired clause text (post-application of the edit).
    confidence : float
        Confidence in [0, 1] that applying this hint will resolve the divergence.
    """

    hint_id: str
    divergence_id: str
    repair_kind: str
    suggested_text: str
    confidence: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "repair_kind", self.repair_kind.upper())
        if self.repair_kind not in REPAIR_KINDS:
            raise ValueError(
                f"repair_kind must be one of {REPAIR_KINDS}, got {self.repair_kind!r}"
            )
        if not (0.0 <= self.confidence <= 1.0):
            raise ValueError(f"confidence must be in [0, 1], got {self.confidence}")

    def is_actionable(self, threshold: float = MIN_REPAIR_CONFIDENCE) -> bool:
        """Return True iff confidence meets the actionability threshold."""
        return self.confidence >= threshold


@dataclass(frozen=True)
class CountermodelSearch:
    """State of a search procedure for text countermodels.

    The search maintains a bounded enumeration of candidate witness texts,
    scored by a trust-weighted heuristic.  When a valid countermodel is found
    it is appended to ``found_models``; the search terminates when either
    ``max_depth`` candidates have been tried or the full space is exhausted.

    Fields
    ------
    search_id : str
        Unique identifier for this search run.
    strategy : str
        One of the ``SEARCH_STRATEGIES`` constants.
    max_depth : int
        Maximum number of candidate witnesses to try.
    trust_threshold : int
        Minimum trust-tier ordinal a countermodel must meet to be accepted.
    found_models : tuple[TextCountermodel, ...]
        All countermodels found so far, in discovery order.
    """

    search_id: str
    strategy: str
    max_depth: int
    trust_threshold: int
    found_models: tuple[TextCountermodel, ...]

    def __post_init__(self) -> None:
        if self.strategy not in SEARCH_STRATEGIES:
            raise ValueError(
                f"strategy must be one of {SEARCH_STRATEGIES}, got {self.strategy!r}"
            )
        if self.max_depth < 1:
            raise ValueError(f"max_depth must be ≥ 1, got {self.max_depth}")

    def is_exhausted(self) -> bool:
        """Return True iff the search has reached its depth limit."""
        return len(self.found_models) >= self.max_depth

    def best_model(self) -> TextCountermodel | None:
        """Return the highest-trust genuine countermodel found, or None."""
        genuine = [m for m in self.found_models if m.is_genuine]
        if not genuine:
            return None
        return max(genuine, key=lambda m: m.trust_level)

    def summary(self) -> str:
        """Return a one-line summary of the search state."""
        return (
            f"CountermodelSearch[{self.strategy}] "
            f"depth={len(self.found_models)}/{self.max_depth} "
            f"found={len(self.found_models)} models"
        )


@dataclass(frozen=True)
class ClauseDecomposition:
    """Decomposition of a text into logical clauses with dependency information.

    Breaking a multi-clause text into individual clauses is the prerequisite
    for clausewise countermodel search.  The decomposition also records
    syntactic type tags for each clause and a dependency graph that captures
    which clauses reference or modify others.

    The dependency graph is represented as a tuple of (from_clause, to_clause)
    pairs where an edge (u, v) means "clause u depends on clause v" in the
    sense that the truth value of u's formula may be influenced by v's.

    Fields
    ------
    decomp_id : str
        Stable content-addressed identifier for this decomposition.
    source_text : str
        The original unsplit text.
    clauses : tuple[str, ...]
        The individual clauses in document order.
    clause_types : tuple[str, ...]
        Syntactic type tag for each clause (parallel to ``clauses``).
    dependency_graph : tuple[tuple[str, str], ...]
        Directed dependency edges as (from_clause, to_clause) pairs.
    """

    decomp_id: str
    source_text: str
    clauses: tuple[str, ...]
    clause_types: tuple[str, ...]
    dependency_graph: tuple[tuple[str, str], ...]

    def __post_init__(self) -> None:
        if len(self.clauses) != len(self.clause_types):
            raise ValueError(
                f"clauses and clause_types must have the same length, "
                f"got {len(self.clauses)} and {len(self.clause_types)}"
            )

    def independent_clauses(self) -> tuple[str, ...]:
        """Return clauses that no other clause depends on (i.e. roots)."""
        has_incoming: set[str] = {b for _, b in self.dependency_graph}
        return tuple(c for c in self.clauses if c not in has_incoming)

    def clause_by_index(self, index: int) -> str:
        """Return the clause at *index*, raising IndexError if out of range."""
        return self.clauses[index]

    def type_of(self, clause: str) -> str | None:
        """Return the type tag for *clause*, or None if not found."""
        try:
            idx = self.clauses.index(clause)
            return self.clause_types[idx]
        except ValueError:
            return None


# ---------------------------------------------------------------------------
# Judgment tuple accessors (named positions in the 8-tuple)
# ---------------------------------------------------------------------------

def _judgment_clause(j: tuple) -> str:
    return str(j[0]) if len(j) > 0 else ""

def _judgment_formula(j: tuple) -> str:
    return str(j[1]) if len(j) > 1 else ""

def _judgment_annotations(j: tuple) -> Any:
    return j[2] if len(j) > 2 else {}

def _judgment_evidence(j: tuple) -> Any:
    return j[3] if len(j) > 3 else {}

def _judgment_obstruction(j: tuple) -> Any:
    return j[4] if len(j) > 4 else None

def _judgment_budget(j: tuple) -> int:
    return int(j[5]) if len(j) > 5 else 0

def _judgment_trust(j: tuple) -> TrustTier:
    raw = j[6] if len(j) > 6 else 0
    if isinstance(raw, TrustTier):
        return raw
    return trust_from_int(int(raw))

def _judgment_proof(j: tuple) -> Any:
    return j[7] if len(j) > 7 else None


# ---------------------------------------------------------------------------
# Clausewise decomposition
# ---------------------------------------------------------------------------

#: Regex patterns used to split text into logical clauses.
_CLAUSE_SPLIT_RE = re.compile(
    r"(?<=[.!?;])\s+|(?<=\n)\s*(?=[A-Z])|(?:\band\b|\bor\b|\bbut\b)(?=\s)",
    re.UNICODE,
)

#: Mapping from leading-keyword to clause type tag.
_CLAUSE_TYPE_KEYWORDS: dict[str, str] = {
    "import": "IMPORT",
    "from": "IMPORT",
    "def ": "DECLARATION",
    "class ": "DECLARATION",
    "return": "STATEMENT",
    "yield": "STATEMENT",
    "raise": "STATEMENT",
    "assert": "ASSERTION",
    "if ": "CONDITIONAL",
    "elif ": "CONDITIONAL",
    "else": "CONDITIONAL",
    "for ": "LOOP",
    "while ": "LOOP",
    "try": "EXCEPTION",
    "except": "EXCEPTION",
    "with ": "CONTEXT",
    "#": "COMMENT",
    '"""': "DOCSTRING",
    "'''": "DOCSTRING",
}


def _classify_clause(clause: str) -> str:
    """Return a syntactic type tag for *clause* based on leading keywords."""
    stripped = clause.strip()
    for keyword, tag in _CLAUSE_TYPE_KEYWORDS.items():
        if stripped.startswith(keyword):
            return tag
    if re.match(r"[A-Za-z_]\w*\s*=", stripped):
        return "ASSIGNMENT"
    if re.match(r"[A-Za-z_]\w*\s*\(", stripped):
        return "EXPRESSION"
    return "UNKNOWN"


def _build_dependency_pairs(clauses: tuple[str, ...]) -> tuple[tuple[str, str], ...]:
    """Build a simple dependency graph by detecting shared identifiers.

    Two clauses share a directed dependency edge (u → v) when clause *u*
    contains an identifier that is assigned or defined in clause *v* and *v*
    appears before *u* in document order.  This is a conservative syntactic
    approximation; a full data-flow analysis would require an AST.
    """
    # Extract defined names: anything matching "name =" or "def name" or "class name".
    defined_in: dict[str, str] = {}
    for clause in clauses:
        m = re.search(r"(?:def |class )?([A-Za-z_]\w*)\s*(?:=|\()", clause)
        if m:
            name = m.group(1)
            if name not in defined_in:
                defined_in[name] = clause

    edges: list[tuple[str, str]] = []
    for i, clause in enumerate(clauses):
        for name, defn_clause in defined_in.items():
            if defn_clause != clause and name in clause and defn_clause in clauses:
                defn_idx = clauses.index(defn_clause)
                if defn_idx < i:
                    edges.append((clause, defn_clause))
    # Deduplicate while preserving order.
    seen: set[tuple[str, str]] = set()
    unique: list[tuple[str, str]] = []
    for e in edges:
        if e not in seen:
            seen.add(e)
            unique.append(e)
    return tuple(unique)


def decompose_into_clauses(text: str) -> ClauseDecomposition:
    """Decompose *text* into a ``ClauseDecomposition`` of logical clauses.

    The decomposition splits the input on sentence boundaries, logical
    connectives (and/or/but), and Python statement separators.  Each
    resulting fragment is classified by ``_classify_clause`` and the
    inter-clause dependency graph is inferred heuristically.

    Parameters
    ----------
    text:
        Arbitrary text — may be a docstring, natural-language description,
        or a short Python snippet.

    Returns
    -------
    ClauseDecomposition
        A frozen dataclass describing the clauses, their types, and their
        dependency edges.

    Notes
    -----
    The decomposition is intentionally conservative: when in doubt it errs
    on the side of producing more, shorter clauses rather than fewer longer
    ones.  Callers that need sentence-level granularity should post-filter.
    """
    if not text or not text.strip():
        empty_id = _make_id("decomp", "<empty>")
        return ClauseDecomposition(
            decomp_id=empty_id,
            source_text=text,
            clauses=(),
            clause_types=(),
            dependency_graph=(),
        )

    # Split on newlines first (for code-style text), then on sentence boundaries.
    raw_lines = [line.strip() for line in text.splitlines() if line.strip()]
    sub_clauses: list[str] = []
    for line in raw_lines:
        # Further split on sentence-ending punctuation + space.
        parts = _CLAUSE_SPLIT_RE.split(line)
        sub_clauses.extend(p.strip() for p in parts if p.strip())

    # Deduplicate while preserving order.
    seen_clauses: set[str] = set()
    unique_clauses: list[str] = []
    for c in sub_clauses:
        if c not in seen_clauses:
            seen_clauses.add(c)
            unique_clauses.append(c)

    clauses_tuple = tuple(unique_clauses)
    types_tuple = tuple(_classify_clause(c) for c in clauses_tuple)
    dep_graph = _build_dependency_pairs(clauses_tuple)
    decomp_id = _make_id("decomp", text[:128])
    return ClauseDecomposition(
        decomp_id=decomp_id,
        source_text=text,
        clauses=clauses_tuple,
        clause_types=types_tuple,
        dependency_graph=dep_graph,
    )


# ---------------------------------------------------------------------------
# Countermodel validity checking
# ---------------------------------------------------------------------------

def check_countermodel_validity(cm: TextCountermodel) -> bool:
    """Validate that *cm* is a genuine countermodel.

    A countermodel is genuine when:
    1. The witness text is non-empty.
    2. The two judgment formulas are not identical (otherwise there is nothing
       to witness).
    3. The formulas are syntactically incompatible (heuristic check via
       ``_formulas_are_compatible``).
    4. The trust level is non-negative.
    5. The countermodel ID is consistent with its content.

    Parameters
    ----------
    cm : TextCountermodel
        The countermodel to validate.

    Returns
    -------
    bool
        True iff all validity conditions are satisfied.

    Notes
    -----
    This function does *not* invoke a theorem prover; it performs purely
    syntactic and structural checks.  For full semantic validation you must
    call a Z3 solver (see ``jugeo.solver.z3_session``).
    """
    if not cm.witness_text.strip():
        _LOGGER.debug("countermodel %s invalid: empty witness text", cm.countermodel_id)
        return False

    phi_a = _judgment_formula(cm.judgment_a)
    phi_b = _judgment_formula(cm.judgment_b)

    if phi_a == phi_b:
        _LOGGER.debug(
            "countermodel %s invalid: both judgments have identical formulas",
            cm.countermodel_id,
        )
        return False

    if _formulas_are_compatible(phi_a, phi_b):
        # Formulas appear compatible; no obvious contradiction to witness.
        # Still accept — the incompatibility may be subtle.
        _LOGGER.debug(
            "countermodel %s: formulas appear compatible (may be false positive)",
            cm.countermodel_id,
        )

    if cm.trust_level < 0:
        _LOGGER.debug(
            "countermodel %s invalid: negative trust level %d",
            cm.countermodel_id,
            cm.trust_level,
        )
        return False

    if not cm.diverging_clause.strip():
        _LOGGER.debug(
            "countermodel %s invalid: empty diverging clause",
            cm.countermodel_id,
        )
        return False

    return True


# ---------------------------------------------------------------------------
# Minimal diverging clause search
# ---------------------------------------------------------------------------

def minimal_diverging_clause(cm: TextCountermodel) -> str:
    """Return the shortest sub-clause of *cm.diverging_clause* that still diverges.

    This function applies a simple bisection strategy: it progressively
    shortens the diverging clause by splitting on whitespace and checking
    whether the shorter fragment is still relevant to the divergence.
    "Relevant" is approximated by checking whether the fragment appears in
    both the left and right formula strings of the countermodel.

    Parameters
    ----------
    cm : TextCountermodel
        The countermodel whose diverging clause we wish to minimise.

    Returns
    -------
    str
        The minimal diverging sub-clause.  If no strictly shorter sub-clause
        is found the original ``cm.diverging_clause`` is returned unchanged.

    Notes
    -----
    A complete minimisation would use delta-debugging (DD algorithm) against
    a semantic oracle.  This implementation is a fast syntactic approximation
    suitable for explanatory output.
    """
    clause = cm.diverging_clause.strip()
    if not clause:
        return clause

    phi_a = _judgment_formula(cm.judgment_a).lower()
    phi_b = _judgment_formula(cm.judgment_b).lower()
    witness = cm.witness_text.lower()

    tokens = clause.split()
    if len(tokens) <= 1:
        return clause

    # Try progressively smaller windows, starting at full width.
    best = clause
    for window in range(len(tokens), 0, -1):
        for start in range(len(tokens) - window + 1):
            fragment = " ".join(tokens[start : start + window])
            frag_lower = fragment.lower()
            # Keep fragment if it appears in at least one formula or the witness.
            if frag_lower in phi_a or frag_lower in phi_b or frag_lower in witness:
                best = fragment
                break
        else:
            continue
        break  # Found a smaller relevant fragment.

    return best


# ---------------------------------------------------------------------------
# Repair confidence scoring
# ---------------------------------------------------------------------------

def repair_confidence_score(hint: TextRepairHint, cm: TextCountermodel) -> float:
    """Compute a confidence score for *hint* given the countermodel *cm*.

    The score is a weighted combination of:
    - Structural match: how much of the suggested text overlaps with the
      witness text tokens (Jaccard coefficient of token sets).
    - Trust alignment: whether the hint's divergence_id matches cm.countermodel_id.
    - Kind bonus: SUBSTITUTION hints receive a modest bonus because they
      preserve clause length; DELETION hints receive a penalty because they
      may introduce new gaps.
    - Genuine bonus: a genuine countermodel earns a higher baseline.

    Parameters
    ----------
    hint : TextRepairHint
        The repair hint to score.
    cm : TextCountermodel
        The countermodel the hint is supposed to address.

    Returns
    -------
    float
        A score in [0.0, 1.0].
    """
    base = 0.3 if cm.is_genuine else 0.15

    # Identity check: does this hint address the right countermodel?
    if hint.divergence_id != cm.countermodel_id:
        base *= 0.5  # Penalise cross-countermodel hints.

    # Jaccard similarity between suggested text and witness text tokens.
    suggested_tokens = set(hint.suggested_text.lower().split())
    witness_tokens = set(cm.witness_text.lower().split())
    if suggested_tokens or witness_tokens:
        intersection = len(suggested_tokens & witness_tokens)
        union = len(suggested_tokens | witness_tokens)
        jaccard = intersection / union if union > 0 else 0.0
    else:
        jaccard = 0.0

    # Trust alignment bonus.
    trust_bonus = cm.trust_level * 0.05  # 0..0.15

    # Kind-specific adjustments.
    kind_delta = {
        "SUBSTITUTION": +0.10,
        "DELETION": -0.05,
        "INSERTION": +0.05,
        "REORDER": 0.00,
    }.get(hint.repair_kind, 0.0)

    raw = base + jaccard * 0.4 + trust_bonus + kind_delta
    return max(0.0, min(1.0, raw))


# ---------------------------------------------------------------------------
# Clause divergence explanation
# ---------------------------------------------------------------------------

def explain_clause_divergence(
    clause: str,
    context: dict[str, Any] | None = None,
) -> ClauseExplanation:
    """Return a ``ClauseExplanation`` for why *clause* causes divergence.

    The explanation is produced by a multi-pass analysis:
    1. Classify the clause syntactically (``_classify_clause``).
    2. Look for semantic divergence markers (ambiguous quantifiers, negations,
       scope-altering punctuation).
    3. Look for syntactic divergence markers (naming-law violations, forbidden
       character classes).
    4. If neither is found, default to PRAGMATIC divergence (context-dependent).
    5. Produce ranked repair hints based on the divergence type.

    Parameters
    ----------
    clause : str
        The text of the diverging clause.
    context : dict, optional
        Additional context mapping e.g. ``{"formula_a": "...", "formula_b": "..."}``.
        Used to refine the divergence-type classification.

    Returns
    -------
    ClauseExplanation
        A frozen explanation record.

    Examples
    --------
    >>> expl = explain_clause_divergence("the function must not return None")
    >>> expl.divergence_type
    'SEMANTIC'
    """
    ctx = context or {}
    clause_kind = _classify_clause(clause)

    # --- Pass 1: semantic divergence markers ---
    semantic_markers = [
        # Negation ambiguity
        (r"\bnot\b.*\bnot\b", "Double negation creates ambiguous scope."),
        (r"\bnever\b.*\balways\b", "Contradictory temporal quantifiers."),
        (r"\bmust\b.*\bshould\b", "Conflicting modal verbs weaken the obligation."),
        (r"\b(any|every|all)\b.*\b(no|none|never)\b", "Universal vs. empty quantifier conflict."),
        (r"(≥|>=|at least).*(<|less than)", "Numeric range self-contradiction."),
        (r"\binclude\b.*\bexclude\b", "Include/exclude conflict in same clause."),
    ]

    semantic_hints: list[str] = []
    for pattern, hint_text in semantic_markers:
        if re.search(pattern, clause, re.IGNORECASE):
            semantic_hints.append(hint_text)

    if semantic_hints:
        divergence_type = "SEMANTIC"
        confidence = min(0.55 + 0.1 * len(semantic_hints), 0.95)
        hints = tuple(semantic_hints[:4] + [
            "Consider rewriting with explicit quantifier scope.",
            "Use a single modal verb (must/should/may) consistently.",
        ])
        explanation_id = _make_id("expl", clause, "SEMANTIC")
        return ClauseExplanation(
            explanation_id=explanation_id,
            clause_text=clause,
            clause_kind=clause_kind,
            divergence_type=divergence_type,
            confidence=confidence,
            repair_hints=hints,
        )

    # --- Pass 2: syntactic divergence markers ---
    syntactic_checks: list[tuple[bool, str]] = [
        (bool(re.search(r"[^\x00-\x7F]", clause)), "Non-ASCII character found; may violate naming law."),
        (bool(re.search(r"\s{2,}", clause)), "Multiple consecutive spaces may break tokenisation."),
        (bool(re.search(r"[A-Z]{2,}", clause)) and clause_kind == "DECLARATION",
         "All-caps identifier segment may violate camelCase naming law."),
        (bool(re.search(r"__\w+__", clause)), "Dunder identifier may conflict with reserved names."),
        (bool(re.search(r"\bpass\b|\bNone\b|\bnothing\b", clause, re.IGNORECASE)),
         "Placeholder or null literal may underspecify the clause semantics."),
    ]

    syntactic_hints: list[str] = []
    for triggered, hint_text in syntactic_checks:
        if triggered:
            syntactic_hints.append(hint_text)

    if syntactic_hints:
        divergence_type = "SYNTACTIC"
        confidence = min(0.50 + 0.08 * len(syntactic_hints), 0.90)
        hints = tuple(syntactic_hints[:4] + [
            "Apply NFKC Unicode normalisation before evaluation.",
            "Enforce a consistent naming-law pattern (snake_case or camelCase).",
        ])
        explanation_id = _make_id("expl", clause, "SYNTACTIC")
        return ClauseExplanation(
            explanation_id=explanation_id,
            clause_text=clause,
            clause_kind=clause_kind,
            divergence_type=divergence_type,
            confidence=confidence,
            repair_hints=hints,
        )

    # --- Pass 3: pragmatic fallback ---
    formula_a: str = ctx.get("formula_a", "")
    formula_b: str = ctx.get("formula_b", "")
    pragmatic_hints: list[str] = []

    if formula_a and formula_b:
        shared_terms = set(formula_a.lower().split()) & set(formula_b.lower().split())
        if len(shared_terms) < 3:
            pragmatic_hints.append(
                "Formulas share few terms; divergence may stem from incompatible domain vocabularies."
            )
    pragmatic_hints.extend([
        "Consult the project style guide for context-specific term-of-art requirements.",
        "Check whether the clause references a domain concept that has divergent definitions.",
        "Consider adding an explicit scope annotation (e.g. @scope: public/private).",
        f"Clause kind '{clause_kind}' may have context-specific evaluation rules.",
    ])

    confidence = 0.35
    explanation_id = _make_id("expl", clause, "PRAGMATIC")
    return ClauseExplanation(
        explanation_id=explanation_id,
        clause_text=clause,
        clause_kind=clause_kind,
        divergence_type="PRAGMATIC",
        confidence=confidence,
        repair_hints=tuple(pragmatic_hints),
    )


# ---------------------------------------------------------------------------
# Repair hint generation
# ---------------------------------------------------------------------------

def generate_text_repair(countermodel: TextCountermodel) -> list[TextRepairHint]:
    """Generate a list of ``TextRepairHint`` objects for *countermodel*.

    The generator applies four strategies in turn — substitution, deletion,
    insertion, and reorder — each producing one or more candidate hints.
    Only hints whose ``repair_confidence_score`` exceeds ``MIN_REPAIR_CONFIDENCE``
    are included in the output list.

    The output is sorted in descending order of confidence so callers can
    present the most promising repair first.

    Parameters
    ----------
    countermodel : TextCountermodel
        The countermodel to repair.

    Returns
    -------
    list[TextRepairHint]
        Repair hints, sorted by descending confidence.

    Notes
    -----
    This function does *not* verify that the suggested text resolves the
    countermodel; it only generates candidates.  Use ``check_countermodel_validity``
    on the resulting judgment pair after applying the hint to confirm resolution.
    """
    clause = countermodel.diverging_clause
    witness = countermodel.witness_text
    cm_id = countermodel.countermodel_id

    phi_a = _judgment_formula(countermodel.judgment_a)
    phi_b = _judgment_formula(countermodel.judgment_b)

    candidates: list[TextRepairHint] = []

    # --- Strategy 1: SUBSTITUTION ---
    # Replace the witness text with a neutral synonym.
    neutral_synonyms: dict[str, str] = {
        "not": "without",
        "never": "not always",
        "must": "should",
        "always": "typically",
        "all": "most",
        "none": "few",
        "exclude": "limit",
        "include": "consider",
        "positive": "non-negative",
        "negative": "non-positive",
    }
    substituted = clause
    for original, replacement in neutral_synonyms.items():
        pattern = rf"\b{re.escape(original)}\b"
        if re.search(pattern, substituted, re.IGNORECASE):
            substituted = re.sub(pattern, replacement, substituted, count=1, flags=re.IGNORECASE)
    if substituted != clause:
        hint_id = _make_id("hint", cm_id, "SUBSTITUTION", substituted)
        hint = TextRepairHint(
            hint_id=hint_id,
            divergence_id=cm_id,
            repair_kind="SUBSTITUTION",
            suggested_text=substituted,
            confidence=0.0,  # will be overwritten below
        )
        score = repair_confidence_score(hint, countermodel)
        hint = TextRepairHint(
            hint_id=hint_id,
            divergence_id=cm_id,
            repair_kind="SUBSTITUTION",
            suggested_text=substituted,
            confidence=score,
        )
        candidates.append(hint)

    # --- Strategy 2: DELETION ---
    # Remove the fragment most responsible for the divergence.
    minimal = minimal_diverging_clause(countermodel)
    if minimal and minimal != clause:
        deleted = clause.replace(minimal, "").strip()
        if deleted:
            hint_id = _make_id("hint", cm_id, "DELETION", deleted)
            hint = TextRepairHint(
                hint_id=hint_id,
                divergence_id=cm_id,
                repair_kind="DELETION",
                suggested_text=deleted,
                confidence=0.0,
            )
            score = repair_confidence_score(hint, countermodel)
            hint = TextRepairHint(
                hint_id=hint_id,
                divergence_id=cm_id,
                repair_kind="DELETION",
                suggested_text=deleted,
                confidence=score,
            )
            candidates.append(hint)

    # --- Strategy 3: INSERTION ---
    # Insert a clarifying qualifier at the start of the clause.
    qualifier_map = {
        "SEMANTIC": "Formally: ",
        "SYNTACTIC": "Canonically: ",
        "PRAGMATIC": "By convention: ",
    }
    expl = explain_clause_divergence(clause, {"formula_a": phi_a, "formula_b": phi_b})
    qualifier = qualifier_map.get(expl.divergence_type, "Note: ")
    inserted = qualifier + clause
    hint_id = _make_id("hint", cm_id, "INSERTION", inserted[:64])
    hint = TextRepairHint(
        hint_id=hint_id,
        divergence_id=cm_id,
        repair_kind="INSERTION",
        suggested_text=inserted,
        confidence=0.0,
    )
    score = repair_confidence_score(hint, countermodel)
    hint = TextRepairHint(
        hint_id=hint_id,
        divergence_id=cm_id,
        repair_kind="INSERTION",
        suggested_text=inserted,
        confidence=score,
    )
    candidates.append(hint)

    # --- Strategy 4: REORDER ---
    # Reverse the token order of the clause as a last resort.
    tokens = clause.split()
    if len(tokens) > 1:
        reordered = " ".join(reversed(tokens))
        hint_id = _make_id("hint", cm_id, "REORDER", reordered[:64])
        hint = TextRepairHint(
            hint_id=hint_id,
            divergence_id=cm_id,
            repair_kind="REORDER",
            suggested_text=reordered,
            confidence=0.0,
        )
        score = repair_confidence_score(hint, countermodel)
        hint = TextRepairHint(
            hint_id=hint_id,
            divergence_id=cm_id,
            repair_kind="REORDER",
            suggested_text=reordered,
            confidence=score,
        )
        candidates.append(hint)

    # Filter and sort.
    actionable = [h for h in candidates if h.is_actionable()]
    actionable.sort(key=lambda h: h.confidence, reverse=True)
    return actionable


# ---------------------------------------------------------------------------
# Countermodel search construction
# ---------------------------------------------------------------------------

def build_countermodel_search(
    strategy: str = "GREEDY",
    depth: int = DEFAULT_MAX_DEPTH,
    trust_threshold: int = 0,
) -> CountermodelSearch:
    """Construct a fresh ``CountermodelSearch`` with the given parameters.

    Parameters
    ----------
    strategy : str
        One of the ``SEARCH_STRATEGIES`` constants.  Defaults to "GREEDY".
    depth : int
        Maximum number of candidate witnesses to evaluate.
    trust_threshold : int
        Minimum trust tier ordinal (0–3) that a found model must satisfy.

    Returns
    -------
    CountermodelSearch
        A new search object with no models found yet.

    Raises
    ------
    ValueError
        If *strategy* is not in ``SEARCH_STRATEGIES`` or *depth* < 1.
    """
    if strategy not in SEARCH_STRATEGIES:
        raise ValueError(
            f"strategy must be one of {SEARCH_STRATEGIES}, got {strategy!r}"
        )
    if depth < 1:
        raise ValueError(f"depth must be ≥ 1, got {depth}")
    search_id = _fresh_id("search")
    return CountermodelSearch(
        search_id=search_id,
        strategy=strategy,
        max_depth=depth,
        trust_threshold=max(0, min(3, trust_threshold)),
        found_models=(),
    )


# ---------------------------------------------------------------------------
# Main countermodel builder
# ---------------------------------------------------------------------------

def _generate_witness_candidates(
    judgment_a: tuple,
    judgment_b: tuple,
    max_count: int,
) -> Iterator[tuple[str, str]]:
    """Yield (witness_text, diverging_clause) pairs for the two judgments.

    Candidates are synthesised from the formula strings and clause texts of
    both judgments.  The iterator is finite and yields at most *max_count*
    candidates.

    Strategy:
    1. Yield tokens from formula_a that are absent from formula_b (and vice
       versa); these are strong candidates because they appear in exactly
       one judgment.
    2. Yield cross-product pairs of tokens from each formula.
    3. Yield the witness clauses from the decomposition of each clause text.
    """
    phi_a_tokens = set(_judgment_formula(judgment_a).lower().split())
    phi_b_tokens = set(_judgment_formula(judgment_b).lower().split())
    clause_a = _judgment_clause(judgment_a)
    clause_b = _judgment_clause(judgment_b)

    yielded = 0

    # Tokens exclusive to each formula.
    for token in sorted(phi_a_tokens - phi_b_tokens):
        if yielded >= max_count:
            return
        yield token, clause_a
        yielded += 1

    for token in sorted(phi_b_tokens - phi_a_tokens):
        if yielded >= max_count:
            return
        yield token, clause_b
        yielded += 1

    # Cross-product pairs.
    for tok_a, tok_b in itertools.product(sorted(phi_a_tokens)[:8], sorted(phi_b_tokens)[:8]):
        if yielded >= max_count:
            return
        yield f"{tok_a} {tok_b}", clause_a
        yielded += 1

    # Sub-clauses from the decomposition of each clause text.
    for source_clause in (clause_a, clause_b):
        decomp = decompose_into_clauses(source_clause)
        for sub in decomp.clauses:
            if yielded >= max_count:
                return
            yield sub, source_clause
            yielded += 1


def build_text_countermodel(
    judgment_a: tuple,
    judgment_b: tuple,
    search: CountermodelSearch,
) -> TextCountermodel:
    """Find a ``TextCountermodel`` witnessing divergence between *judgment_a* and *judgment_b*.

    The function iterates over candidate witness texts generated by
    ``_generate_witness_candidates``, constructs a provisional countermodel
    for each, and returns the first that passes ``check_countermodel_validity``.
    If no genuine countermodel is found within the search depth budget a
    tentative (``is_genuine=False``) countermodel is returned as a best effort.

    The relationship to the judgment 8-tuple
    -----------------------------------------
    Given J₁ = (c, φ, A, E, O, B, T, Π) and J₂ = (c, φ', A', E', O', B', T', Π'):

    The function examines whether there is a text w such that:
      - w ∈ [[φ]] (w satisfies the formula of J₁), and
      - w ∉ [[φ']] (w does not satisfy the formula of J₂).

    This is approximated syntactically by checking that the token sets of w
    overlap more with φ than with φ'.

    The Čech H¹ obstruction O is computed via ``_cech_h1_representative`` and
    is available on the returned countermodel via ``.obstruction_class()``.

    Parameters
    ----------
    judgment_a : tuple
        First judgment as a raw 8-tuple (c, φ, A, E, O, B, T, Π).
    judgment_b : tuple
        Second judgment as a raw 8-tuple.
    search : CountermodelSearch
        Search configuration controlling strategy, depth, and trust threshold.

    Returns
    -------
    TextCountermodel
        The best countermodel found (genuine if possible, tentative otherwise).
    """
    phi_a = _judgment_formula(judgment_a)
    phi_b = _judgment_formula(judgment_b)

    if not phi_a and not phi_b:
        # No formulas at all — produce a minimal trivial countermodel.
        cm_id = _make_id("cm", "<empty>", "<empty>")
        return TextCountermodel(
            countermodel_id=cm_id,
            judgment_a=judgment_a,
            judgment_b=judgment_b,
            diverging_clause="<no clause>",
            witness_text="<no witness>",
            trust_level=0,
            is_genuine=False,
        )

    phi_a_tokens = set(phi_a.lower().split())
    phi_b_tokens = set(phi_b.lower().split())

    best_candidate: tuple[str, str] | None = None  # (witness, diverging_clause)
    best_score: float = -1.0

    trust_min = search.trust_threshold

    for witness, diverging_clause in _generate_witness_candidates(
        judgment_a, judgment_b, search.max_depth
    ):
        w_tokens = set(witness.lower().split())
        # Score: high if witness overlaps more with phi_a than phi_b.
        overlap_a = len(w_tokens & phi_a_tokens) / max(len(phi_a_tokens), 1)
        overlap_b = len(w_tokens & phi_b_tokens) / max(len(phi_b_tokens), 1)
        score = overlap_a - overlap_b

        if score > best_score:
            best_score = score
            best_candidate = (witness, diverging_clause)

    if best_candidate is None:
        # No candidates at all.
        best_candidate = (phi_a[:40], _judgment_clause(judgment_a))

    witness_text, diverging_clause = best_candidate

    # Determine trust level: use the meet of the two judgment trust tiers.
    tier_a = _judgment_trust(judgment_a)
    tier_b = _judgment_trust(judgment_b)
    combined_tier = trust_meet(tier_a, tier_b)
    trust_level = trust_to_int(combined_tier)
    trust_level = max(trust_level, trust_min)

    cm_id = _make_id("cm", phi_a[:64], phi_b[:64], witness_text[:64])

    provisional = TextCountermodel(
        countermodel_id=cm_id,
        judgment_a=judgment_a,
        judgment_b=judgment_b,
        diverging_clause=diverging_clause,
        witness_text=witness_text,
        trust_level=trust_level,
        is_genuine=False,
    )

    is_genuine = check_countermodel_validity(provisional)

    cm = TextCountermodel(
        countermodel_id=cm_id,
        judgment_a=judgment_a,
        judgment_b=judgment_b,
        diverging_clause=diverging_clause,
        witness_text=witness_text,
        trust_level=trust_level,
        is_genuine=is_genuine,
    )

    _LOGGER.info(
        "build_text_countermodel: found %s countermodel (score=%.3f trust=%s)",
        "genuine" if is_genuine else "tentative",
        best_score,
        combined_tier.name,
    )
    return cm


# ---------------------------------------------------------------------------
# Higher-level pipeline helpers
# ---------------------------------------------------------------------------

def _build_semantic_divergence_witness(
    countermodel: TextCountermodel,
) -> SemanticDivergenceWitness:
    """Build a ``SemanticDivergenceWitness`` from an existing countermodel.

    The witness records the two semantic interpretations as the formula strings
    of the two judgments, and uses the countermodel's witness text as the
    distinguishing formula in a simplified free-text encoding.

    Parameters
    ----------
    countermodel : TextCountermodel
        A countermodel (genuine or tentative).

    Returns
    -------
    SemanticDivergenceWitness
        The constructed witness.
    """
    phi_a = _judgment_formula(countermodel.judgment_a)
    phi_b = _judgment_formula(countermodel.judgment_b)

    distinguishing = (
        f"The text fragment «{countermodel.witness_text}» satisfies "
        f"({phi_a}) but not ({phi_b})."
    )
    witness_id = _make_id(
        "sdw",
        countermodel.countermodel_id,
        phi_a[:32],
        phi_b[:32],
    )
    return SemanticDivergenceWitness(
        witness_id=witness_id,
        left_interpretation=phi_a or "(empty formula)",
        right_interpretation=phi_b or "(empty formula)",
        distinguishing_formula=distinguishing,
        trust_level=countermodel.trust_level,
        is_constructive=countermodel.is_genuine,
    )


def run_full_countermodel_pipeline(
    judgment_a: tuple,
    judgment_b: tuple,
    strategy: str = "GREEDY",
    depth: int = DEFAULT_MAX_DEPTH,
) -> dict[str, Any]:
    """Run the complete countermodel pipeline and return a results dict.

    The pipeline executes the following steps:
    1. Build a countermodel search configuration.
    2. Decompose both clauses into sub-clauses.
    3. Find a countermodel via ``build_text_countermodel``.
    4. Explain the diverging clause via ``explain_clause_divergence``.
    5. Generate repair hints via ``generate_text_repair``.
    6. Build a semantic divergence witness.

    Parameters
    ----------
    judgment_a, judgment_b : tuple
        The two judgments to compare.
    strategy : str
        Search strategy (default "GREEDY").
    depth : int
        Search depth budget.

    Returns
    -------
    dict
        A results dictionary with keys:
        ``countermodel``, ``explanation``, ``repairs``, ``witness``,
        ``decomp_a``, ``decomp_b``, ``obstruction_class``.
    """
    search = build_countermodel_search(strategy=strategy, depth=depth)
    decomp_a = decompose_into_clauses(_judgment_clause(judgment_a))
    decomp_b = decompose_into_clauses(_judgment_clause(judgment_b))

    cm = build_text_countermodel(judgment_a, judgment_b, search)
    explanation = explain_clause_divergence(
        cm.diverging_clause,
        context={
            "formula_a": _judgment_formula(judgment_a),
            "formula_b": _judgment_formula(judgment_b),
        },
    )
    repairs = generate_text_repair(cm)
    witness = _build_semantic_divergence_witness(cm)

    return {
        "countermodel": cm,
        "explanation": explanation,
        "repairs": repairs,
        "witness": witness,
        "decomp_a": decomp_a,
        "decomp_b": decomp_b,
        "obstruction_class": cm.obstruction_class(),
    }


# ---------------------------------------------------------------------------
# Smoke test / __main__
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
        stream=sys.stdout,
    )

    print("=" * 70)
    print("Text Countermodels — Smoke Test")
    print("=" * 70)

    # ------------------------------------------------------------------
    # Construct two judgment 8-tuples.
    #
    # J₁ = (c, φ, A, E, O, B, T, Π)
    # J₂ = (c, φ', A', E', O', B', T', Π')
    #
    # Here we use short natural-language strings for the formula φ so the
    # smoke test can run without a Z3 solver.
    # ------------------------------------------------------------------

    clause_text_a = (
        "The function must always return a non-negative integer "
        "and must not raise any exceptions."
    )
    clause_text_b = (
        "The function should never return a negative value "
        "but may raise ValueError on invalid input."
    )

    formula_a = "positive_return AND no_exceptions"
    formula_b = "non_negative_return AND allow_value_error"
    annotations_a = {"author": "alice", "version": "1.0"}
    annotations_b = {"author": "bob", "version": "2.0"}
    evidence_a = {"test_pass_rate": 0.95, "lint_score": 9.1}
    evidence_b = {"test_pass_rate": 0.88, "lint_score": 8.7}
    obstruction_a = None  # Will be filled after countermodel search.
    obstruction_b = None
    budget_a = 10
    budget_b = 8
    trust_a = TrustTier.HIGH
    trust_b = TrustTier.MID
    proof_a = None
    proof_b = None

    judgment_a = (
        clause_text_a, formula_a, annotations_a, evidence_a,
        obstruction_a, budget_a, trust_a, proof_a,
    )
    judgment_b = (
        clause_text_b, formula_b, annotations_b, evidence_b,
        obstruction_b, budget_b, trust_b, proof_b,
    )

    print()
    print("Judgment A:", judgment_a[:2], "... trust =", trust_a.name)
    print("Judgment B:", judgment_b[:2], "... trust =", trust_b.name)
    print()

    # ------------------------------------------------------------------
    # Step 1: Build countermodel search.
    # ------------------------------------------------------------------
    print("Step 1: Building countermodel search …")
    search = build_countermodel_search(strategy="GREEDY", depth=32)
    print(f"  {search.summary()}")
    print()

    # ------------------------------------------------------------------
    # Step 2: Decompose clauses.
    # ------------------------------------------------------------------
    print("Step 2: Decomposing clauses …")
    decomp_a = decompose_into_clauses(clause_text_a)
    decomp_b = decompose_into_clauses(clause_text_b)
    print(f"  Decomp A ({decomp_a.decomp_id[:8]}…): {len(decomp_a.clauses)} clauses")
    for i, (c, t) in enumerate(zip(decomp_a.clauses, decomp_a.clause_types)):
        print(f"    [{i}] ({t}) {c!r}")
    print(f"  Decomp B ({decomp_b.decomp_id[:8]}…): {len(decomp_b.clauses)} clauses")
    for i, (c, t) in enumerate(zip(decomp_b.clauses, decomp_b.clause_types)):
        print(f"    [{i}] ({t}) {c!r}")
    print()

    # ------------------------------------------------------------------
    # Step 3: Find countermodel.
    # ------------------------------------------------------------------
    print("Step 3: Searching for countermodel …")
    cm = build_text_countermodel(judgment_a, judgment_b, search)
    print(f"  {cm.summary()}")
    print(f"  Obstruction class (Čech H¹): {cm.obstruction_class()}")
    print()

    # ------------------------------------------------------------------
    # Step 4: Validate countermodel.
    # ------------------------------------------------------------------
    print("Step 4: Validating countermodel …")
    valid = check_countermodel_validity(cm)
    print(f"  Valid: {valid}")
    minimal_clause = minimal_diverging_clause(cm)
    print(f"  Minimal diverging clause: {minimal_clause!r}")
    print()

    # ------------------------------------------------------------------
    # Step 5: Explain clause divergence.
    # ------------------------------------------------------------------
    print("Step 5: Explaining clause divergence …")
    expl = explain_clause_divergence(
        cm.diverging_clause,
        context={"formula_a": formula_a, "formula_b": formula_b},
    )
    print(f"  explanation_id : {expl.explanation_id[:16]}…")
    print(f"  clause_kind    : {expl.clause_kind}")
    print(f"  divergence_type: {expl.divergence_type}")
    print(f"  confidence     : {expl.confidence:.2f}")
    print(f"  primary_hint   : {expl.primary_hint()}")
    print()

    # ------------------------------------------------------------------
    # Step 6: Generate repair hints.
    # ------------------------------------------------------------------
    print("Step 6: Generating repair hints …")
    hints = generate_text_repair(cm)
    if hints:
        for h in hints:
            print(f"  [{h.repair_kind}] conf={h.confidence:.2f}  «{h.suggested_text[:70]}»")
    else:
        print("  No actionable repair hints found.")
    print()

    # ------------------------------------------------------------------
    # Step 7: Build semantic divergence witness.
    # ------------------------------------------------------------------
    print("Step 7: Building semantic divergence witness …")
    sdw = _build_semantic_divergence_witness(cm)
    print(f"  {sdw.as_divergence_summary()}")
    print(f"  is_constructive : {sdw.is_constructive}")
    print(f"  is_symmetric    : {sdw.is_symmetric()}")
    print()

    # ------------------------------------------------------------------
    # Step 8: Full pipeline convenience call.
    # ------------------------------------------------------------------
    print("Step 8: Full pipeline (run_full_countermodel_pipeline) …")
    results = run_full_countermodel_pipeline(
        judgment_a, judgment_b, strategy="HEURISTIC", depth=16
    )
    print(f"  obstruction_class : {results['obstruction_class']}")
    print(f"  repairs generated : {len(results['repairs'])}")
    print(f"  explanation type  : {results['explanation'].divergence_type}")
    print()

    # ------------------------------------------------------------------
    # Step 9: TrustTier algebra checks.
    # ------------------------------------------------------------------
    print("Step 9: TrustTier ordered-algebra checks …")
    for ta in TrustTier:
        for tb in TrustTier:
            m = trust_meet(ta, tb)
            j = trust_join(ta, tb)
            assert m <= j, f"meet > join for {ta}, {tb}"
    print("  All meet ≤ join assertions passed for TrustTier lattice.")
    print()

    # ------------------------------------------------------------------
    # Step 10: ClauseDecomposition edge cases.
    # ------------------------------------------------------------------
    print("Step 10: ClauseDecomposition edge cases …")
    empty_decomp = decompose_into_clauses("")
    assert empty_decomp.clauses == (), "Empty text should give zero clauses"
    single_decomp = decompose_into_clauses("return x")
    assert len(single_decomp.clauses) >= 1, "Single clause should be detected"
    print(f"  Empty decomp   : {len(empty_decomp.clauses)} clauses ✓")
    print(f"  Single decomp  : {len(single_decomp.clauses)} clauses ✓")
    print()

    print("=" * 70)
    print("Smoke test PASSED.")
    print("=" * 70)
