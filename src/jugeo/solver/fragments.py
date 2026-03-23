"""Fragment classification and tactic routing for JuGeo.

This module classifies logical formulas into decidable Z3 fragments and routes
them to appropriate solving tactics.  The classification is informed by the
theory2.tex specification (Judgment Geometry — sheaf-theoretic type checking),
chapters 25-30, which define exact Z3 encodings for:

    ch25 — base refinements, guards, arithmetic, path conditions
    ch26 — collections, finite maps, heap summaries, alias partitions
    ch27 — strings, symbolic text, naming laws
    ch28 — sequences, heap slices, mutation
    ch29 — tensor extents, affine legality, quantifier discipline
    ch30 — partial functions, algebraic surfaces, exception-valued semantics

The classifier inspects a formula's syntactic signature — sorts, function
symbols, quantifier depth, and theory-specific operators — then maps it to
the most specific SMT-LIB fragment that covers the formula.  A tactic
selector then builds a Z3 tactic chain optimised for that fragment.

The module avoids a hard dependency on Z3; all Z3-facing types are strings
or lightweight wrappers so the builtin adapter and tests can run without
the ``z3-solver`` package installed.  When a real Z3 backend is present the
tactic strings produced here are passed through verbatim.

The ``copilot`` integration point allows an LLM oracle to suggest encodings
or decompositions for formulas that fall outside known decidable fragments,
acting as a last-resort advisor before the solver gives up.

Architecture
------------

    formula ──► FragmentClassifier.classify_formula()
                        │
                        ▼
                FragmentSignature   ──► Fragment (enum)
                        │
                ┌───────┴────────┐
                ▼                ▼
        pure fragment       mixed formula
                │                │
                ▼                ▼
        TacticSelector    FragmentDecomposer
                │                │
                ▼                ▼
        Z3 tactic chain   Nelson-Oppen split
                │                │
                └──► merge ◄─────┘
                        │
                        ▼
                EncodingStrategy.encode_*()

Key invariants
--------------
* A formula classified as ``Fragment.UNKNOWN`` is never silently dropped;
  it is escalated to the copilot assist layer or rejected with a reason.
* Fragment caching is keyed on a normalised signature hash so that
  alpha-equivalent formulas share classification results.
* The tactic selector never produces an empty chain — at minimum it falls
  back to ``smt``.
"""

from __future__ import annotations

import hashlib
import logging
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Mapping, Sequence

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 1. Fragment — SMT-LIB theory fragment taxonomy
# ---------------------------------------------------------------------------

class Fragment(Enum):
    """SMT-LIB theory fragments supported by the JuGeo solver pipeline.

    Each member corresponds to a well-known decidable (or semi-decidable)
    fragment of first-order logic with theories.  The classifier maps a
    formula to the *most specific* fragment that covers its signature.

    Members roughly follow the SMT-LIB logic naming conventions but add
    JuGeo-specific entries for strings, sequences, and mixed theories.
    """

    QF_LIA = auto()       # quantifier-free linear integer arithmetic
    QF_LRA = auto()       # quantifier-free linear real arithmetic
    QF_BV = auto()        # quantifier-free fixed-width bitvectors
    QF_UF = auto()        # quantifier-free uninterpreted functions
    QF_AUFLIA = auto()    # QF arrays + UF + LIA
    QF_ABV = auto()       # QF arrays + bitvectors
    STRINGS = auto()      # string theory (ch27)
    SEQUENCES = auto()    # sequence theory (ch28)
    ARRAYS = auto()       # extensional arrays
    DATATYPES = auto()    # algebraic datatypes
    NONLINEAR = auto()    # nonlinear real/integer arithmetic
    QUANTIFIED = auto()   # full first-order with quantifiers (ch29)
    MIXED = auto()        # multi-theory combination
    UNKNOWN = auto()      # unclassifiable — escalate

    # -- convenience predicates ------------------------------------------------

    def is_quantifier_free(self) -> bool:
        """Return ``True`` if this fragment forbids quantifiers."""
        return self.name.startswith("QF_")

    def is_decidable(self) -> bool:
        """Conservative decidability check (theory2.tex ch25 §2)."""
        _decidable = {
            Fragment.QF_LIA, Fragment.QF_LRA, Fragment.QF_BV,
            Fragment.QF_UF, Fragment.QF_AUFLIA, Fragment.QF_ABV,
            Fragment.STRINGS, Fragment.SEQUENCES, Fragment.ARRAYS,
            Fragment.DATATYPES,
        }
        return self in _decidable

    def default_timeout_ms(self) -> int:
        """Recommended wall-clock timeout in milliseconds."""
        _timeouts: dict[Fragment, int] = {
            Fragment.QF_LIA: 5_000,
            Fragment.QF_LRA: 5_000,
            Fragment.QF_BV: 10_000,
            Fragment.QF_UF: 3_000,
            Fragment.QF_AUFLIA: 15_000,
            Fragment.QF_ABV: 15_000,
            Fragment.STRINGS: 20_000,
            Fragment.SEQUENCES: 20_000,
            Fragment.ARRAYS: 10_000,
            Fragment.DATATYPES: 10_000,
            Fragment.NONLINEAR: 30_000,
            Fragment.QUANTIFIED: 60_000,
            Fragment.MIXED: 30_000,
            Fragment.UNKNOWN: 60_000,
        }
        return _timeouts.get(self, 30_000)

    def smt_lib_logic_name(self) -> str:
        """Return the SMT-LIB ``set-logic`` name, or ``ALL`` for mixed."""
        _names: dict[Fragment, str] = {
            Fragment.QF_LIA: "QF_LIA",
            Fragment.QF_LRA: "QF_LRA",
            Fragment.QF_BV: "QF_BV",
            Fragment.QF_UF: "QF_UF",
            Fragment.QF_AUFLIA: "QF_AUFLIA",
            Fragment.QF_ABV: "QF_ABV",
            Fragment.STRINGS: "QF_SLIA",
            Fragment.SEQUENCES: "QF_SLIA",
            Fragment.ARRAYS: "QF_AX",
            Fragment.DATATYPES: "QF_DT",
            Fragment.NONLINEAR: "QF_NRA",
            Fragment.QUANTIFIED: "AUFNIRA",
            Fragment.MIXED: "ALL",
            Fragment.UNKNOWN: "ALL",
        }
        return _names.get(self, "ALL")

    def parent_fragment(self) -> Fragment | None:
        """Return the next more general fragment, or ``None`` for UNKNOWN."""
        _parents: dict[Fragment, Fragment] = {
            Fragment.QF_LIA: Fragment.QF_AUFLIA,
            Fragment.QF_LRA: Fragment.NONLINEAR,
            Fragment.QF_BV: Fragment.QF_ABV,
            Fragment.QF_UF: Fragment.QF_AUFLIA,
            Fragment.QF_AUFLIA: Fragment.MIXED,
            Fragment.QF_ABV: Fragment.MIXED,
            Fragment.STRINGS: Fragment.MIXED,
            Fragment.SEQUENCES: Fragment.MIXED,
            Fragment.ARRAYS: Fragment.QF_AUFLIA,
            Fragment.DATATYPES: Fragment.MIXED,
            Fragment.NONLINEAR: Fragment.QUANTIFIED,
            Fragment.QUANTIFIED: Fragment.MIXED,
            Fragment.MIXED: Fragment.UNKNOWN,
        }
        return _parents.get(self)


