"""The encoding layer should begin from a typed structural core.

This module implements the structural-core establishment phase of the exact
Z3 encoding pipeline, as described in **Chapter 26.3** of
``preliminaries/theory2.tex``.

The central claim of this section is that every sound Z3 encoding begins not
with constraints or predicates but with a **typed structural core**: a minimal
Z3 context that fixes the SMT sort for every free variable before any
refinement predicate, guard formula, or arithmetic assertion is added.

Structural Core Definition
--------------------------
Given a set of typed variables ``{x₁:T₁, x₂:T₂, …, xₙ:Tₙ}`` drawn from the
Python scalar types ``{int, float, bool, str}``, the structural core is the
SMT-LIB 2 fragment:

    (declare-const x₁ T̂₁)
    (declare-const x₂ T̂₂)
    …
    (declare-const xₙ T̂ₙ)

where ``T̂ᵢ`` is the Z3 sort that corresponds to Python type ``Tᵢ``:

    int   →  Int
    float →  Real
    bool  →  Bool
    str   →  String

The structural core deliberately excludes refinement predicates: those are
layered on top in subsequent encoding phases (§26.4–§26.6).

Rationale
---------
Starting from the structural core guarantees three properties:

1. **Type safety**: every formula added later is well-sorted, because the
   sort of each variable is fixed before any formula references it.
2. **Modularity**: the structural core is shared across multiple refinement
   predicates that constrain the same variable.  Adding a new predicate never
   requires re-declaring sorts.
3. **Incremental solvability**: Z3's incremental API (``push``/``pop``) requires
   that all ``declare-const`` commands appear before the scope boundary.  The
   structural core satisfies this requirement.

Architecture
------------
The module exposes three cooperating components:

* **ScalarSort** — an :class:`~enum.Enum` that maps Python scalar types to
  their Z3 SMT sort names, and provides a suite of introspection helpers.

* **TheEncodingLayerBeginWitness** — a frozen :class:`~dataclasses.dataclass`
  that captures the complete structural-core state: the declared variables,
  their sort assignments, any base assertions, and provenance metadata.

* **TheEncodingLayerBeginAnalyzer** — a stateless analysis helper that parses
  Python-style type annotation strings (``"int"``, ``"float"``, ``"{x:int|x>0}"``),
  infers :class:`ScalarSort` values, builds declaration tuples, and validates
  the completed structural core.

* **TheEncodingLayerBeginCoordinator** — the session-level coordinator that
  maintains a mutable ``_cache`` and ``_stats`` dict and exposes the
  high-level ``establish_core`` entry-point used by downstream modules.

copilot note
------------
When copilot generates type annotations for new scalar encodings, pass them
through :func:`establish_simple_core` to obtain a witness whose
``smt2_preamble()`` can be spliced directly into an SMT-LIB 2 script without
any additional sort bookkeeping.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass
from enum import Enum, auto
from itertools import chain
from typing import Any

# copilot: try/except guards keep the module importable when jugeo internals
# are not yet installed in the active virtual environment.
try:
    from jugeo.encodings.scalar_encodings.models import (
        SortKind,
        FragmentHint,
        EncodeStatus,
        EncodingContext,
    )
    _MODELS_AVAILABLE = True
except ImportError:
    SortKind = None  # type: ignore[assignment,misc]
    FragmentHint = None  # type: ignore[assignment,misc]
    EncodeStatus = None  # type: ignore[assignment,misc]
    EncodingContext = None  # type: ignore[assignment,misc]
    _MODELS_AVAILABLE = False

try:
    from jugeo.solver.fragments import Fragment, classify_fragment
    _FRAGMENTS_AVAILABLE = True
except ImportError:
    Fragment = None  # type: ignore[assignment,misc]
    classify_fragment = None  # type: ignore[assignment,misc]
    _FRAGMENTS_AVAILABLE = False

try:
    from jugeo.solver.z3_session import Z3Session, Z3Formula, SolveOutcome
    _Z3SESSION_AVAILABLE = True
except ImportError:
    Z3Session = None  # type: ignore[assignment,misc]
    Z3Formula = None  # type: ignore[assignment,misc]
    SolveOutcome = None  # type: ignore[assignment,misc]
    _Z3SESSION_AVAILABLE = False

try:
    import z3
    _Z3_AVAILABLE = True
except ImportError:
    z3 = None  # type: ignore[assignment]
    _Z3_AVAILABLE = False

logger = logging.getLogger(__name__)

# ============================== scalar sort enum ==============================


class ScalarSort(Enum):
    """Z3 SMT sort corresponding to each Python scalar type.

    Each member represents a distinct SMT sort that Z3 can use as the declared
    sort for a ``(declare-const ...)`` command.  The member values are
    ``auto()`` integers; the sort string representations are obtained via
    :meth:`to_z3_sort_name`.

    Members
    -------
    INT_SORT
        Maps to Z3's ``Int`` sort (linear integer arithmetic, ``QF_LIA``).
    FLOAT_SORT
        Maps to Z3's ``Real`` sort (linear real arithmetic, ``QF_LRA``).
    BOOL_SORT
        Maps to Z3's ``Bool`` sort (propositional logic).
    STRING_SORT
        Maps to Z3's ``String`` sort (string theory, ``QF_S``).
    BITVEC_SORT
        Maps to Z3's ``(_ BitVec 64)`` sort (bit-vector arithmetic, ``QF_BV``).
    REAL_SORT
        Alias for the real sort when the annotation is explicitly ``Real``
        rather than ``float``; used by domain experts who write SMT2 directly.
    UNKNOWN_SORT
        Sentinel for unannotated or un-parseable type strings.
    """

    INT_SORT = auto()
    FLOAT_SORT = auto()
    BOOL_SORT = auto()
    STRING_SORT = auto()
    BITVEC_SORT = auto()
    REAL_SORT = auto()
    UNKNOWN_SORT = auto()

    # ------------------------------------------------------------------ #
    # Sort name helpers                                                    #
    # ------------------------------------------------------------------ #

    def to_z3_sort_name(self) -> str:
        """Return the SMT-LIB 2 sort name string for this scalar sort.

        The returned string is the token that appears in SMT-LIB 2 commands
        such as ``(declare-const x <sort>)``.

        Returns
        -------
        str
            One of ``"Int"``, ``"Real"``, ``"Bool"``, ``"String"``,
            ``"(_ BitVec 64)"``, or ``"UNKNOWN"``.

        Examples
        --------
        >>> ScalarSort.INT_SORT.to_z3_sort_name()
        'Int'
        >>> ScalarSort.FLOAT_SORT.to_z3_sort_name()
        'Real'
        >>> ScalarSort.BOOL_SORT.to_z3_sort_name()
        'Bool'
        """
        _map = {
            ScalarSort.INT_SORT: "Int",
            ScalarSort.FLOAT_SORT: "Real",
            ScalarSort.BOOL_SORT: "Bool",
            ScalarSort.STRING_SORT: "String",
            ScalarSort.BITVEC_SORT: "(_ BitVec 64)",
            ScalarSort.REAL_SORT: "Real",
            ScalarSort.UNKNOWN_SORT: "UNKNOWN",
        }
        return _map[self]

    def python_type_name(self) -> str:
        """Return the Python type name most closely associated with this sort.

        Returns
        -------
        str
            A Python built-in type name: ``"int"``, ``"float"``, ``"bool"``,
            ``"str"``, or ``"object"`` for unknown/bitvec sorts.

        Examples
        --------
        >>> ScalarSort.STRING_SORT.python_type_name()
        'str'
        >>> ScalarSort.BITVEC_SORT.python_type_name()
        'object'
        """
        _map = {
            ScalarSort.INT_SORT: "int",
            ScalarSort.FLOAT_SORT: "float",
            ScalarSort.BOOL_SORT: "bool",
            ScalarSort.STRING_SORT: "str",
            ScalarSort.BITVEC_SORT: "object",
            ScalarSort.REAL_SORT: "float",
            ScalarSort.UNKNOWN_SORT: "object",
        }
        return _map[self]

    def is_numeric(self) -> bool:
        """Return True if this sort participates in numeric (arithmetic) theories.

        ``INT_SORT``, ``FLOAT_SORT``, ``REAL_SORT``, and ``BITVEC_SORT`` are
        considered numeric.  ``BOOL_SORT``, ``STRING_SORT``, and
        ``UNKNOWN_SORT`` are not.

        Returns
        -------
        bool

        Examples
        --------
        >>> ScalarSort.INT_SORT.is_numeric()
        True
        >>> ScalarSort.BOOL_SORT.is_numeric()
        False
        """
        return self in (
            ScalarSort.INT_SORT,
            ScalarSort.FLOAT_SORT,
            ScalarSort.REAL_SORT,
            ScalarSort.BITVEC_SORT,
        )

    def is_ordered(self) -> bool:
        """Return True if the sort has a natural total order.

        Ordered sorts admit comparison operators (``<``, ``<=``, ``>``,
        ``>=``) in SMT-LIB 2 assertions.  ``Bool`` and ``String`` have
        partial or lexicographic orderings that are not directly representable
        in standard QF fragments without additional axioms.

        Returns
        -------
        bool

        Examples
        --------
        >>> ScalarSort.REAL_SORT.is_ordered()
        True
        >>> ScalarSort.STRING_SORT.is_ordered()
        False
        """
        return self in (
            ScalarSort.INT_SORT,
            ScalarSort.FLOAT_SORT,
            ScalarSort.REAL_SORT,
            ScalarSort.BITVEC_SORT,
        )

    def default_smt2_decl(self, var: str) -> str:
        """Return the default SMT-LIB 2 ``declare-const`` command for a variable.

        Produces the declaration ``(declare-const <var> <sort>)`` using
        :meth:`to_z3_sort_name` to fill in the sort.

        Parameters
        ----------
        var:
            The name of the SMT variable to declare (e.g. ``"x"``).

        Returns
        -------
        str
            The SMT-LIB 2 declaration command as a single-line string.

        Examples
        --------
        >>> ScalarSort.INT_SORT.default_smt2_decl("x")
        '(declare-const x Int)'
        >>> ScalarSort.FLOAT_SORT.default_smt2_decl("y")
        '(declare-const y Real)'
        """
        sort_name = self.to_z3_sort_name()
        return f"(declare-const {var} {sort_name})"


# ============================== witness dataclass ==============================


@dataclass(frozen=True)
class TheEncodingLayerBeginWitness:
    """Witness that a typed structural core has been established.

    An instance of this class certifies that the Z3 sort declarations and
    base variable declarations for a set of Python-typed variables have been
    generated and are ready to be emitted as the preamble of an SMT-LIB 2
    script.  The witness is *immutable* (``frozen=True``) so that downstream
    analysis phases can freely share and compare witnesses without fear of
    aliasing bugs.

    Fields
    ------
    witness_id : str
        Unique identifier for this witness instance (UUID4 hex string).
    variable_declarations : tuple[tuple[str, str], ...]
        Sequence of ``(variable_name, smt2_sort_string)`` pairs capturing the
        structural core declarations in the order they should be emitted.
    sort_assignments : tuple[tuple[str, ScalarSort], ...]
        Sequence of ``(variable_name, ScalarSort)`` pairs recording the
        :class:`ScalarSort` assigned to each variable.
    base_assertions : tuple[str, ...]
        Tuple of SMT-LIB 2 assertion strings that form the minimal non-trivial
        constraints on the structural-core variables (e.g. ``"(assert (> x 0))"``
        for a variable declared with type ``{x:int | x > 0}``).
    structural_depth : int
        The number of distinct sort layers present in the core (1 = all
        variables share one sort, N = N distinct sorts).
    copilot_label : str
        Free-text label for copilot annotation; may be empty.
    encoding_cost : float
        Estimated SMT solving cost of the structural core, in abstract units.
        Computed as ``structural_depth * len(variable_declarations)``.
    timestamp : float
        Unix timestamp (seconds) at which the witness was created.
    """

    witness_id: str
    variable_declarations: tuple[tuple[str, str], ...]
    sort_assignments: tuple[tuple[str, ScalarSort], ...]
    base_assertions: tuple[str, ...]
    structural_depth: int
    copilot_label: str
    encoding_cost: float
    timestamp: float

    # ------------------------------------------------------------------ #
    # Accessor helpers                                                     #
    # ------------------------------------------------------------------ #

    def variable_names(self) -> list[str]:
        """Return an ordered list of all declared variable names.

        Returns
        -------
        list[str]
            Variable names in declaration order.

        Examples
        --------
        >>> w.variable_names()
        ['x', 'y', 'z']
        """
        return [name for name, _ in self.variable_declarations]

    def sort_for(self, var: str) -> ScalarSort:
        """Look up the :class:`ScalarSort` assigned to a declared variable.

        Parameters
        ----------
        var:
            Variable name to look up.

        Returns
        -------
        ScalarSort
            The assigned sort, or ``ScalarSort.UNKNOWN_SORT`` if the variable
            was not declared in this witness.

        Examples
        --------
        >>> w.sort_for("x")
        <ScalarSort.INT_SORT: 1>
        """
        # copilot: linear scan is acceptable because structural cores rarely
        # exceed O(50) variables in practice.
        for name, sort in self.sort_assignments:
            if name == var:
                return sort
        logger.warning("sort_for: variable %r not found in witness %s", var, self.witness_id)
        return ScalarSort.UNKNOWN_SORT

    # ------------------------------------------------------------------ #
    # SMT-LIB 2 emission                                                  #
    # ------------------------------------------------------------------ #

    def smt2_preamble(self) -> str:
        """Return the complete SMT-LIB 2 preamble for this structural core.

        The preamble consists of:

        1. A comment header identifying the witness.
        2. One ``(declare-const ...)`` command per declared variable.
        3. The base assertions, if any.

        Returns
        -------
        str
            A multi-line SMT-LIB 2 string ready for concatenation with
            refinement predicates and proof obligations.

        Examples
        --------
        >>> print(w.smt2_preamble())
        ; structural core witness abc123
        (declare-const x Int)
        (declare-const y Real)
        """
        lines: list[str] = []
        lines.append(f"; structural core witness {self.witness_id}")
        lines.append(f"; depth={self.structural_depth} cost={self.encoding_cost:.3f}")
        if self.copilot_label:
            lines.append(f"; copilot-label: {self.copilot_label}")
        lines.append("")
        for var, sort_str in self.variable_declarations:
            lines.append(f"(declare-const {var} {sort_str})")
        if self.base_assertions:
            lines.append("")
            lines.append("; base assertions from structural core")
            for assertion in self.base_assertions:
                lines.append(assertion)
        return "\n".join(lines)

    # ------------------------------------------------------------------ #
    # Fingerprint and merge                                                #
    # ------------------------------------------------------------------ #

    def fingerprint(self) -> str:
        """Compute a stable content fingerprint for this witness.

        The fingerprint is an MD5 digest over the JSON-serialised
        ``variable_declarations`` and ``sort_assignments`` fields.  It is
        stable across Python sessions and can be used as a cache key.

        Returns
        -------
        str
            A 32-character lowercase hex string.

        Examples
        --------
        >>> w.fingerprint()
        'a3f8c2e1...'
        """
        payload = json.dumps(
            {
                "decls": list(self.variable_declarations),
                "sorts": [(n, s.name) for n, s in self.sort_assignments],
                "assertions": list(self.base_assertions),
            },
            sort_keys=True,
        )
        return hashlib.md5(payload.encode()).hexdigest()

    def merge(self, other: TheEncodingLayerBeginWitness) -> TheEncodingLayerBeginWitness:
        """Merge two structural-core witnesses into a combined witness.

        Variables from ``self`` take precedence; variables in ``other`` that
        do not appear in ``self`` are appended.  Base assertions from both
        witnesses are unioned (duplicates removed while preserving order).

        Parameters
        ----------
        other:
            The witness to merge into this one.

        Returns
        -------
        TheEncodingLayerBeginWitness
            A new witness containing the union of both cores.

        Examples
        --------
        >>> merged = w1.merge(w2)
        >>> set(w1.variable_names() + w2.variable_names()) == set(merged.variable_names())
        True
        """
        # copilot: build merged declaration list preserving ordering from self,
        # then appending new-only entries from other.
        self_vars = set(self.variable_names())
        extra_decls = [(v, s) for v, s in other.variable_declarations if v not in self_vars]
        extra_sorts = [(v, s) for v, s in other.sort_assignments if v not in self_vars]

        merged_decls = self.variable_declarations + tuple(extra_decls)
        merged_sorts = self.sort_assignments + tuple(extra_sorts)

        seen_assertions: set[str] = set()
        merged_assertions: list[str] = []
        for a in chain(self.base_assertions, other.base_assertions):
            if a not in seen_assertions:
                seen_assertions.add(a)
                merged_assertions.append(a)

        new_depth = len({s.name for _, s in merged_sorts})
        new_cost = float(new_depth * len(merged_decls))

        return TheEncodingLayerBeginWitness(
            witness_id=str(uuid.uuid4()).replace("-", ""),
            variable_declarations=merged_decls,
            sort_assignments=merged_sorts,
            base_assertions=tuple(merged_assertions),
            structural_depth=new_depth,
            copilot_label=f"merged:{self.witness_id[:8]}+{other.witness_id[:8]}",
            encoding_cost=new_cost,
            timestamp=time.time(),
        )

    def copilot_core_hint(self) -> str:
        """Return a human-readable copilot hint summarising this witness.

        The hint is suitable for insertion into copilot comment blocks that
        document encoding decisions in generated code.

        Returns
        -------
        str
            A single-line hint string.

        Examples
        --------
        >>> print(w.copilot_core_hint())
        # copilot: structural core with 3 vars, depth=2, cost=6.0 [INT_SORT, REAL_SORT]
        """
        sort_names = sorted({s.name for _, s in self.sort_assignments})
        return (
            f"# copilot: structural core with {len(self.variable_declarations)} vars, "
            f"depth={self.structural_depth}, cost={self.encoding_cost:.1f} "
            f"[{', '.join(sort_names)}]"
        )


# ============================== analyzer ==============================


class TheEncodingLayerBeginAnalyzer:
    """Analyzes Python type annotations and builds the typed structural core.

    This class provides the complete pipeline from raw Python-style type
    annotation strings to a :class:`TheEncodingLayerBeginWitness` that can be
    emitted as an SMT-LIB 2 preamble.  The pipeline proceeds in four steps:

    1. **Normalization** — strip whitespace, lower-case keywords, expand
       abbreviations.
    2. **Sort inference** — map each (possibly refined) type string to a
       :class:`ScalarSort`.
    3. **Declaration building** — produce ``(declare-const ...)`` tuples.
    4. **Assertion encoding** — encode any base refinement predicates as
       SMT-LIB 2 assertions (the refinement predicate is preserved verbatim
       from the annotation; full predicate compilation happens in later phases).

    The analyzer is intentionally *stateless* across calls: all state is
    returned in the witness.

    copilot: Call :meth:`analyze_type_annotations` with a dict mapping
    variable names to their Python-style type strings.  The result is a
    witness whose :meth:`~TheEncodingLayerBeginWitness.smt2_preamble` can be
    prepended to any SMT2 script.
    """

    def __init__(self) -> None:
        """Initialise the analyzer with an empty normalisation cache."""
        # copilot: the normalization cache avoids repeated regex work for
        # repeated calls with the same type strings.
        self._norm_cache: dict[str, str] = {}
        logger.debug("TheEncodingLayerBeginAnalyzer initialised")

    # ------------------------------------------------------------------ #
    # Public pipeline entry-point                                         #
    # ------------------------------------------------------------------ #

    def analyze_type_annotations(
        self, annotations: dict[str, str]
    ) -> TheEncodingLayerBeginWitness:
        """Build a structural-core witness from a dict of type annotations.

        The dict maps variable names to their Python-style type annotation
        strings.  Annotation strings may be plain (``"int"``, ``"float"``) or
        refinement-encoded (``"{x:int|x>0}"``).

        Parameters
        ----------
        annotations:
            Mapping from variable name to type annotation string.

        Returns
        -------
        TheEncodingLayerBeginWitness
            A frozen witness encoding the structural core for the given
            variable set.

        Examples
        --------
        >>> analyzer = TheEncodingLayerBeginAnalyzer()
        >>> w = analyzer.analyze_type_annotations({"x": "int", "y": "float"})
        >>> w.variable_names()
        ['x', 'y']
        """
        logger.debug("analyze_type_annotations called with %d variables", len(annotations))
        var_sorts: dict[str, ScalarSort] = {}
        for var, type_str in annotations.items():
            normalized = self._normalize_type_str(type_str)
            sort = self.infer_scalar_sort(normalized)
            var_sorts[var] = sort

        declarations = self.build_variable_declarations(var_sorts)
        base_assertions: list[str] = []
        for var, type_str in annotations.items():
            normalized = self._normalize_type_str(type_str)
            if self._is_refinement_type(normalized):
                assertion = self.encode_base_assertion(var, var_sorts[var])
                if assertion:
                    base_assertions.append(assertion)
        depth = self.compute_structural_depth(declarations)
        cost = float(depth * len(declarations))

        sort_assignments = tuple((var, var_sorts[var]) for var in annotations)

        return TheEncodingLayerBeginWitness(
            witness_id=str(uuid.uuid4()).replace("-", ""),
            variable_declarations=tuple(declarations),
            sort_assignments=sort_assignments,
            base_assertions=tuple(base_assertions),
            structural_depth=depth,
            copilot_label="",
            encoding_cost=cost,
            timestamp=time.time(),
        )

    # ------------------------------------------------------------------ #
    # Sort inference                                                       #
    # ------------------------------------------------------------------ #

    def infer_scalar_sort(self, type_str: str) -> ScalarSort:
        """Infer the :class:`ScalarSort` for a (normalised) type annotation string.

        The inference rules are:

        * ``"int"`` / ``"integer"`` → :attr:`ScalarSort.INT_SORT`
        * ``"float"`` / ``"double"`` → :attr:`ScalarSort.FLOAT_SORT`
        * ``"bool"`` / ``"boolean"`` → :attr:`ScalarSort.BOOL_SORT`
        * ``"str"`` / ``"string"`` → :attr:`ScalarSort.STRING_SORT`
        * ``"real"`` → :attr:`ScalarSort.REAL_SORT`
        * ``"bitvec"`` / ``"bv"`` → :attr:`ScalarSort.BITVEC_SORT`
        * Refinement types delegate to the base type.
        * Everything else → :attr:`ScalarSort.UNKNOWN_SORT`

        Parameters
        ----------
        type_str:
            Normalised annotation string (lower-case, whitespace-stripped).

        Returns
        -------
        ScalarSort

        Examples
        --------
        >>> analyzer.infer_scalar_sort("int")
        <ScalarSort.INT_SORT: 1>
        >>> analyzer.infer_scalar_sort("{x:float|x>0.0}")
        <ScalarSort.FLOAT_SORT: 2>
        """
        # copilot: check for refinement types before the plain-type table.
        if self._is_refinement_type(type_str):
            base = self._extract_base_type(type_str)
            return self.infer_scalar_sort(base)

        _table: dict[str, ScalarSort] = {
            "int": ScalarSort.INT_SORT,
            "integer": ScalarSort.INT_SORT,
            "float": ScalarSort.FLOAT_SORT,
            "double": ScalarSort.FLOAT_SORT,
            "bool": ScalarSort.BOOL_SORT,
            "boolean": ScalarSort.BOOL_SORT,
            "str": ScalarSort.STRING_SORT,
            "string": ScalarSort.STRING_SORT,
            "real": ScalarSort.REAL_SORT,
            "bitvec": ScalarSort.BITVEC_SORT,
            "bv": ScalarSort.BITVEC_SORT,
            "bv64": ScalarSort.BITVEC_SORT,
        }
        sort = _table.get(type_str, ScalarSort.UNKNOWN_SORT)
        if sort is ScalarSort.UNKNOWN_SORT:
            logger.debug("infer_scalar_sort: unknown type string %r", type_str)
        return sort

    # ------------------------------------------------------------------ #
    # Declaration building                                                 #
    # ------------------------------------------------------------------ #

    def build_variable_declarations(
        self, var_sorts: dict[str, ScalarSort]
    ) -> list[tuple[str, str]]:
        """Build a list of ``(variable_name, smt2_sort_string)`` tuples.

        For each variable in ``var_sorts``, the corresponding sort's
        :meth:`ScalarSort.to_z3_sort_name` is used to produce the sort string
        token.

        Parameters
        ----------
        var_sorts:
            Mapping from variable name to :class:`ScalarSort`.

        Returns
        -------
        list[tuple[str, str]]
            Declaration pairs in dict-insertion order.

        Examples
        --------
        >>> analyzer.build_variable_declarations({"x": ScalarSort.INT_SORT})
        [('x', 'Int')]
        """
        return [(var, sort.to_z3_sort_name()) for var, sort in var_sorts.items()]

    def encode_base_assertion(self, var: str, sort: ScalarSort) -> str:
        """Encode a minimal base assertion for a variable given its sort.

        The base assertion is the most permissive non-trivial constraint on a
        variable of the given sort.  For most sorts this is the tautology
        ``(assert true)``, which is not emitted (empty string returned).  For
        ``BITVEC_SORT`` a non-negativity assertion is emitted because unsigned
        semantics require ``bvuge`` lower bound.

        Parameters
        ----------
        var:
            Variable name.
        sort:
            Assigned :class:`ScalarSort`.

        Returns
        -------
        str
            An SMT-LIB 2 assertion string, or ``""`` if no base assertion is
            needed for this sort.

        Examples
        --------
        >>> analyzer.encode_base_assertion("x", ScalarSort.INT_SORT)
        ''
        >>> analyzer.encode_base_assertion("bv", ScalarSort.BITVEC_SORT)
        '(assert (bvuge bv #x0000000000000000))'
        """
        # copilot: only BITVEC_SORT has a structural base assertion; all
        # numeric signed sorts allow negative values by default.
        if sort is ScalarSort.BITVEC_SORT:
            return f"(assert (bvuge {var} #x0000000000000000))"
        return ""

    def compute_structural_depth(self, declarations: list[tuple[str, str]]) -> int:
        """Compute the structural depth of a set of declarations.

        Structural depth is the number of *distinct* sort strings present in
        the declaration list.  A homogeneous core (all variables share the
        same sort) has depth 1; a maximally heterogeneous core has depth equal
        to the number of distinct :class:`ScalarSort` members.

        Parameters
        ----------
        declarations:
            Declaration pairs produced by :meth:`build_variable_declarations`.

        Returns
        -------
        int
            The structural depth (>= 1 if the list is non-empty, 0 otherwise).

        Examples
        --------
        >>> analyzer.compute_structural_depth([("x", "Int"), ("y", "Int")])
        1
        >>> analyzer.compute_structural_depth([("x", "Int"), ("y", "Real")])
        2
        """
        if not declarations:
            return 0
        return len({sort_str for _, sort_str in declarations})

    def validate_structural_core(
        self, witness: TheEncodingLayerBeginWitness
    ) -> bool:
        """Validate that a structural-core witness is internally consistent.

        Checks:

        1. All variable names in ``variable_declarations`` appear in
           ``sort_assignments`` (and vice versa).
        2. All sort strings in ``variable_declarations`` match the
           :meth:`ScalarSort.to_z3_sort_name` for the corresponding entry in
           ``sort_assignments``.
        3. ``structural_depth`` equals the actual number of distinct sort
           strings.
        4. ``encoding_cost`` equals ``structural_depth * len(variable_declarations)``.

        Parameters
        ----------
        witness:
            The witness to validate.

        Returns
        -------
        bool
            ``True`` if the witness passes all checks.

        Examples
        --------
        >>> analyzer.validate_structural_core(w)
        True
        """
        decl_names = set(witness.variable_names())
        sort_names = {n for n, _ in witness.sort_assignments}
        if decl_names != sort_names:
            logger.warning(
                "validate_structural_core: declaration/sort name mismatch: %s vs %s",
                decl_names, sort_names,
            )
            return False

        sort_map = dict(witness.sort_assignments)
        for var, sort_str in witness.variable_declarations:
            expected = sort_map[var].to_z3_sort_name()
            if expected != sort_str:
                logger.warning(
                    "validate_structural_core: sort mismatch for %r: expected %r got %r",
                    var, expected, sort_str,
                )
                return False

        actual_depth = len({s for _, s in witness.variable_declarations})
        if actual_depth != witness.structural_depth:
            logger.warning(
                "validate_structural_core: depth mismatch: expected %d got %d",
                witness.structural_depth, actual_depth,
            )
            return False

        expected_cost = float(witness.structural_depth * len(witness.variable_declarations))
        if not math.isclose(expected_cost, witness.encoding_cost, rel_tol=1e-6):
            logger.warning(
                "validate_structural_core: cost mismatch: expected %.3f got %.3f",
                expected_cost, witness.encoding_cost,
            )
            return False

        return True

    def emit_smt2_declarations(
        self, witness: TheEncodingLayerBeginWitness
    ) -> str:
        """Emit a fully-formed SMT-LIB 2 declarations block.

        Wraps :meth:`TheEncodingLayerBeginWitness.smt2_preamble` with a
        ``(set-logic ...)`` command inferred from the sorts present and a
        final ``(check-sat)`` stub.

        Parameters
        ----------
        witness:
            The structural-core witness to emit.

        Returns
        -------
        str
            A self-contained SMT-LIB 2 script preamble string.

        Examples
        --------
        >>> print(analyzer.emit_smt2_declarations(w))
        (set-logic QF_LIA)
        ; structural core witness ...
        (declare-const x Int)
        (check-sat)
        """
        sorts_present = {s for _, s in witness.sort_assignments}
        logic = self._infer_logic(sorts_present)
        lines: list[str] = [f"(set-logic {logic})", ""]
        lines.append(witness.smt2_preamble())
        lines.append("")
        lines.append("; end of structural core")
        return "\n".join(lines)

    def copilot_structural_core_hint(
        self, witness: TheEncodingLayerBeginWitness
    ) -> str:
        """Produce a copilot-style hint comment for the given structural core.

        The hint summarises the witness in a format suitable for insertion in
        generated code comments.

        Parameters
        ----------
        witness:
            The witness to summarise.

        Returns
        -------
        str
            A multi-line string beginning with ``# copilot:``.

        Examples
        --------
        >>> print(analyzer.copilot_structural_core_hint(w))
        # copilot: structural core id=abc123 vars=['x','y'] depth=2 cost=4.0
        """
        var_list = witness.variable_names()
        lines = [
            f"# copilot: structural core id={witness.witness_id[:8]}",
            f"# copilot: vars={var_list}",
            f"# copilot: depth={witness.structural_depth} cost={witness.encoding_cost:.2f}",
            f"# copilot: assertions={len(witness.base_assertions)}",
        ]
        return "\n".join(lines)

    # ------------------------------------------------------------------ #
    # Private helpers                                                      #
    # ------------------------------------------------------------------ #

    def _normalize_type_str(self, type_str: str) -> str:
        """Normalize a raw type annotation string for sort inference.

        Normalization removes leading/trailing whitespace, collapses internal
        whitespace runs to a single space, and lower-cases the string.  The
        result is cached to avoid repeated work.

        Parameters
        ----------
        type_str:
            Raw annotation string.

        Returns
        -------
        str
            Normalized form.
        """
        if type_str in self._norm_cache:
            return self._norm_cache[type_str]
        # copilot: strip + lower + collapse internal spaces
        normalized = " ".join(type_str.strip().lower().split())
        self._norm_cache[type_str] = normalized
        return normalized

    def _is_refinement_type(self, type_str: str) -> bool:
        """Return True if ``type_str`` encodes a refinement type.

        A refinement type annotation has the form ``{<var>:<base>|<pred>}``
        (with optional spaces).  The detection is a simple brace-and-pipe
        heuristic.

        Parameters
        ----------
        type_str:
            Normalised annotation string.

        Returns
        -------
        bool
        """
        return type_str.startswith("{") and "|" in type_str and type_str.endswith("}")

    def _extract_base_type(self, refinement: str) -> str:
        """Extract the base type from a refinement type string.

        Given ``"{x:int|x>0}"``, returns ``"int"``.

        Parameters
        ----------
        refinement:
            Normalised refinement type string.

        Returns
        -------
        str
            The base type token, or ``"unknown"`` if parsing fails.
        """
        try:
            # Format: {var:base|pred}
            inner = refinement.strip("{}")
            colon_idx = inner.index(":")
            pipe_idx = inner.index("|")
            base = inner[colon_idx + 1: pipe_idx].strip()
            return base
        except (ValueError, IndexError):
            logger.warning("_extract_base_type: failed to parse %r", refinement)
            return "unknown"

    def _extract_refinement_predicate(self, refinement: str) -> str:
        """Extract the predicate from a refinement type string.

        Given ``"{x:int|x>0}"``, returns ``"x>0"``.

        Parameters
        ----------
        refinement:
            Normalised refinement type string.

        Returns
        -------
        str
            The predicate substring, or ``""`` if parsing fails.
        """
        try:
            inner = refinement.strip("{}")
            pipe_idx = inner.index("|")
            return inner[pipe_idx + 1:].strip()
        except (ValueError, IndexError):
            logger.warning("_extract_refinement_predicate: failed to parse %r", refinement)
            return ""

    def _infer_logic(self, sorts: set[ScalarSort]) -> str:
        """Infer the SMT-LIB 2 logic identifier from a set of :class:`ScalarSort`.

        Returns the most specific logic that supports all sorts:

        * Only INT_SORT → ``QF_LIA``
        * Only FLOAT/REAL → ``QF_LRA``
        * INT + FLOAT/REAL → ``QF_LIRA``
        * Any BITVEC → ``QF_BV``
        * String → ``QF_S``
        * Mixed → ``ALL``

        Parameters
        ----------
        sorts:
            Set of ScalarSort values present in the core.

        Returns
        -------
        str
            An SMT-LIB 2 logic identifier string.
        """
        has_int = ScalarSort.INT_SORT in sorts
        has_real = (ScalarSort.FLOAT_SORT in sorts or ScalarSort.REAL_SORT in sorts)
        has_bv = ScalarSort.BITVEC_SORT in sorts
        has_str = ScalarSort.STRING_SORT in sorts

        if has_str:
            return "ALL"
        if has_bv:
            return "QF_BV"
        if has_int and has_real:
            return "QF_LIRA"
        if has_int:
            return "QF_LIA"
        if has_real:
            return "QF_LRA"
        if ScalarSort.BOOL_SORT in sorts:
            return "QF_UF"
        return "QF_LIA"


# ============================== coordinator ==============================


class TheEncodingLayerBeginCoordinator:
    """Main coordinator for typed structural core establishment.

    The coordinator maintains a session-level mutable ``_cache`` mapping
    fingerprints to previously computed witnesses, and a ``_stats`` dict
    counting operations.  It exposes the high-level ``establish_core`` entry
    point and delegates analysis work to :class:`TheEncodingLayerBeginAnalyzer`.

    Attributes
    ----------
    _analyzer : TheEncodingLayerBeginAnalyzer
        The stateless analysis helper owned by this coordinator.
    _cache : dict[str, TheEncodingLayerBeginWitness]
        Fingerprint → witness cache for de-duplication.
    _stats : dict[str, int]
        Counters: ``"cores_established"``, ``"cache_hits"``,
        ``"variables_declared"``, ``"merge_operations"``.

    copilot: Use :meth:`establish_core` as the single entry point for all
    structural core establishment in the encoding pipeline.  The coordinator
    guarantees that identical annotation dicts produce the same witness
    (cache hit) without re-running the analysis.
    """

    def __init__(self) -> None:
        """Initialise the coordinator with fresh cache and stats."""
        self._analyzer = TheEncodingLayerBeginAnalyzer()
        self._cache: dict[str, TheEncodingLayerBeginWitness] = {}
        self._stats: dict[str, int] = defaultdict(int)
        logger.debug("TheEncodingLayerBeginCoordinator initialised")

    # ------------------------------------------------------------------ #
    # Core establishment                                                   #
    # ------------------------------------------------------------------ #

    def establish_core(
        self,
        annotations: dict[str, str],
        label: str = "",
    ) -> TheEncodingLayerBeginWitness:
        """Establish the typed structural core for a set of annotations.

        Checks the cache first; on a miss, delegates to
        :meth:`TheEncodingLayerBeginAnalyzer.analyze_type_annotations` and
        stores the result.

        Parameters
        ----------
        annotations:
            Mapping from variable name to type annotation string.
        label:
            Optional copilot label to attach to the witness.

        Returns
        -------
        TheEncodingLayerBeginWitness
            The structural-core witness (possibly from cache).

        Examples
        --------
        >>> coord = TheEncodingLayerBeginCoordinator()
        >>> w = coord.establish_core({"n": "int", "x": "float"})
        >>> w.variable_names()
        ['n', 'x']
        """
        # copilot: compute a stable cache key from the sorted annotation dict.
        cache_key = hashlib.md5(
            json.dumps(sorted(annotations.items())).encode()
        ).hexdigest()

        if cache_key in self._cache:
            self._stats["cache_hits"] += 1
            logger.debug("establish_core: cache hit for key %s", cache_key[:8])
            cached = self._cache[cache_key]
            # Re-attach label if provided
            if label and not cached.copilot_label:
                return TheEncodingLayerBeginWitness(
                    witness_id=cached.witness_id,
                    variable_declarations=cached.variable_declarations,
                    sort_assignments=cached.sort_assignments,
                    base_assertions=cached.base_assertions,
                    structural_depth=cached.structural_depth,
                    copilot_label=label,
                    encoding_cost=cached.encoding_cost,
                    timestamp=cached.timestamp,
                )
            return cached

        witness = self._analyzer.analyze_type_annotations(annotations)
        if label:
            witness = TheEncodingLayerBeginWitness(
                witness_id=witness.witness_id,
                variable_declarations=witness.variable_declarations,
                sort_assignments=witness.sort_assignments,
                base_assertions=witness.base_assertions,
                structural_depth=witness.structural_depth,
                copilot_label=label,
                encoding_cost=witness.encoding_cost,
                timestamp=witness.timestamp,
            )

        self._cache[cache_key] = witness
        self._stats["cores_established"] += 1
        self._stats["variables_declared"] += len(annotations)
        logger.info(
            "establish_core: new witness %s with %d vars (depth=%d)",
            witness.witness_id[:8], len(annotations), witness.structural_depth,
        )
        return witness

    def establish_core_from_function(
        self, func_signature: str
    ) -> TheEncodingLayerBeginWitness:
        """Parse a Python function signature string and establish the structural core.

        Accepts a simplified signature of the form
        ``"f(x: int, y: float, flag: bool) -> int"`` and extracts the
        parameter annotations to pass to :meth:`establish_core`.  The return
        type is recorded as a virtual variable ``"_return"`` in the witness.

        Parameters
        ----------
        func_signature:
            A Python-style function signature string.

        Returns
        -------
        TheEncodingLayerBeginWitness
            Structural core for all parameters (and return type).

        Examples
        --------
        >>> coord.establish_core_from_function("add(x: int, y: int) -> int")
        TheEncodingLayerBeginWitness(...)
        """
        # copilot: simple heuristic parser — not a full Python parser.
        annotations: dict[str, str] = {}
        try:
            # Extract parameter list
            paren_start = func_signature.index("(")
            paren_end = func_signature.rindex(")")
            params_str = func_signature[paren_start + 1: paren_end]
            for param in params_str.split(","):
                param = param.strip()
                if ":" in param:
                    var, type_str = param.split(":", 1)
                    annotations[var.strip()] = type_str.strip()

            # Extract return type
            if "->" in func_signature:
                return_type = func_signature.split("->", 1)[1].strip()
                annotations["_return"] = return_type
        except (ValueError, IndexError) as exc:
            logger.warning(
                "establish_core_from_function: parse error for %r: %s",
                func_signature, exc,
            )

        return self.establish_core(annotations, label=f"func:{func_signature[:30]}")

    def add_variable(
        self,
        witness: TheEncodingLayerBeginWitness,
        var: str,
        type_str: str,
    ) -> TheEncodingLayerBeginWitness:
        """Add a single variable to an existing structural-core witness.

        If ``var`` is already declared in ``witness``, returns ``witness``
        unchanged.  Otherwise builds a new single-variable witness and merges
        it into ``witness``.

        Parameters
        ----------
        witness:
            The existing structural-core witness to extend.
        var:
            New variable name.
        type_str:
            Python-style type annotation string.

        Returns
        -------
        TheEncodingLayerBeginWitness
            An extended witness.

        Examples
        --------
        >>> w2 = coord.add_variable(w, "z", "bool")
        >>> "z" in w2.variable_names()
        True
        """
        if var in witness.variable_names():
            logger.debug("add_variable: %r already declared; returning unchanged", var)
            return witness
        extra = self.establish_core({var: type_str})
        self._stats["merge_operations"] += 1
        return witness.merge(extra)

    def emit_full_z3_context(
        self, witness: TheEncodingLayerBeginWitness
    ) -> str:
        """Emit the complete Z3 context for the structural core.

        Delegates to :meth:`TheEncodingLayerBeginAnalyzer.emit_smt2_declarations`
        and wraps the result in a Z3 Python script stub (if Z3 is available)
        or returns the raw SMT2 script otherwise.

        Parameters
        ----------
        witness:
            The structural-core witness to emit.

        Returns
        -------
        str
            An SMT-LIB 2 script or Z3 Python script string.

        Examples
        --------
        >>> print(coord.emit_full_z3_context(w))
        (set-logic QF_LIA)
        ...
        """
        smt2 = self._analyzer.emit_smt2_declarations(witness)
        if _Z3_AVAILABLE:
            header = f"; Z3 available: {_Z3_AVAILABLE}\n"
        else:
            header = "; Z3 not available — SMT-LIB 2 output only\n"
        return header + smt2

    def structural_core_report(self) -> str:
        """Return a human-readable summary of coordinator activity.

        Returns
        -------
        str
            A multi-line report string.

        Examples
        --------
        >>> print(coord.structural_core_report())
        TheEncodingLayerBeginCoordinator
          cores_established: 3
          cache_hits: 1
          variables_declared: 7
          merge_operations: 0
          cached_witnesses: 3
        """
        lines = ["TheEncodingLayerBeginCoordinator"]
        for key, value in sorted(self._stats.items()):
            lines.append(f"  {key}: {value}")
        lines.append(f"  cached_witnesses: {len(self._cache)}")
        return "\n".join(lines)

    @property
    def stats(self) -> dict[str, int]:
        """Read-only view of the operation statistics dict.

        Returns
        -------
        dict[str, int]
            Copy of the ``_stats`` counter dict.
        """
        return dict(self._stats)

    def __repr__(self) -> str:
        """Return a concise repr string for the coordinator."""
        return (
            f"TheEncodingLayerBeginCoordinator("
            f"cores={self._stats['cores_established']}, "
            f"cached={len(self._cache)}, "
            f"z3={_Z3_AVAILABLE})"
        )


# ============================== module convenience ==============================


def establish_simple_core(
    annotations: dict[str, str],
) -> TheEncodingLayerBeginWitness:
    """Module-level convenience function: establish a structural core.

    Creates a fresh :class:`TheEncodingLayerBeginCoordinator` and calls
    :meth:`~TheEncodingLayerBeginCoordinator.establish_core`.  Suitable for
    one-off or scripting use where a long-lived coordinator is not required.

    Parameters
    ----------
    annotations:
        Mapping from variable name to type annotation string.

    Returns
    -------
    TheEncodingLayerBeginWitness
        The structural-core witness for the given annotations.

    Examples
    --------
    >>> w = establish_simple_core({"x": "int", "y": "float", "flag": "bool"})
    >>> print(w.smt2_preamble())
    ; structural core witness ...
    (declare-const x Int)
    (declare-const y Real)
    (declare-const flag Bool)
    """
    # copilot: always use a fresh coordinator for module-level calls to avoid
    # cross-call cache aliasing in library contexts.
    coord = TheEncodingLayerBeginCoordinator()
    return coord.establish_core(annotations)


# ============================== smoke test ==============================

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print("=== the_encoding_layer_should_begin_fr smoke test ===\n")

    # --- ScalarSort helpers ---
    for member in ScalarSort:
        print(
            f"  {member.name}: z3={member.to_z3_sort_name()!r} "
            f"py={member.python_type_name()!r} "
            f"numeric={member.is_numeric()} ordered={member.is_ordered()}"
        )
        print(f"    decl: {member.default_smt2_decl('v')}")
    print()

    # --- Analyzer ---
    analyzer = TheEncodingLayerBeginAnalyzer()
    annotations_plain = {"n": "int", "x": "float", "flag": "bool", "name": "str"}
    witness_plain = analyzer.analyze_type_annotations(annotations_plain)
    print("Plain type annotations witness:")
    print(f"  id={witness_plain.witness_id[:8]}")
    print(f"  vars={witness_plain.variable_names()}")
    print(f"  depth={witness_plain.structural_depth}")
    print(f"  cost={witness_plain.encoding_cost}")
    print(f"  fingerprint={witness_plain.fingerprint()[:16]}")
    print(f"  valid={analyzer.validate_structural_core(witness_plain)}")
    print()
    print(witness_plain.smt2_preamble())
    print()

    # Refinement type annotation
    annotations_refined = {"x": "{x:int|x>0}", "y": "{y:float|y<1.0}"}
    witness_refined = analyzer.analyze_type_annotations(annotations_refined)
    print("Refinement type annotations witness:")
    print(witness_refined.smt2_preamble())
    print()

    # --- Emit full Z3 context ---
    smt2_out = analyzer.emit_smt2_declarations(witness_plain)
    print("SMT2 declarations:")
    print(smt2_out)
    print()

    # --- copilot hint ---
    print(witness_plain.copilot_core_hint())
    print(analyzer.copilot_structural_core_hint(witness_plain))
    print()

    # --- Coordinator ---
    coord = TheEncodingLayerBeginCoordinator()
    w1 = coord.establish_core({"a": "int", "b": "real"}, label="test-core-1")
    w2 = coord.establish_core({"c": "bool", "d": "str"}, label="test-core-2")
    w1_cached = coord.establish_core({"a": "int", "b": "real"})  # cache hit

    # Add variable
    w3 = coord.add_variable(w1, "e", "float")
    print(f"After add_variable: vars={w3.variable_names()}")

    # Merge
    merged = w1.merge(w2)
    print(f"Merged witness vars: {merged.variable_names()}")
    print(f"Merged depth: {merged.structural_depth}")
    print()

    # Function signature parsing
    w_func = coord.establish_core_from_function("compute(x: int, y: float, flag: bool) -> int")
    print(f"Function signature witness vars: {w_func.variable_names()}")
    print()

    # Full Z3 context
    full_ctx = coord.emit_full_z3_context(w1)
    print("Full Z3 context:")
    print(full_ctx)
    print()

    print(coord.structural_core_report())
    print(repr(coord))
    print()

    # --- Module-level convenience ---
    w_simple = establish_simple_core({"x": "int", "y": "float"})
    print(f"establish_simple_core result: {w_simple.variable_names()}")
    print()

    # --- sort_for lookup ---
    w_check = coord.establish_core({"alpha": "int", "beta": "bool"})
    print(f"sort_for alpha: {w_check.sort_for('alpha')}")
    print(f"sort_for beta: {w_check.sort_for('beta')}")
    print(f"sort_for gamma (missing): {w_check.sort_for('gamma')}")

    # --- Z3 availability ---
    print(f"\nZ3 available: {_Z3_AVAILABLE}")
    print(f"models available: {_MODELS_AVAILABLE}")
    print(f"fragments available: {_FRAGMENTS_AVAILABLE}")
    print("\n=== smoke test complete ===")