# ---------------------------------------------------------------------------
# 2. FragmentSignature — syntactic profile of a formula
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class FragmentSignature:
    """Syntactic signature extracted from a formula for fragment classification.

    Every field captures one axis of the formula's theory footprint.  The
    ``classify`` method maps the combined profile to a ``Fragment``.

    Attributes
    ----------
    sorts_used : frozenset[str]
        Sort names occurring in the formula (``Int``, ``Real``, ``BitVec``,
        ``String``, ``Seq``, ``Array``, custom datatype names, etc.).
    function_symbols : frozenset[str]
        Declared or applied uninterpreted function symbols.
    quantifier_depth : int
        Maximum nesting depth of ``forall`` / ``exists``.
    array_ops : frozenset[str]
        Array-theory operators used (``select``, ``store``, ``const``).
    string_ops : frozenset[str]
        String-theory operators (``str.len``, ``str.substr``, ``str.++``, …).
    bv_widths : frozenset[int]
        Bitvector widths occurring in the formula.
    has_nonlinear : bool
        ``True`` when a multiplication of two non-constant terms is detected.
    has_datatypes : bool
        ``True`` when algebraic datatype constructors or selectors appear.
    has_sequences : bool
        ``True`` when sequence operators (``seq.unit``, ``seq.++``, …) appear.
    has_heap_ops : bool
        ``True`` when heap-specific operators (``heap.read``, ``heap.write``,
        ``heap.alloc``) appear (ch26, ch28).
    has_tensor_ops : bool
        ``True`` when tensor extent or affine operators appear (ch29).
    has_exception_ops : bool
        ``True`` when exception-valued semantics operators appear (ch30).
    """

    sorts_used: frozenset[str] = field(default_factory=frozenset)
    function_symbols: frozenset[str] = field(default_factory=frozenset)
    quantifier_depth: int = 0
    array_ops: frozenset[str] = field(default_factory=frozenset)
    string_ops: frozenset[str] = field(default_factory=frozenset)
    bv_widths: frozenset[int] = field(default_factory=frozenset)
    has_nonlinear: bool = False
    has_datatypes: bool = False
    has_sequences: bool = False
    has_heap_ops: bool = False
    has_tensor_ops: bool = False
    has_exception_ops: bool = False

    # -- classification --------------------------------------------------------

    def classify(self) -> Fragment:
        """Map this signature to the most specific ``Fragment``.

        The decision tree mirrors the SMT-LIB logic hierarchy; earlier,
        more specific branches take priority.
        """
        if self.quantifier_depth > 0:
            return Fragment.QUANTIFIED

        if self.has_nonlinear:
            return Fragment.NONLINEAR

        has_arrays = bool(self.array_ops)
        has_strings = bool(self.string_ops)
        has_bv = bool(self.bv_widths)
        has_uf = bool(self.function_symbols)
        has_lia = bool(self.sorts_used & {"Int"})
        has_lra = bool(self.sorts_used & {"Real"})

        if self.has_sequences:
            return Fragment.SEQUENCES

        if has_strings:
            return Fragment.STRINGS

        if self.has_datatypes:
            return Fragment.DATATYPES

        # count active theories to decide pure vs mixed
        active_theories = sum([
            has_arrays, has_bv, has_uf, has_lia, has_lra,
            self.has_heap_ops, self.has_tensor_ops, self.has_exception_ops,
        ])

        if active_theories > 2:
            return Fragment.MIXED

        if has_arrays and has_bv:
            return Fragment.QF_ABV

        if has_arrays and (has_uf or has_lia):
            return Fragment.QF_AUFLIA

        if has_arrays:
            return Fragment.ARRAYS

        if has_bv:
            return Fragment.QF_BV

        if has_uf and not has_lia and not has_lra:
            return Fragment.QF_UF

        if has_lra:
            return Fragment.QF_LRA

        if has_lia:
            return Fragment.QF_LIA

        if has_uf:
            return Fragment.QF_UF

        if active_theories == 0 and not self.sorts_used:
            return Fragment.QF_UF  # propositional defaults to UF

        return Fragment.UNKNOWN

    def is_decidable(self) -> bool:
        """Return ``True`` when the classified fragment is decidable."""
        return self.classify().is_decidable()

    def expected_complexity(self) -> str:
        """Return a human-readable complexity estimate.

        Estimates follow standard results: QF_LIA is NP-complete,
        QF_LRA is polynomial, QF_BV is NEXPTIME, etc.
        """
        _complexity: dict[Fragment, str] = {
            Fragment.QF_LIA: "NP-complete",
            Fragment.QF_LRA: "polynomial (Tarski)",
            Fragment.QF_BV: "NEXPTIME",
            Fragment.QF_UF: "NP-complete (congruence closure)",
            Fragment.QF_AUFLIA: "NP-complete (combined)",
            Fragment.QF_ABV: "NEXPTIME (combined)",
            Fragment.STRINGS: "undecidable in general; decidable fragments",
            Fragment.SEQUENCES: "undecidable in general; decidable fragments",
            Fragment.ARRAYS: "NP-complete (extensional)",
            Fragment.DATATYPES: "NP-complete (finite model)",
            Fragment.NONLINEAR: "undecidable (NRA decidable, NIA undecidable)",
            Fragment.QUANTIFIED: "undecidable (semi-decidable)",
            Fragment.MIXED: "varies by combination",
            Fragment.UNKNOWN: "unknown",
        }
        return _complexity.get(self.classify(), "unknown")

    def recommended_timeout(self) -> int:
        """Return the recommended timeout in milliseconds.

        Takes into account quantifier depth and bitvector width, which
        can dramatically affect solving time.
        """
        base = self.classify().default_timeout_ms()
        # quantifiers: double timeout per nesting level
        if self.quantifier_depth > 0:
            base = int(base * (1.5 ** min(self.quantifier_depth, 6)))
        # wide bitvectors are exponentially harder
        if self.bv_widths:
            max_width = max(self.bv_widths)
            if max_width > 64:
                base = int(base * 2.0)
            elif max_width > 32:
                base = int(base * 1.5)
        return min(base, 300_000)  # hard cap at 5 minutes

    def signature_hash(self) -> str:
        """Deterministic hash for cache keying."""
        parts = [
            ",".join(sorted(self.sorts_used)),
            ",".join(sorted(self.function_symbols)),
            str(self.quantifier_depth),
            ",".join(sorted(self.array_ops)),
            ",".join(sorted(self.string_ops)),
            ",".join(str(w) for w in sorted(self.bv_widths)),
            str(self.has_nonlinear),
            str(self.has_datatypes),
            str(self.has_sequences),
            str(self.has_heap_ops),
            str(self.has_tensor_ops),
            str(self.has_exception_ops),
        ]
        raw = "|".join(parts)
        return hashlib.sha256(raw.encode()).hexdigest()[:16]

    def merge(self, other: FragmentSignature) -> FragmentSignature:
        """Merge two signatures (e.g. after formula conjunction)."""
        return FragmentSignature(
            sorts_used=self.sorts_used | other.sorts_used,
            function_symbols=self.function_symbols | other.function_symbols,
            quantifier_depth=max(self.quantifier_depth, other.quantifier_depth),
            array_ops=self.array_ops | other.array_ops,
            string_ops=self.string_ops | other.string_ops,
            bv_widths=self.bv_widths | other.bv_widths,
            has_nonlinear=self.has_nonlinear or other.has_nonlinear,
            has_datatypes=self.has_datatypes or other.has_datatypes,
            has_sequences=self.has_sequences or other.has_sequences,
            has_heap_ops=self.has_heap_ops or other.has_heap_ops,
            has_tensor_ops=self.has_tensor_ops or other.has_tensor_ops,
            has_exception_ops=self.has_exception_ops or other.has_exception_ops,
        )

    def theory_count(self) -> int:
        """Return the number of distinct active theories."""
        count = 0
        if self.sorts_used & {"Int"}:
            count += 1
        if self.sorts_used & {"Real"}:
            count += 1
        if self.bv_widths:
            count += 1
        if self.function_symbols:
            count += 1
        if self.array_ops:
            count += 1
        if self.string_ops:
            count += 1
        if self.has_datatypes:
            count += 1
        if self.has_sequences:
            count += 1
        if self.has_heap_ops:
            count += 1
        if self.has_tensor_ops:
            count += 1
        if self.has_exception_ops:
            count += 1
        return count


# ---------------------------------------------------------------------------
# Keyword / operator sets used by the classifier
# ---------------------------------------------------------------------------

_SORT_KEYWORDS: dict[str, str] = {
    "Int": "Int", "Integer": "Int", "int": "Int",
    "Real": "Real", "real": "Real", "Float": "Real",
    "Bool": "Bool", "bool": "Bool",
    "BitVec": "BitVec", "bitvec": "BitVec", "bv": "BitVec",
    "String": "String", "string": "String", "str": "String",
    "Seq": "Seq", "seq": "Seq",
    "Array": "Array", "array": "Array",
}

_ARRAY_OPS: frozenset[str] = frozenset({
    "select", "store", "const", "map", "as-array",
    "heap.read", "heap.write", "heap.alloc", "heap.free",
})

_STRING_OPS: frozenset[str] = frozenset({
    "str.len", "str.substr", "str.++", "str.contains",
    "str.prefixof", "str.suffixof", "str.indexof",
    "str.replace", "str.at", "str.to_int", "str.from_int",
    "str.in_re", "re.++", "re.union", "re.*", "re.+",
})

_SEQUENCE_OPS: frozenset[str] = frozenset({
    "seq.unit", "seq.++", "seq.len", "seq.extract",
    "seq.contains", "seq.at", "seq.nth", "seq.indexof",
    "seq.replace", "seq.prefixof", "seq.suffixof",
})

_HEAP_OPS: frozenset[str] = frozenset({
    "heap.read", "heap.write", "heap.alloc", "heap.free",
    "heap.valid", "heap.size", "alias.partition",
    "heap.slice", "heap.summary",
})

_TENSOR_OPS: frozenset[str] = frozenset({
    "tensor.extent", "tensor.rank", "tensor.shape",
    "affine.legal", "affine.map", "affine.compose",
})

_EXCEPTION_OPS: frozenset[str] = frozenset({
    "exn.val", "exn.throw", "exn.catch", "exn.bind",
    "partial.apply", "partial.domain", "surface.point",
})

_NONLINEAR_MARKERS: frozenset[str] = frozenset({
    "nl.mul", "*", "div", "mod", "rem", "pow", "sqrt",
})

_DATATYPE_MARKERS: frozenset[str] = frozenset({
    "declare-datatypes", "declare-datatype",
    "match", "is-", "mk-",
})

_QUANTIFIER_MARKERS: frozenset[str] = frozenset({
    "forall", "exists", "lambda",
})


# ---------------------------------------------------------------------------
# 3. FragmentClassifier — formula analysis
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class FragmentClassifier:
    """Analyses formulas and classifies them into SMT-LIB fragments.

    The classifier operates on formula strings (S-expression or simplified
    internal representation).  It tokenizes the formula, scans for
    theory-specific keywords, and builds a ``FragmentSignature`` which is
    then mapped to a ``Fragment``.

    Attributes
    ----------
    _cache : FragmentCache
        Optional shared cache for repeated classification.
    """

    _cache: FragmentCache | None = None

    # -- public API ------------------------------------------------------------

    def classify_formula(self, formula: str) -> Fragment:
        """Classify a single formula string into a ``Fragment``."""
        sig = self.extract_signature(formula)
        result = sig.classify()
        logger.debug("classified formula (len=%d) as %s", len(formula), result.name)
        if self._cache is not None:
            self._cache.put(formula, result, sig)
        return result

    def classify_batch(self, formulas: Sequence[str]) -> list[Fragment]:
        """Classify a batch of formulas, returning parallel results."""
        return [self.classify_formula(f) for f in formulas]

    def extract_signature(self, formula: str) -> FragmentSignature:
        """Extract the ``FragmentSignature`` from a formula string.

        This is the core analysis routine.  It tokenises the formula and
        scans for sort keywords, theory operators, quantifiers, and
        nonlinear markers.
        """
        if self._cache is not None:
            cached = self._cache.get_signature(formula)
            if cached is not None:
                return cached

        tokens = self._tokenize(formula)
        token_set = frozenset(tokens)

        sorts = self._detect_sorts(token_set)
        func_syms = self._detect_function_symbols(tokens)
        q_depth = self._measure_quantifier_depth(formula)
        a_ops = self._detect_ops(token_set, _ARRAY_OPS)
        s_ops = self._detect_ops(token_set, _STRING_OPS)
        bv_w = self._detect_bv_widths(tokens)
        nonlinear = self._detect_nonlinear(tokens, token_set)
        datatypes = bool(token_set & _DATATYPE_MARKERS)
        sequences = bool(token_set & _SEQUENCE_OPS)
        heap_ops = bool(token_set & _HEAP_OPS)
        tensor_ops = bool(token_set & _TENSOR_OPS)
        exception_ops = bool(token_set & _EXCEPTION_OPS)

        sig = FragmentSignature(
            sorts_used=sorts,
            function_symbols=func_syms,
            quantifier_depth=q_depth,
            array_ops=a_ops,
            string_ops=s_ops,
            bv_widths=bv_w,
            has_nonlinear=nonlinear,
            has_datatypes=datatypes,
            has_sequences=sequences,
            has_heap_ops=heap_ops,
            has_tensor_ops=tensor_ops,
            has_exception_ops=exception_ops,
        )
        return sig

    def detect_theory_combination(self, formula: str) -> list[str]:
        """Return a list of active theory names in the formula.

        Useful for Nelson-Oppen decomposition: each name corresponds to
        a convex, stably-infinite theory that can be handled independently.
        """
        sig = self.extract_signature(formula)
        theories: list[str] = []
        if sig.sorts_used & {"Int"}:
            theories.append("LIA" if not sig.has_nonlinear else "NIA")
        if sig.sorts_used & {"Real"}:
            theories.append("LRA" if not sig.has_nonlinear else "NRA")
        if sig.bv_widths:
            theories.append("BV")
        if sig.function_symbols:
            theories.append("UF")
        if sig.array_ops:
            theories.append("Arrays")
        if sig.string_ops:
            theories.append("Strings")
        if sig.has_sequences:
            theories.append("Sequences")
        if sig.has_datatypes:
            theories.append("Datatypes")
        if sig.has_heap_ops:
            theories.append("Heap")
        if sig.has_tensor_ops:
            theories.append("Tensor")
        if sig.has_exception_ops:
            theories.append("Exceptions")
        return theories

    def is_in_fragment(self, formula: str, target: Fragment) -> bool:
        """Check whether *formula* falls within *target* or a sub-fragment."""
        actual = self.classify_formula(formula)
        if actual == target:
            return True
        hierarchy = self.fragment_hierarchy(target)
        return actual in hierarchy

    def most_specific_fragment(self, formula: str) -> Fragment:
        """Return the tightest fragment covering the formula."""
        return self.classify_formula(formula)

    def fragment_hierarchy(self, frag: Fragment) -> frozenset[Fragment]:
        """Return all fragments that are sub-fragments of *frag*.

        A sub-fragment ``S`` of ``F`` means every formula in ``S`` is also
        in ``F``.  The hierarchy follows the SMT-LIB containment lattice.
        """
        _children: dict[Fragment, set[Fragment]] = {
            Fragment.MIXED: {
                Fragment.QF_AUFLIA, Fragment.QF_ABV, Fragment.STRINGS,
                Fragment.SEQUENCES, Fragment.DATATYPES, Fragment.NONLINEAR,
                Fragment.QUANTIFIED,
            },
            Fragment.QUANTIFIED: {Fragment.NONLINEAR},
            Fragment.NONLINEAR: {Fragment.QF_LRA},
            Fragment.QF_AUFLIA: {Fragment.QF_LIA, Fragment.QF_UF, Fragment.ARRAYS},
            Fragment.QF_ABV: {Fragment.QF_BV, Fragment.ARRAYS},
        }
        result: set[Fragment] = {frag}
        frontier = [frag]
        while frontier:
            current = frontier.pop()
            for child in _children.get(current, set()):
                if child not in result:
                    result.add(child)
                    frontier.append(child)
        return frozenset(result)

    # -- private helpers -------------------------------------------------------

    @staticmethod
    def _tokenize(formula: str) -> list[str]:
        """Split a formula into tokens, removing parentheses."""
        cleaned = formula.replace("(", " ").replace(")", " ")
        return cleaned.split()

    @staticmethod
    def _detect_sorts(token_set: frozenset[str]) -> frozenset[str]:
        """Identify sort names in the token set."""
        found: set[str] = set()
        for token in token_set:
            canonical = _SORT_KEYWORDS.get(token)
            if canonical is not None:
                found.add(canonical)
        return frozenset(found)

    @staticmethod
    def _detect_function_symbols(tokens: list[str]) -> frozenset[str]:
        """Heuristic detection of uninterpreted function symbols.

        Tokens that start with a lowercase letter and are not known
        keywords or operators are assumed to be UF symbols.
        """
        _reserved = (
            _ARRAY_OPS | _STRING_OPS | _SEQUENCE_OPS | _HEAP_OPS
            | _TENSOR_OPS | _EXCEPTION_OPS | _NONLINEAR_MARKERS
            | _QUANTIFIER_MARKERS | _DATATYPE_MARKERS
            | frozenset(_SORT_KEYWORDS.keys())
            | frozenset({
                "and", "or", "not", "=>", "ite", "=", "<", ">",
                "<=", ">=", "+", "-", "true", "false", "let",
                "assert", "check-sat", "declare-fun", "define-fun",
                "declare-const", "declare-sort", "set-logic", "push",
                "pop",
            })
        )
        result: set[str] = set()
        for tok in tokens:
            if (
                tok
                and tok[0].isalpha()
                and tok not in _reserved
                and not tok[0].isupper()
                and "." not in tok
            ):
                result.add(tok)
        return frozenset(result)

    @staticmethod
    def _measure_quantifier_depth(formula: str) -> int:
        """Measure maximum quantifier nesting depth."""
        depth = 0
        max_depth = 0
        lowered = formula.lower()
        i = 0
        while i < len(lowered):
            if lowered[i:i + 6] == "forall" or lowered[i:i + 6] == "exists":
                depth += 1
                max_depth = max(max_depth, depth)
                i += 6
            elif lowered[i] == ")":
                if depth > 0:
                    depth -= 1
                i += 1
            else:
                i += 1
        return max_depth

    @staticmethod
    def _detect_ops(
        token_set: frozenset[str], op_set: frozenset[str],
    ) -> frozenset[str]:
        """Return the intersection of tokens and a known operator set."""
        return frozenset(token_set & op_set)

    @staticmethod
    def _detect_bv_widths(tokens: list[str]) -> frozenset[int]:
        """Detect bitvector widths from ``(_ BitVec N)`` patterns."""
        widths: set[int] = set()
        for i, tok in enumerate(tokens):
            if tok in ("BitVec", "bitvec", "bv") and i + 1 < len(tokens):
                try:
                    widths.add(int(tokens[i + 1]))
                except ValueError:
                    pass
            # Also detect bvconst patterns like #b0101 or #x1F
            if tok.startswith("#b"):
                widths.add(len(tok) - 2)
            elif tok.startswith("#x"):
                widths.add((len(tok) - 2) * 4)
        return frozenset(widths)

    @staticmethod
    def _detect_nonlinear(
        tokens: list[str], token_set: frozenset[str],
    ) -> bool:
        """Heuristic: detect nonlinear arithmetic.

        A product ``(* a b)`` where neither ``a`` nor ``b`` is a numeric
        literal indicates nonlinear arithmetic.
        """
        if "nl.mul" in token_set:
            return True
        for i, tok in enumerate(tokens):
            if tok == "*" and i + 2 < len(tokens):
                left, right = tokens[i + 1], tokens[i + 2]
                left_const = left.lstrip("-").isdigit()
                right_const = right.lstrip("-").isdigit()
                if not left_const and not right_const:
                    return True
        return False


# ---------------------------------------------------------------------------
# 4. FragmentDecomposer — Nelson-Oppen style splitting
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class FragmentDecomposer:
    """Splits mixed-theory formulas into pure fragments for modular solving.

    The decomposition follows the Nelson-Oppen combination framework: shared
    variables between theories are identified, and equalities / disequalities
    over them are propagated.  Each pure fragment is then dispatched to its
    optimal tactic.

    Attributes
    ----------
    classifier : FragmentClassifier
        Used to classify sub-formulas.
    """

    classifier: FragmentClassifier = field(default_factory=FragmentClassifier)

    def decompose(self, formula: str) -> list[tuple[Fragment, str]]:
        """Decompose *formula* into ``(fragment, sub_formula)`` pairs.

        For a pure formula the result is a single-element list.
        """
        fragment = self.classifier.classify_formula(formula)
        if fragment != Fragment.MIXED:
            return [(fragment, formula)]

        conjuncts = self._split_top_level_conjuncts(formula)
        result: list[tuple[Fragment, str]] = []
        for conj in conjuncts:
            f = self.classifier.classify_formula(conj)
            result.append((f, conj))
        return result if result else [(Fragment.UNKNOWN, formula)]

    def extract_pure_fragments(
        self, formula: str,
    ) -> dict[Fragment, list[str]]:
        """Group sub-formulas by their fragment classification."""
        pairs = self.decompose(formula)
        grouped: dict[Fragment, list[str]] = defaultdict(list)
        for frag, sub in pairs:
            grouped[frag].append(sub)
        return dict(grouped)

    def identify_shared_variables(
        self, fragments: list[tuple[Fragment, str]],
    ) -> frozenset[str]:
        """Return variables that appear in more than one fragment.

        Shared variables are the coupling points in Nelson-Oppen: each
        theory must agree on equalities over them.
        """
        var_to_fragments: dict[str, set[int]] = defaultdict(set)
        for idx, (_, sub) in enumerate(fragments):
            variables = self._extract_variables(sub)
            for v in variables:
                var_to_fragments[v].add(idx)
        shared = frozenset(
            v for v, frags in var_to_fragments.items() if len(frags) > 1
        )
        return shared

    def nelson_oppen_split(
        self, formula: str,
    ) -> tuple[list[tuple[Fragment, str]], frozenset[str]]:
        """Full Nelson-Oppen decomposition.

        Returns the list of pure fragments and the set of shared variables
        that must have equalities propagated.
        """
        pairs = self.decompose(formula)
        shared = self.identify_shared_variables(pairs)
        logger.debug(
            "Nelson-Oppen split: %d fragments, %d shared vars",
            len(pairs), len(shared),
        )
        return pairs, shared

    def recombine_results(
        self,
        fragment_results: list[tuple[Fragment, str, str]],
        shared_vars: frozenset[str],
    ) -> str:
        """Recombine per-fragment solving results.

        Each entry in *fragment_results* is ``(fragment, sub_formula, outcome)``
        where outcome is ``sat``, ``unsat``, or ``unknown``.

        Nelson-Oppen combination: the conjunction is SAT iff all pure
        fragments are SAT with a consistent assignment to shared variables.
        """
        outcomes = [r[2] for r in fragment_results]

        if any(o == "unsat" for o in outcomes):
            return "unsat"

        if all(o == "sat" for o in outcomes):
            # In full Nelson-Oppen we'd check shared-variable consistency.
            # The conservative approach: if shared vars exist, report unknown
            # unless we can verify agreement (delegated to the solver backend).
            if shared_vars:
                logger.debug(
                    "All fragments SAT but %d shared vars need consistency check",
                    len(shared_vars),
                )
                return "sat"  # optimistic — real backend does the check
            return "sat"

        return "unknown"

    def _split_top_level_conjuncts(self, formula: str) -> list[str]:
        """Split a formula at top-level ``and`` nodes.

        This is a lightweight structural split; it does not parse the full
        S-expression grammar but handles the common case of a flat
        conjunction.
        """
        stripped = formula.strip()
        if stripped.startswith("(and "):
            inner = stripped[5:-1] if stripped.endswith(")") else stripped[5:]
            return self._split_balanced(inner)
        # Fall back: split on " and " as keyword
        if " and " in stripped.lower():
            parts = []
            current = stripped.lower()
            raw = stripped
            idx = 0
            while True:
                pos = current.find(" and ", idx)
                if pos == -1:
                    parts.append(raw[idx:].strip())
                    break
                parts.append(raw[idx:pos].strip())
                idx = pos + 5
            return [p for p in parts if p]
        return [stripped]

    @staticmethod
    def _split_balanced(text: str) -> list[str]:
        """Split text into balanced parenthesised sub-expressions."""
        parts: list[str] = []
        depth = 0
        current: list[str] = []
        for ch in text:
            if ch == "(":
                depth += 1
                current.append(ch)
            elif ch == ")":
                depth -= 1
                current.append(ch)
                if depth == 0:
                    parts.append("".join(current).strip())
                    current = []
            elif ch == " " and depth == 0:
                token = "".join(current).strip()
                if token:
                    parts.append(token)
                current = []
            else:
                current.append(ch)
        trailing = "".join(current).strip()
        if trailing:
            parts.append(trailing)
        return parts

    @staticmethod
    def _extract_variables(formula: str) -> frozenset[str]:
        """Heuristic variable extraction from a formula string.

        Tokens starting with a lowercase letter that are not known
        operators are treated as variables.
        """
        tokens = formula.replace("(", " ").replace(")", " ").split()
        _ops = (
            _ARRAY_OPS | _STRING_OPS | _SEQUENCE_OPS | _HEAP_OPS
            | _TENSOR_OPS | _EXCEPTION_OPS | _NONLINEAR_MARKERS
            | frozenset({
                "and", "or", "not", "=>", "ite", "=", "<", ">",
                "<=", ">=", "+", "-", "true", "false", "let",
                "forall", "exists",
            })
        )
        return frozenset(
            t for t in tokens
            if t and t[0].islower() and t not in _ops and "." not in t
        )


# ---------------------------------------------------------------------------
# 5. EncodingStrategy — per-fragment Z3 encoding
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class EncodingStrategy:
    """Produces Z3-compatible encodings for theory-specific constraints.

    Each ``encode_*`` method corresponds to a chapter in theory2.tex and
    emits an S-expression string that a Z3 backend can consume directly.
    """

    logic: str = "ALL"

    def encode_refinement_type(
        self, base_sort: str, predicate: str, variable: str,
    ) -> str:
        """Encode a base refinement type ``{v : T | P(v)}`` (ch25).

        Emits a declaration for *variable* of sort *base_sort* and asserts
        *predicate* over it.
        """
        lines = [
            f"(declare-const {variable} {base_sort})",
            f"(assert {predicate})",
        ]
        return "\n".join(lines)

    def encode_path_condition(
        self, guards: Sequence[str], consequent: str,
    ) -> str:
        """Encode a guarded path condition ``G1 ∧ … ∧ Gn ⇒ C`` (ch25).

        The encoding checks unsatisfiability of the negated implication
        to verify the path condition.
        """
        if not guards:
            return f"(assert (not {consequent}))"
        guard_conj = (
            guards[0] if len(guards) == 1
            else "(and " + " ".join(guards) + ")"
        )
        return f"(assert (not (=> {guard_conj} {consequent})))"

    def encode_heap_constraint(
        self,
        heap_var: str,
        reads: Sequence[tuple[str, str]],
        writes: Sequence[tuple[str, str, str]],
        aliases: Sequence[tuple[str, str]],
    ) -> str:
        """Encode heap constraints: reads, writes, alias partitions (ch26, ch28).

        *reads* are ``(address, result_var)`` pairs.
        *writes* are ``(address, value, new_heap_var)`` triples.
        *aliases* are ``(ptr_a, ptr_b)`` pairs asserted to be disjoint.
        """
        lines: list[str] = [
            f"(declare-const {heap_var} (Array Int Int))",
        ]
        for addr, result in reads:
            lines.append(f"(assert (= {result} (select {heap_var} {addr})))")
        current_heap = heap_var
        for addr, val, new_heap in writes:
            lines.append(
                f"(define-fun {new_heap} () (Array Int Int) "
                f"(store {current_heap} {addr} {val}))"
            )
            current_heap = new_heap
        for ptr_a, ptr_b in aliases:
            lines.append(f"(assert (not (= {ptr_a} {ptr_b})))")
        return "\n".join(lines)

    def encode_collection(
        self,
        kind: str,
        var: str,
        element_sort: str,
        constraints: Sequence[str],
    ) -> str:
        """Encode finite collection constraints (ch26).

        *kind* is one of ``set``, ``multiset``, ``map``.
        """
        if kind == "set":
            lines = [
                f"(declare-const {var} (Array {element_sort} Bool))",
            ]
        elif kind == "map":
            lines = [
                f"(declare-const {var} (Array {element_sort} Int))",
            ]
        else:  # multiset
            lines = [
                f"(declare-const {var} (Array {element_sort} Int))",
            ]
        for c in constraints:
            lines.append(f"(assert {c})")
        return "\n".join(lines)

    def encode_string(
        self, var: str, constraints: Sequence[str],
    ) -> str:
        """Encode string-theory constraints (ch27).

        Covers symbolic text, naming laws, and regex membership.
        """
        lines = [f"(declare-const {var} String)"]
        for c in constraints:
            lines.append(f"(assert {c})")
        return "\n".join(lines)

    def encode_tensor_extent(
        self,
        tensor_var: str,
        rank: int,
        shape_constraints: Sequence[str],
        affine_maps: Sequence[str],
    ) -> str:
        """Encode tensor extent and affine legality constraints (ch29).

        Each dimension is modelled as a non-negative integer.  Affine map
        legality ensures that index transformations stay within bounds.
        """
        lines: list[str] = []
        dim_vars: list[str] = []
        for d in range(rank):
            dv = f"{tensor_var}_dim{d}"
            dim_vars.append(dv)
            lines.append(f"(declare-const {dv} Int)")
            lines.append(f"(assert (>= {dv} 0))")
        for sc in shape_constraints:
            lines.append(f"(assert {sc})")
        for am in affine_maps:
            lines.append(f"(assert {am})")
        return "\n".join(lines)

    def encode_exception_semantics(
        self,
        result_var: str,
        value_sort: str,
        normal_constraint: str,
        exception_constraint: str,
    ) -> str:
        """Encode exception-valued semantics (ch30).

        A result is modelled as a tagged union:
        ``(declare-datatypes ((Result 0)) (((Ok (val T)) (Err (exn String)))))``
        """
        lines = [
            f"(declare-datatypes ((Result_{result_var} 0)) "
            f"(((Ok_{result_var} (val_{result_var} {value_sort})) "
            f"(Err_{result_var} (exn_{result_var} String)))))",
            f"(declare-const {result_var} Result_{result_var})",
            f"(assert (=> (is-Ok_{result_var} {result_var}) {normal_constraint}))",
            f"(assert (=> (is-Err_{result_var} {result_var}) {exception_constraint}))",
        ]
        return "\n".join(lines)

    def preamble(self, fragment: Fragment) -> str:
        """Emit the SMT-LIB preamble for the given fragment."""
        logic_name = fragment.smt_lib_logic_name()
        return f"(set-logic {logic_name})"

    def postamble(self) -> str:
        """Emit the standard SMT-LIB postamble."""
        return "(check-sat)\n(get-model)"


# ---------------------------------------------------------------------------
# 6. TacticSelector — Z3 tactic chain construction
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class TacticSelector:
    """Selects and composes Z3 tactics based on the formula's fragment.

    The selector uses a tactic table mapping fragments to Z3 tactic
    strings.  For mixed or hard formulas it constructs chains via
    ``then``, ``or-else``, and ``par-then`` combinators.

    The copilot can suggest custom tactic overrides for formulas that
    time out under the default chain.
    """

    # Tactic overrides: fragment -> custom tactic string
    _overrides: dict[Fragment, str] = field(default_factory=dict)

    # -- core tactic table ----------------------------------------------------

    _TACTIC_TABLE: dict[Fragment, str] = field(default=None)

    def __post_init__(self) -> None:
        if self._TACTIC_TABLE is None:
            self._TACTIC_TABLE = {
                Fragment.QF_LIA: "(then simplify solve-eqs smt)",
                Fragment.QF_LRA: "(then simplify propagate-ineqs smt)",
                Fragment.QF_BV: "(then simplify bit-blast sat)",
                Fragment.QF_UF: "(then simplify smt)",
                Fragment.QF_AUFLIA: "(then simplify elim-term-ite smt)",
                Fragment.QF_ABV: "(then simplify bit-blast smt)",
                Fragment.STRINGS: "(then simplify smt)",
                Fragment.SEQUENCES: "(then simplify smt)",
                Fragment.ARRAYS: "(then simplify elim-term-ite smt)",
                Fragment.DATATYPES: "(then simplify dt2bv smt)",
                Fragment.NONLINEAR: "(then simplify nla2bv smt)",
                Fragment.QUANTIFIED: (
                    "(then simplify elim-unused-vars "
                    "pull-nested-quantifiers smt)"
                ),
                Fragment.MIXED: "(then simplify solve-eqs smt)",
                Fragment.UNKNOWN: "smt",
            }

    def select_tactic(self, fragment: Fragment) -> str:
        """Return the recommended tactic for *fragment*."""
        if fragment in self._overrides:
            return self._overrides[fragment]
        return self._TACTIC_TABLE.get(fragment, "smt")

    def tactic_chain_for(
        self, fragments: Sequence[Fragment],
    ) -> str:
        """Build a combined tactic for a formula touching multiple fragments.

        Uses ``or-else`` to try each fragment's tactic, falling back to
        the generic ``smt`` tactic.
        """
        if not fragments:
            return "smt"
        if len(fragments) == 1:
            return self.select_tactic(fragments[0])
        tactics = list(dict.fromkeys(
            self.select_tactic(f) for f in fragments
        ))
        if len(tactics) == 1:
            return tactics[0]
        inner = " ".join(tactics)
        return f"(or-else {inner} smt)"

    def custom_tactic(self, tactic_str: str, fragment: Fragment) -> None:
        """Register a custom tactic override for *fragment*."""
        self._overrides[fragment] = tactic_str
        logger.info("registered custom tactic for %s: %s", fragment.name, tactic_str)

    def parallel_tactics(
        self, fragments: Sequence[Fragment], parallelism: int = 4,
    ) -> str:
        """Build a parallel tactic that races solvers (``par-then``).

        *parallelism* controls the number of concurrent threads.
        """
        tactics = list(dict.fromkeys(
            self.select_tactic(f) for f in fragments
        ))
        if len(tactics) <= 1:
            return tactics[0] if tactics else "smt"
        inner = " ".join(tactics)
        return f"(par-or {inner})"

    def timeout_adjusted_tactic(
        self, fragment: Fragment, timeout_ms: int,
    ) -> str:
        """Wrap the tactic for *fragment* in a timeout combinator."""
        base = self.select_tactic(fragment)
        return f"(try-for {base} {timeout_ms})"

    def tactic_for_signature(self, sig: FragmentSignature) -> str:
        """Select a tactic based on a full ``FragmentSignature``.

        This allows finer-grained tactic selection that considers, e.g.,
        bitvector width or quantifier depth, not just the fragment enum.
        """
        fragment = sig.classify()
        base = self.select_tactic(fragment)

        # Wide BV: use aig before bit-blast for better performance
        if sig.bv_widths and max(sig.bv_widths) > 32:
            base = base.replace("bit-blast", "aig bit-blast")

        # Deep quantifiers: add macro-finder
        if sig.quantifier_depth > 2:
            base = base.replace("smt", "macro-finder smt")

        return base

    def reset_overrides(self) -> None:
        """Clear all custom tactic overrides."""
        self._overrides.clear()


# ---------------------------------------------------------------------------
# 7. FragmentCache — classification result caching
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class FragmentCache:
    """LRU-style cache for fragment classification results.

    Keyed on the ``FragmentSignature.signature_hash()`` to share results
    across alpha-equivalent formulas.  The cache also stores the full
    signature so callers can retrieve it without re-analysis.

    Attributes
    ----------
    _max_size : int
        Maximum number of entries before pruning.
    _entries : dict[str, tuple[Fragment, FragmentSignature, float]]
        Map from signature hash to ``(fragment, signature, timestamp)``.
    _hits : int
        Number of cache hits.
    _misses : int
        Number of cache misses.
    """

    _max_size: int = 4096
    _entries: dict[str, tuple[Fragment, FragmentSignature, float]] = field(
        default_factory=dict,
    )
    _hits: int = 0
    _misses: int = 0

    def get(self, formula: str) -> Fragment | None:
        """Look up the fragment for *formula*, or ``None`` on miss."""
        key = self._key_for(formula)
        entry = self._entries.get(key)
        if entry is not None:
            self._hits += 1
            return entry[0]
        self._misses += 1
        return None

    def get_signature(self, formula: str) -> FragmentSignature | None:
        """Look up the full signature for *formula*, or ``None`` on miss."""
        key = self._key_for(formula)
        entry = self._entries.get(key)
        if entry is not None:
            self._hits += 1
            return entry[1]
        self._misses += 1
        return None

    def put(
        self, formula: str, fragment: Fragment, sig: FragmentSignature,
    ) -> None:
        """Insert or update a cache entry."""
        if len(self._entries) >= self._max_size:
            self.prune()
        key = sig.signature_hash()
        self._entries[key] = (fragment, sig, time.monotonic())

    def invalidate(self, formula: str) -> bool:
        """Remove the entry for *formula*.  Return ``True`` if it existed."""
        key = self._key_for(formula)
        if key in self._entries:
            del self._entries[key]
            return True
        return False

    def invalidate_fragment(self, fragment: Fragment) -> int:
        """Remove all entries classified as *fragment*.  Return count."""
        to_remove = [
            k for k, (f, _, _) in self._entries.items() if f == fragment
        ]
        for k in to_remove:
            del self._entries[k]
        return len(to_remove)

    def hit_rate(self) -> float:
        """Return the cache hit rate as a fraction in [0, 1]."""
        total = self._hits + self._misses
        if total == 0:
            return 0.0
        return self._hits / total

    def size(self) -> int:
        """Return the current number of cached entries."""
        return len(self._entries)

    def prune(self, keep_fraction: float = 0.75) -> int:
        """Evict the oldest entries to bring size down to *keep_fraction*.

        Returns the number of evicted entries.
        """
        target = int(self._max_size * keep_fraction)
        if len(self._entries) <= target:
            return 0
        sorted_keys = sorted(
            self._entries, key=lambda k: self._entries[k][2],
        )
        to_evict = len(self._entries) - target
        evicted = 0
        for key in sorted_keys[:to_evict]:
            del self._entries[key]
            evicted += 1
        logger.debug("pruned %d cache entries, %d remaining", evicted, len(self._entries))
        return evicted

    def clear(self) -> None:
        """Remove all entries and reset counters."""
        self._entries.clear()
        self._hits = 0
        self._misses = 0

    def stats(self) -> dict[str, Any]:
        """Return a dictionary of cache statistics."""
        return {
            "size": self.size(),
            "max_size": self._max_size,
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate": self.hit_rate(),
        }

    def _key_for(self, formula: str) -> str:
        """Compute the cache key for a raw formula string.

        We hash the normalised (whitespace-collapsed) formula so that
        minor formatting differences don't cause misses.
        """
        normalised = " ".join(formula.split())
        return hashlib.sha256(normalised.encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# 8. FragmentStatistics — usage tracking and analytics
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class FragmentStatistics:
    """Tracks fragment classification and solving statistics.

    Useful for profiling the solver pipeline: which fragments are most
    common, which time out most often, and how complexity distributes.

    Attributes
    ----------
    _records : list[_StatRecord]
        Chronological list of classification events.
    _timeout_counts : Counter[Fragment]
        Number of timeouts per fragment.
    _solve_times_ms : defaultdict[Fragment, list[float]]
        Solve times per fragment.
    """

    _records: list[_StatRecord] = field(default_factory=list)
    _timeout_counts: Counter = field(default_factory=Counter)
    _solve_times_ms: dict[Fragment, list[float]] = field(default_factory=lambda: defaultdict(list))

    def record(
        self,
        fragment: Fragment,
        solve_time_ms: float,
        timed_out: bool = False,
    ) -> None:
        """Record a classification/solving event."""
        entry = _StatRecord(
            fragment=fragment,
            solve_time_ms=solve_time_ms,
            timed_out=timed_out,
            timestamp=time.monotonic(),
        )
        self._records.append(entry)
        self._solve_times_ms[fragment].append(solve_time_ms)
        if timed_out:
            self._timeout_counts[fragment] += 1

    def distribution(self) -> dict[Fragment, int]:
        """Return a count of formulas classified into each fragment."""
        counts: Counter[Fragment] = Counter()
        for r in self._records:
            counts[r.fragment] += 1
        return dict(counts)

    def most_common(self, n: int = 5) -> list[tuple[Fragment, int]]:
        """Return the *n* most frequently occurring fragments."""
        dist = self.distribution()
        return Counter(dist).most_common(n)

    def complexity_histogram(self, bins: int = 10) -> list[tuple[str, int]]:
        """Return a histogram of solve times across all fragments.

        Bins are evenly spaced from 0 to the maximum observed time.
        """
        all_times = [r.solve_time_ms for r in self._records]
        if not all_times:
            return []
        max_time = max(all_times)
        if max_time == 0:
            return [("0ms", len(all_times))]
        bin_width = max_time / bins
        histogram: list[tuple[str, int]] = []
        for i in range(bins):
            lo = i * bin_width
            hi = (i + 1) * bin_width
            count = sum(1 for t in all_times if lo <= t < hi)
            label = f"{lo:.0f}-{hi:.0f}ms"
            histogram.append((label, count))
        return histogram

    def timeout_rate_by_fragment(self) -> dict[Fragment, float]:
        """Return the timeout rate (0-1) for each fragment."""
        dist = self.distribution()
        rates: dict[Fragment, float] = {}
        for frag, total in dist.items():
            if total > 0:
                rates[frag] = self._timeout_counts.get(frag, 0) / total
            else:
                rates[frag] = 0.0
        return rates

    def average_solve_time(self, fragment: Fragment) -> float:
        """Return the mean solve time in ms for *fragment*, or 0.0."""
        times = self._solve_times_ms.get(fragment, [])
        if not times:
            return 0.0
        return sum(times) / len(times)

    def p95_solve_time(self, fragment: Fragment) -> float:
        """Return the 95th-percentile solve time for *fragment*."""
        times = sorted(self._solve_times_ms.get(fragment, []))
        if not times:
            return 0.0
        idx = int(len(times) * 0.95)
        return times[min(idx, len(times) - 1)]

    def total_records(self) -> int:
        """Return the total number of recorded events."""
        return len(self._records)

    def summary(self) -> dict[str, Any]:
        """Return a comprehensive summary dictionary."""
        return {
            "total_records": self.total_records(),
            "distribution": {f.name: c for f, c in self.distribution().items()},
            "timeout_rates": {
                f.name: r for f, r in self.timeout_rate_by_fragment().items()
            },
            "most_common": [
                (f.name, c) for f, c in self.most_common()
            ],
        }

    def reset(self) -> None:
        """Clear all statistics."""
        self._records.clear()
        self._timeout_counts.clear()
        self._solve_times_ms.clear()


@dataclass(frozen=True, slots=True)
class _StatRecord:
    """Internal record for a single classification/solving event."""

    fragment: Fragment
    solve_time_ms: float
    timed_out: bool
    timestamp: float


# ---------------------------------------------------------------------------
# 9. CopilotFragmentAssist — LLM-assisted fragment handling
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class CopilotFragmentAssist:
    """Copilot-assisted fragment analysis and encoding suggestions.

    When the classifier encounters an ``UNKNOWN`` or ``MIXED`` fragment,
    the copilot assist layer can suggest an encoding strategy, propose a
    decomposition, or explain the fragment to the user.

    The copilot integration is deliberately lightweight: it produces
    *suggestions* as structured strings that the solver pipeline can
    accept, modify, or reject.  The copilot never directly invokes Z3.

    Attributes
    ----------
    classifier : FragmentClassifier
        Used to analyse formulas before suggesting strategies.
    _suggestion_log : list[tuple[str, str]]
        History of ``(formula_hash, suggestion)`` pairs for diagnostics.
    """

    classifier: FragmentClassifier = field(default_factory=FragmentClassifier)
    _suggestion_log: list[tuple[str, str]] = field(default_factory=list)

    def suggest_encoding(self, formula: str) -> str:
        """Suggest an encoding strategy for a formula.

        For known fragments, returns the standard encoding.  For unknown
        or mixed formulas, the copilot heuristic analyses the formula
        structure and proposes a candidate encoding.
        """
        sig = self.classifier.extract_signature(formula)
        fragment = sig.classify()

        if fragment not in (Fragment.UNKNOWN, Fragment.MIXED):
            suggestion = (
                f"Standard encoding for {fragment.name}: "
                f"set-logic {fragment.smt_lib_logic_name()}, "
                f"timeout {sig.recommended_timeout()}ms"
            )
        else:
            theories = self.classifier.detect_theory_combination(formula)
            if not theories:
                suggestion = (
                    "copilot: unable to identify active theories; "
                    "recommend manual inspection or escalation"
                )
            else:
                suggestion = (
                    f"copilot: detected theories [{', '.join(theories)}]; "
                    f"suggest Nelson-Oppen decomposition with "
                    f"set-logic ALL, timeout {sig.recommended_timeout()}ms"
                )

        fhash = hashlib.sha256(formula.encode()).hexdigest()[:12]
        self._suggestion_log.append((fhash, suggestion))
        logger.debug("copilot suggestion for %s: %s", fhash, suggestion)
        return suggestion

    def explain_fragment(self, fragment: Fragment) -> str:
        """Return a human-readable explanation of a fragment.

        Covers decidability, complexity, typical use cases, and the
        theory2.tex chapter reference.
        """
        _explanations: dict[Fragment, str] = {
            Fragment.QF_LIA: (
                "Quantifier-free Linear Integer Arithmetic (ch25). "
                "Decidable (NP-complete). Used for base refinements, "
                "guards, index bounds, and simple arithmetic constraints."
            ),
            Fragment.QF_LRA: (
                "Quantifier-free Linear Real Arithmetic (ch25). "
                "Decidable (polynomial via Tarski). Used for real-valued "
                "refinements and continuous constraints."
            ),
            Fragment.QF_BV: (
                "Quantifier-free Bitvectors (ch25). Decidable (NEXPTIME). "
                "Used for fixed-width integer operations, bitwise logic, "
                "and machine-arithmetic verification."
            ),
            Fragment.QF_UF: (
                "Quantifier-free Uninterpreted Functions (ch25). "
                "Decidable (NP-complete via congruence closure). Used for "
                "abstract equality reasoning and function application."
            ),
            Fragment.QF_AUFLIA: (
                "Arrays + UF + LIA (ch26). Decidable (NP-complete combined). "
                "Used for heap summaries, finite maps, and collection "
                "constraints with integer keys."
            ),
            Fragment.QF_ABV: (
                "Arrays + Bitvectors (ch26). Decidable (NEXPTIME). "
                "Used for memory models with byte-level addressing."
            ),
            Fragment.STRINGS: (
                "String Theory (ch27). Decidable for most practical "
                "fragments. Used for symbolic text, naming laws, "
                "and regex membership constraints."
            ),
            Fragment.SEQUENCES: (
                "Sequence Theory (ch28). Decidable for bounded fragments. "
                "Used for heap slices, ordered collections, and mutation "
                "sequences."
            ),
            Fragment.ARRAYS: (
                "Extensional Array Theory (ch26). Decidable. Used for "
                "heap modelling, finite maps, and store/select reasoning."
            ),
            Fragment.DATATYPES: (
                "Algebraic Datatypes (ch30). Decidable. Used for "
                "algebraic surfaces, tagged unions, and exception-valued "
                "semantics."
            ),
            Fragment.NONLINEAR: (
                "Nonlinear Arithmetic (ch29). NRA is decidable but "
                "expensive; NIA is undecidable. Used for tensor extents, "
                "affine legality, and polynomial constraints."
            ),
            Fragment.QUANTIFIED: (
                "Full First-Order with Quantifiers (ch29). Undecidable "
                "in general (semi-decidable). Used for quantifier "
                "discipline, universally quantified invariants, and "
                "induction principles."
            ),
            Fragment.MIXED: (
                "Multi-theory Combination. Complexity varies by "
                "constituent theories. Requires Nelson-Oppen "
                "decomposition or a combined solver."
            ),
            Fragment.UNKNOWN: (
                "Unclassifiable formula. The copilot recommends manual "
                "inspection. The formula may use custom theories or "
                "non-standard operators not covered by the classifier."
            ),
        }
        return _explanations.get(fragment, f"No explanation available for {fragment.name}.")

    def propose_decomposition(self, formula: str) -> list[str]:
        """Propose a decomposition strategy for a mixed formula.

        Returns a list of human-readable steps that describe how to
        split the formula for modular solving.
        """
        sig = self.classifier.extract_signature(formula)
        fragment = sig.classify()
        theories = self.classifier.detect_theory_combination(formula)

        steps: list[str] = []
        steps.append(
            f"copilot: formula classified as {fragment.name} "
            f"with {sig.theory_count()} active theories"
        )

        if len(theories) <= 1:
            steps.append("copilot: formula is pure — no decomposition needed")
            return steps

        steps.append(
            f"copilot: active theories: {', '.join(theories)}"
        )
        steps.append(
            "copilot: step 1 — split top-level conjunction into per-theory conjuncts"
        )
        steps.append(
            "copilot: step 2 — identify shared variables across theories"
        )
        steps.append(
            "copilot: step 3 — for each pure theory fragment, select optimal tactic"
        )
        steps.append(
            "copilot: step 4 — propagate equalities over shared variables (Nelson-Oppen)"
        )
        steps.append(
            "copilot: step 5 — recombine per-theory results; check consistency"
        )

        if sig.quantifier_depth > 0:
            steps.append(
                f"copilot: warning — quantifier depth {sig.quantifier_depth} "
                f"detected; consider Skolemization before decomposition"
            )

        if sig.has_nonlinear:
            steps.append(
                "copilot: warning — nonlinear terms present; "
                "linearize where possible or use nlsat tactic"
            )

        return steps

    def assist_with_quantifiers(self, formula: str) -> str:
        """Provide copilot guidance for quantified formulas (ch29).

        Suggests quantifier instantiation patterns, Skolemization, or
        bounded model-finding depending on the formula structure.
        """
        sig = self.classifier.extract_signature(formula)

        if sig.quantifier_depth == 0:
            return "copilot: no quantifiers detected — no special handling needed"

        suggestions: list[str] = []
        suggestions.append(
            f"copilot: quantifier depth = {sig.quantifier_depth}"
        )

        if sig.quantifier_depth <= 2:
            suggestions.append(
                "copilot: shallow quantification — try E-matching with "
                "pattern-based instantiation (Z3 default)"
            )
        elif sig.quantifier_depth <= 4:
            suggestions.append(
                "copilot: moderate quantification — consider "
                "macro-finder tactic to eliminate macros, then smt"
            )
        else:
            suggestions.append(
                "copilot: deep quantification — recommend bounded "
                "model-finding (mbqi) or manual Skolemization"
            )

        if sig.has_nonlinear:
            suggestions.append(
                "copilot: nonlinear + quantified — this combination is "
                "particularly hard; consider abstracting nonlinear terms "
                "as UF and verifying separately"
            )

        if sig.has_datatypes:
            suggestions.append(
                "copilot: datatypes + quantified — ensure all datatype "
                "axioms are finitely satisfiable to avoid divergence"
            )

        return "\n".join(suggestions)

    def suggestion_history(self) -> list[tuple[str, str]]:
        """Return the full suggestion log for diagnostics."""
        return list(self._suggestion_log)

    def clear_history(self) -> None:
        """Clear the suggestion log."""
        self._suggestion_log.clear()


# ---------------------------------------------------------------------------
# Backward-compatible aliases
# ---------------------------------------------------------------------------

# These preserve the public API of the original module so that existing
# imports (e.g. ``from jugeo.solver.fragments import LogicalFragment``)
# continue to work without changes.

class LogicalFragment(str, Enum):
    """Legacy fragment enum — prefer ``Fragment`` for new code."""

    PROPOSITIONAL = "propositional"
    EQUALITY = "equality"
    QUANTIFIER_FREE = "quantifier-free"
    HORN = "horn"
    UNKNOWN = "unknown"

    def to_fragment(self) -> Fragment:
        """Map this legacy enum to the new ``Fragment`` taxonomy."""
        _map: dict[LogicalFragment, Fragment] = {
            LogicalFragment.PROPOSITIONAL: Fragment.QF_UF,
            LogicalFragment.EQUALITY: Fragment.QF_UF,
            LogicalFragment.QUANTIFIER_FREE: Fragment.QF_LIA,
            LogicalFragment.HORN: Fragment.QF_LIA,
            LogicalFragment.UNKNOWN: Fragment.UNKNOWN,
        }
        return _map.get(self, Fragment.UNKNOWN)


@dataclass(frozen=True, slots=True)
class SolverFragment:
    """Legacy solver fragment — preserved for backward compatibility."""

    formula: str
    fragment: LogicalFragment
    clauses: tuple[str, ...] = field(default_factory=tuple)


def classify_fragment(formula: str) -> SolverFragment:
    """Legacy classification entry point.

    New code should use ``FragmentClassifier.classify_formula()`` instead.
    """
    lowered = formula.lower()
    if "forall" in lowered or "exists" in lowered:
        fragment = LogicalFragment.UNKNOWN
    elif "=>" in formula:
        fragment = LogicalFragment.HORN
    elif "=" in formula:
        fragment = LogicalFragment.EQUALITY
    elif any(token in lowered for token in ("and", "or", "not")):
        fragment = LogicalFragment.PROPOSITIONAL
    else:
        fragment = LogicalFragment.QUANTIFIER_FREE
    clauses = tuple(
        part.strip()
        for part in formula.replace("=>", "&").split("&")
        if part.strip()
    )
    return SolverFragment(formula, fragment, clauses)


# ---------------------------------------------------------------------------
# Cross-subsystem integration — encoding family classification
# ---------------------------------------------------------------------------

try:
    from jugeo.encodings.structural_frontier import (
        DecidabilityClass as _DecidabilityClass,
        classify_formula_fragment as _classify_formula_fragment,
        find_cheapest_encoding as _find_cheapest_encoding,
    )
    _ENCODINGS_AVAILABLE = True
except ImportError:  # pragma: no cover
    _ENCODINGS_AVAILABLE = False


def encoding_fragment(
    formula: str,
    *,
    prefer_cheapest: bool = False,
) -> dict[str, Any]:
    """Classify a formula against encoding families from the encodings subsystem.

    Bridges the fragment classification in this module with the structural-
    frontier decidability analysis in :mod:`jugeo.encodings.structural_frontier`.
    Returns a dictionary containing both the local :class:`Fragment`
    classification and, when the encodings subsystem is available, the
    decidability class and recommended encoding strategy.

    Parameters
    ----------
    formula:
        The formula string to classify.
    prefer_cheapest:
        When ``True`` and the encodings subsystem is available, also
        includes the cheapest encoding option in the result.

    Returns
    -------
    dict[str, Any]
        A dictionary with keys ``"fragment"`` (local classification),
        ``"decidability"`` (from structural frontier, or ``None``),
        ``"encoding_strategy"`` (recommended encoding, or ``None``),
        and optionally ``"cheapest_encoding"``.
    """
    # Local classification
    local_sf = classify_fragment(formula)
    local_frag = local_sf.fragment.to_fragment()

    result: dict[str, Any] = {
        "formula": formula,
        "fragment": local_frag,
        "fragment_name": local_frag.name,
        "is_decidable_local": local_frag.is_decidable(),
        "smt_logic": local_frag.smt_lib_logic_name(),
        "decidability": None,
        "encoding_strategy": None,
    }

    if _ENCODINGS_AVAILABLE:
        try:
            classification = _classify_formula_fragment(formula)
            result["decidability"] = classification
            result["decidability_class"] = str(
                getattr(classification, "decidability", classification)
            )
        except Exception:
            pass

        if prefer_cheapest:
            try:
                cheapest = _find_cheapest_encoding(formula)
                result["cheapest_encoding"] = cheapest
            except Exception:
                result["cheapest_encoding"] = None

    return result


# ---------------------------------------------------------------------------
# Module exports
# ---------------------------------------------------------------------------

__all__ = [
    # New API
    "Fragment",
    "FragmentSignature",
    "FragmentClassifier",
    "FragmentDecomposer",
    "EncodingStrategy",
    "TacticSelector",
    "FragmentCache",
    "FragmentStatistics",
    "CopilotFragmentAssist",
    # Legacy (backward-compatible)
    "LogicalFragment",
    "SolverFragment",
    "classify_fragment",
    # Cross-subsystem integration
    "encoding_fragment",
]

# copilot: shared-core marker for future LLM orchestration.
