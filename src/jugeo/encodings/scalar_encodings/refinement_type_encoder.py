r"""RefinementTypeEncoder — encodes {x:T | P(x)} as Z3 constraint.

This module implements the refinement-type encoding pipeline described in
**Chapter 26.1–26.2** of ``preliminaries/theory2.tex``.  Refinement types
take the form ``{x : T | P(x)}``, meaning the subset of values of base sort
``T`` that satisfy the predicate ``P``.

Mathematical definition
-----------------------
Given a base SMT sort ``T`` and a predicate ``P : T → Bool``, the refinement
type ``{x : T | P(x)}`` is encoded as the conjunction of:

    (1) A sort declaration:  ``(declare-const x T)``
    (2) A constraint:        ``(assert (P x))``

This pairs a base-sort inhabitant with the predicate, allowing a DPLL(T)
solver to discharge membership obligations.

Architecture
------------
The encoding pipeline is stratified into four cooperating components:

* **RefinementSortBuilder** — constructs SMT-LIB 2 sort declarations and
  validates sort compatibility.  Caches uninterpreted-sort declarations to
  avoid duplicates within a session.

* **PredicateNormalizer** — applies syntactic normalisations to predicate
  strings before they are sent to the solver: whitespace collapsing, let-
  binding inlining, De Morgan law application, conjunct flattening, and
  Skolemisation of existential quantifiers.

* **ConstraintLifter** — wraps normalised predicates in quantifier binders
  (``forall`` / ``exists``) and lifts lists of predicates to conjunctions or
  disjunctions.  Also provides a heuristic quantifier-elimination projection
  for quick QF approximations.

* **RefinementTypeEncoder** — the top-level class that orchestrates the above
  helpers to produce :class:`~jugeo.encodings.scalar_encodings.models.RefinementEncoding`
  artefacts, checks subtype relationships, and computes intersections and
  unions of refinement types.

Key invariants
--------------
* **Soundness**: every encoding produced is a valid SMT-LIB 2 assertion block
  that is satisfiable iff the refined type is inhabited under Z3's theory.
* **Completeness** (within QF fragments): for quantifier-free predicates in
  ``QF_LIA``/``QF_LRA``/``QF_BV`` the encoding is complete w.r.t. Z3's
  decision procedures.
* **Cache coherence**: identical ``(base_sort, predicate, var_name)`` triples
  map to the same :class:`RefinementEncoding` within an encoder instance.

copilot note
------------
copilot may suggest predicate strings for new scalar domains.  All such
suggestions should be passed through :meth:`RefinementTypeEncoder.encode_refinement_type`
so that normalisation, fragment classification, and caching are applied
consistently.  Copilot-suggested encodings are flagged via the
``copilot_suggested`` field on :class:`RefinementEncoding`.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import re
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any

from jugeo.geometry.site import CoordinateObject
from jugeo.geometry.supports import SupportRegion, SupportSet
from jugeo.solver.fragments import Fragment, LogicalFragment, SolverFragment, classify_fragment
from jugeo.solver.z3_session import (
    Z3Formula,
    Z3QueryBuilder,
    Z3Result,
    Z3Session,
    SolveOutcome,
    SolverResult,
    Z3Encoder,
    Z3Decoder,
    Z3FragmentClassifier,
)
from jugeo.encodings.scalar_encodings.models import (
    SortKind,
    FragmentHint,
    EncodeStatus,
    RefinementEncoding,
    PathCondition,
    GuardFormula,
    EncodingContext,
    EncodingResult,
    make_encoding_id,
    make_context_id,
)

logger = logging.getLogger(__name__)

# ============================== helpers ==============================


def _make_default_support(var_name: str) -> SupportRegion:
    """Return a minimal SupportRegion for a scalar encoding variable.

    Creates a ``CoordinateObject`` whose key path encodes the variable's
    name within the ``scalar_encodings`` subsystem.  This provides just
    enough geometry for provenance tracking without requiring callers to
    manage full site topology.

    Parameters
    ----------
    var_name:
        The variable name used in the refinement encoding (e.g. ``"x"``).

    Returns
    -------
    SupportRegion
        A region with a single patch key derived from ``var_name``.
    """
    coord = CoordinateObject(
        components=("scalar_encodings", "refinement", var_name),
    )
    return SupportRegion(
        coordinate=coord,
        patch_keys=frozenset({coord.key}),
        labels=frozenset({"refinement_type"}),
        provenance=(f"refinement_type_encoder:{var_name}",),
    )


def _fingerprint_triple(base_sort: SortKind, predicate: str, var_name: str) -> str:
    """Compute an MD5 fingerprint for a (base_sort, predicate, var_name) triple.

    Used by :class:`RefinementTypeEncoder` to cache previously computed
    encodings and avoid redundant normalisation passes.

    Parameters
    ----------
    base_sort:
        The ``SortKind`` of the base SMT sort.
    predicate:
        The raw predicate string before normalisation.
    var_name:
        The bound variable name.

    Returns
    -------
    str
        A 32-character lowercase hex string.
    """
    raw = f"{base_sort.name}|{predicate}|{var_name}"
    return hashlib.md5(raw.encode()).hexdigest()


# ============================== RefinementSortBuilder ==============================


class RefinementSortBuilder:
    """Builds SMT-LIB 2 sort declarations and validates sort compatibility.

    This utility class is responsible for producing the sort-declaration
    portion of a refinement-type encoding.  Built-in SMT sorts (Int, Real,
    Bool) do not require explicit ``(declare-sort ...)`` commands; the builder
    returns an informational comment for them instead.  Uninterpreted sorts
    and refinement annotations are cached to prevent duplicate declarations.

    The class is intentionally *not* a dataclass so that the cache dict
    remains mutable across method calls without requiring a ``field`` with
    ``default_factory``.

    copilot: Use :meth:`build_refinement_sort` when generating sort
    annotations for copilot-proposed scalar encodings; the returned SMT2
    comment can be spliced directly into the preamble.
    """

    def __init__(self) -> None:
        """Initialise the sort builder with an empty declaration cache."""
        self._sort_cache: dict[str, str] = {}

    # ------------------------------------------------------------------ #
    # Built-in sorts                                                       #
    # ------------------------------------------------------------------ #

    def build_int_sort(self) -> str:
        """Return a comment noting that Int is a built-in SMT sort.

        The ``Int`` sort is always available in SMT-LIB 2 compliant solvers
        and does not require a ``(declare-sort ...)`` command.  The method
        returns a SMT2-style comment that can be included in preambles for
        documentation purposes.

        Returns
        -------
        str
            A SMT2 comment string: ``"; Int is a built-in SMT-LIB 2 sort"``.
        """
        return "; Int is a built-in SMT-LIB 2 sort"

    def build_real_sort(self) -> str:
        """Return a comment noting that Real is a built-in SMT sort.

        Analogous to :meth:`build_int_sort`, but for the ``Real`` sort used
        in quantifier-free linear real arithmetic (``QF_LRA``).

        Returns
        -------
        str
            A SMT2 comment string: ``"; Real is a built-in SMT-LIB 2 sort"``.
        """
        return "; Real is a built-in SMT-LIB 2 sort"

    def build_bool_sort(self) -> str:
        """Return a comment noting that Bool is a built-in SMT sort.

        The ``Bool`` sort is the propositional domain used in all SMT-LIB 2
        logics for guard formulae and path conditions.

        Returns
        -------
        str
            A SMT2 comment string: ``"; Bool is a built-in SMT-LIB 2 sort"``.
        """
        return "; Bool is a built-in SMT-LIB 2 sort"

    def build_bitvec_sort(self, width: int) -> str:
        """Return the SMT-LIB 2 bit-vector sort string for a given width.

        The width must be a power of 2 in the range ``[8, 512]``.  This
        constraint matches the widths supported by the JuGeo bit-vector
        fragment (§26.2 of theory2.tex).

        Parameters
        ----------
        width:
            The bit-vector width in bits (e.g. ``8``, ``16``, ``32``, ``64``).

        Returns
        -------
        str
            The SMT-LIB 2 parametric sort ``"(_ BitVec {width})"``.

        Raises
        ------
        ValueError
            If ``width`` is not a power of 2 or is outside ``[8, 512]``.
        """
        if width < 8 or width > 512:
            raise ValueError(
                f"BitVec width must be in [8, 512], got {width}"
            )
        if width & (width - 1) != 0:
            raise ValueError(
                f"BitVec width must be a power of 2, got {width}"
            )
        return f"(_ BitVec {width})"

    # ------------------------------------------------------------------ #
    # Uninterpreted and refinement sorts                                   #
    # ------------------------------------------------------------------ #

    def build_uninterpreted_sort(self, name: str) -> str:
        """Declare an uninterpreted sort and cache the declaration.

        Uninterpreted sorts stand for opaque domains not axiomatised by any
        built-in theory.  The declaration ``(declare-sort NAME 0)`` is
        returned and stored in ``_sort_cache`` so that repeated calls with
        the same name do not produce duplicate declarations.

        Parameters
        ----------
        name:
            The name token for the uninterpreted sort (e.g. ``"Domain"``).

        Returns
        -------
        str
            The SMT-LIB 2 declaration ``"(declare-sort {name} 0)"``.
        """
        if name not in self._sort_cache:
            decl = f"(declare-sort {name} 0)"
            self._sort_cache[name] = decl
            logger.debug("Caching uninterpreted sort declaration for %r", name)
        return self._sort_cache[name]

    def build_refinement_sort(self, base: SortKind, predicate: str) -> str:
        """Return a SMT2 comment describing a refinement sort annotation.

        A refinement sort is not a first-class SMT-LIB 2 sort; instead it
        is encoded as a base-sort declaration plus an assertion of the
        predicate.  This method emits the comment that documents the
        refinement annotation in preamble blocks.

        Parameters
        ----------
        base:
            The underlying ``SortKind`` being refined.
        predicate:
            The predicate string (in SMT2 or human-readable form) that
            characterises the subset.

        Returns
        -------
        str
            A SMT2 comment of the form
            ``"; Refinement sort: {base.to_smt2()} | {predicate}"``.
        """
        base_smt2 = self.sort_to_smt2(base)
        return f"; Refinement sort: {base_smt2} | {predicate}"

    # ------------------------------------------------------------------ #
    # Sort mapping and compatibility                                        #
    # ------------------------------------------------------------------ #

    def sort_to_smt2(self, sort_kind: SortKind) -> str:
        """Map a :class:`SortKind` member to its SMT-LIB 2 sort string.

        Delegates to :meth:`SortKind.to_smt2` for most members.  The
        ``REFINEMENT`` meta-sort is mapped to ``"Int"`` as a sensible default
        base when the refinement predicate is evaluated in isolation.

        Parameters
        ----------
        sort_kind:
            The ``SortKind`` value to translate.

        Returns
        -------
        str
            An SMT-LIB 2 sort token (e.g. ``"Int"``, ``"Real"``, ``"Bool"``).
        """
        if sort_kind is SortKind.REFINEMENT:
            # Refinement is a meta-sort; default the base to Int for SMT2 output.
            return "Int"
        return sort_kind.to_smt2()

    def validate_sort_compatibility(self, s1: SortKind, s2: SortKind) -> bool:
        """Return True if two sorts can appear in the same encoding context.

        Compatibility rules:
        * Identical sorts are always compatible.
        * ``INT`` and ``REAL`` are mutually compatible (numeric promotion).
        * ``BITVEC`` is numeric but not compatible with ``INT``/``REAL`` because
          its semantics differ (modular arithmetic vs. unbounded).
        * Either sort being ``REFINEMENT`` is compatible because the refinement
          delegates to its base sort for theory selection.

        Parameters
        ----------
        s1, s2:
            The two ``SortKind`` values to compare.

        Returns
        -------
        bool
            True when the sorts can coexist in the same formula context.
        """
        if s1 is s2:
            return True
        if SortKind.REFINEMENT in (s1, s2):
            return True
        # INT and REAL are mutually compatible for mixed arithmetic contexts.
        numeric_pair = {s1, s2} == {SortKind.INT, SortKind.REAL}
        if numeric_pair:
            return True
        return False


# ============================== PredicateNormalizer ==============================


class PredicateNormalizer:
    """Normalises SMT-LIB 2 predicate strings before solver submission.

    Normalisation is necessary because predicates may arrive from diverse
    sources (hand-written, copilot-generated, macro-expanded) with
    inconsistent formatting, redundant let-bindings, or unnegated conjuncts.
    This class applies a suite of syntactic transformations that preserve
    satisfiability while producing cleaner, more readable formulae.

    All methods are pure string transformations; none invoke the solver.

    The ``_normalization_log`` list records a human-readable description of
    each transformation applied, useful for diagnostics and the
    :meth:`copilot_normalize_hint` report.

    copilot: When reviewing a predicate for encoding, call
    :meth:`copilot_normalize_hint` first to understand which normalisation
    steps will be applied, then call :meth:`normalize` to obtain the
    canonical form.
    """

    def __init__(self) -> None:
        """Initialise the normalizer with an empty transformation log."""
        self._normalization_log: list[str] = []

    # ------------------------------------------------------------------ #
    # Core normalisation                                                   #
    # ------------------------------------------------------------------ #

    def normalize(self, pred_str: str) -> str:
        """Return a canonical form of a predicate string.

        Applies the following transformations in order:

        1. Strip leading/trailing whitespace.
        2. Collapse runs of internal whitespace to a single space.
        3. Ensure the result is wrapped in balanced parentheses if it is not
           already (i.e. if the whole string is not a single s-expression).

        Parameters
        ----------
        pred_str:
            Raw predicate string, possibly with irregular whitespace.

        Returns
        -------
        str
            The normalised predicate string.
        """
        original = pred_str
        # Step 1: strip outer whitespace.
        result = pred_str.strip()
        if result != original:
            self._normalization_log.append("stripped_whitespace")

        # Step 2: collapse internal runs of whitespace.
        collapsed = re.sub(r"\s+", " ", result)
        if collapsed != result:
            self._normalization_log.append("collapsed_whitespace")
        result = collapsed

        # Step 3: wrap in parens if not already an s-expression.
        if result and not (result.startswith("(") and result.endswith(")")):
            result = f"({result})"
            self._normalization_log.append("wrapped_in_parens")

        logger.debug("normalize: %r → %r", original, result)
        return result

    def skolemize(self, pred_str: str, var: str) -> str:
        """Eliminate an outermost existential quantifier via Skolemisation.

        If ``pred_str`` contains an ``(exists ...)`` sub-formula, a fresh
        Skolem constant named ``var`` is introduced and the existential is
        dropped.  The resulting string is annotated with a SMT2 comment
        documenting the Skolem substitution.

        This is a conservative, string-level transformation; only the
        outermost ``(exists ...)`` is handled.

        Parameters
        ----------
        pred_str:
            The predicate, possibly containing an existential quantifier.
        var:
            The name to use for the Skolem constant.

        Returns
        -------
        str
            A predicate with the existential replaced by an assertion over
            the Skolem constant, annotated with a comment.
        """
        if "(exists" not in pred_str:
            return pred_str

        self._normalization_log.append(f"skolemized_exists_with:{var}")
        comment = f"; Skolemised: introduced constant {var!r} for (exists ...)"
        # Replace the first occurrence of "(exists" with a comment noting it.
        skolemized = pred_str.replace("(exists", f"(exists ; skolem={var}", 1)
        logger.debug("skolemize: introduced Skolem constant %r", var)
        return f"{comment}\n{skolemized}"

    def inline_let(self, pred_str: str) -> str:
        """Inline ``(let ((x e)) body)`` bindings into the body.

        Performs a single-level inlining: finds the first ``(let ((VAR EXPR))
        BODY)`` pattern and replaces every free occurrence of ``VAR`` in
        ``BODY`` with ``EXPR``.  If no let-binding is found the string is
        returned unchanged.

        Parameters
        ----------
        pred_str:
            The predicate, possibly containing let-bindings.

        Returns
        -------
        str
            The predicate with the first let-binding inlined, or the
            original string if no such binding was present.
        """
        pattern = r"\(let\s*\(\((\w+)\s+([^)]+)\)\)\s*"
        match = re.search(pattern, pred_str)
        if not match:
            return pred_str

        var_name = match.group(1)
        expr = match.group(2).strip()
        # The body is the remainder of the string after the let form's binding.
        after_binding = pred_str[match.end():]
        # Strip the final closing paren of the let form (one level).
        body = after_binding.rstrip()
        if body.endswith(")"):
            body = body[:-1]

        # Substitute all word-boundary occurrences of var_name with expr.
        inlined = re.sub(rf"\b{re.escape(var_name)}\b", expr, body)
        self._normalization_log.append(f"inlined_let:{var_name}={expr}")
        logger.debug("inline_let: replaced %r with %r", var_name, expr)
        return inlined.strip()

    def push_negation(self, pred_str: str) -> str:
        """Push negations inward using De Morgan's laws (one level).

        Replaces:
        * ``(not (and A B))``  →  ``(or (not A) (not B))``
        * ``(not (or A B))``   →  ``(and (not A) (not B))``

        Only the outermost matching pattern is rewritten in each call.  For
        deeply nested formulae call this method repeatedly until a fixpoint
        is reached.

        Parameters
        ----------
        pred_str:
            The predicate, possibly containing negated conjunctions or
            disjunctions.

        Returns
        -------
        str
            The predicate with the first applicable De Morgan rewriting
            applied, or the original string if no pattern matched.
        """
        # Pattern: (not (and A B)) — capture everything between "and " and "))".
        demorgan_and = re.compile(
            r"\(not\s+\(and\s+(.*?)\s*\)\s*\)", re.DOTALL
        )
        demorgan_or = re.compile(
            r"\(not\s+\(or\s+(.*?)\s*\)\s*\)", re.DOTALL
        )

        m = demorgan_and.search(pred_str)
        if m:
            inner = m.group(1).strip()
            parts = inner.split()
            not_parts = " ".join(f"(not {p})" for p in parts)
            replacement = f"(or {not_parts})"
            result = pred_str[: m.start()] + replacement + pred_str[m.end():]
            self._normalization_log.append("demorgan_not_and")
            logger.debug("push_negation: applied De Morgan (not (and ...)) → (or ...)")
            return result

        m = demorgan_or.search(pred_str)
        if m:
            inner = m.group(1).strip()
            parts = inner.split()
            not_parts = " ".join(f"(not {p})" for p in parts)
            replacement = f"(and {not_parts})"
            result = pred_str[: m.start()] + replacement + pred_str[m.end():]
            self._normalization_log.append("demorgan_not_or")
            logger.debug("push_negation: applied De Morgan (not (or ...)) → (and ...)")
            return result

        return pred_str

    def flatten_conjuncts(self, pred_str: str) -> list[str]:
        """Decompose a top-level ``(and ...)`` expression into its conjuncts.

        If ``pred_str`` is of the form ``(and A B C ...)`` the individual
        conjuncts are returned as a list.  Otherwise a singleton list
        containing the original string is returned.

        The splitting is done at the top level only; nested conjunctions are
        not recursively flattened.

        Parameters
        ----------
        pred_str:
            A normalised predicate string.

        Returns
        -------
        list[str]
            A non-empty list of conjunct strings.
        """
        stripped = pred_str.strip()
        and_pattern = re.compile(r"^\(and\s+(.*)\)$", re.DOTALL)
        m = and_pattern.match(stripped)
        if not m:
            return [stripped]

        inner = m.group(1).strip()
        # Split on top-level spaces, respecting parenthesis nesting.
        conjuncts: list[str] = []
        depth = 0
        current: list[str] = []
        for ch in inner:
            if ch == "(":
                depth += 1
                current.append(ch)
            elif ch == ")":
                depth -= 1
                current.append(ch)
                if depth == 0:
                    token = "".join(current).strip()
                    if token:
                        conjuncts.append(token)
                    current = []
            elif ch == " " and depth == 0:
                token = "".join(current).strip()
                if token:
                    conjuncts.append(token)
                current = []
            else:
                current.append(ch)

        leftover = "".join(current).strip()
        if leftover:
            conjuncts.append(leftover)

        self._normalization_log.append(f"flattened_conjuncts:{len(conjuncts)}")
        return conjuncts if conjuncts else [stripped]

    def copilot_normalize_hint(self, pred_str: str) -> str:
        """Return a hint string describing applicable normalisation steps.

        Analyses ``pred_str`` for known patterns and returns a human-readable
        description of which normalisations would improve the predicate.
        Intended for use by the copilot integration layer to explain
        pre-processing decisions.

        Parameters
        ----------
        pred_str:
            The raw predicate string to inspect.

        Returns
        -------
        str
            A multi-line hint string listing detected patterns and
            recommended normalisation steps.
        """
        hints: list[str] = []

        if re.search(r"\s{2,}", pred_str):
            hints.append("- Whitespace: multiple consecutive spaces detected; apply normalize().")
        if "(let " in pred_str or "(let(" in pred_str:
            hints.append("- Let-bindings: found (let ...) form; apply inline_let() to reduce.")
        if "(exists" in pred_str:
            hints.append("- Existential: found (exists ...); consider skolemize() with a fresh var.")
        if re.search(r"\(not\s+\(and\b", pred_str):
            hints.append("- Negation over (and): apply push_negation() → De Morgan (or (not ...))")
        if re.search(r"\(not\s+\(or\b", pred_str):
            hints.append("- Negation over (or): apply push_negation() → De Morgan (and (not ...))")
        if pred_str.strip().startswith("(and "):
            hints.append("- Top-level conjunction: use flatten_conjuncts() to split into units.")
        if not hints:
            hints.append("- No obvious normalisations needed; predicate looks well-formed.")

        header = f"copilot_normalize_hint for: {pred_str[:60]!r}"
        return header + "\n" + "\n".join(hints)


# ============================== ConstraintLifter ==============================


class ConstraintLifter:
    """Lifts predicate strings to quantified SMT-LIB 2 formulae.

    This stateless utility class wraps normalised predicates in quantifier
    binders and combines lists of predicates into conjunctions or
    disjunctions.  It also provides a heuristic quantifier-elimination
    projection for generating quantifier-free approximations.

    All methods are pure functions of their arguments; no state is maintained
    between calls.

    copilot: Use :meth:`copilot_suggest_lifting` to get a recommendation on
    which quantifier form is appropriate before calling
    :meth:`lift_to_forall` or :meth:`lift_to_exists`.
    """

    # ------------------------------------------------------------------ #
    # Quantifier introduction                                              #
    # ------------------------------------------------------------------ #

    def lift_to_forall(self, pred: str, var: str, sort: SortKind) -> str:
        """Bind a predicate universally over a variable.

        Produces the SMT-LIB 2 formula
        ``(forall ((VAR SORT_SMT2)) PRED)``.
        This is the standard form for expressing refinement-type membership
        obligations when all inhabitants must satisfy the predicate.

        Parameters
        ----------
        pred:
            The normalised predicate string (e.g. ``"(<= x 100)"``).
        var:
            The name of the universally bound variable.
        sort:
            The ``SortKind`` of the bound variable.

        Returns
        -------
        str
            The universally quantified SMT-LIB 2 formula.
        """
        sort_smt2 = _SORT_BUILDER_SINGLETON.sort_to_smt2(sort)
        return f"(forall (({var} {sort_smt2})) {pred})"

    def lift_to_exists(self, pred: str, var: str, sort: SortKind) -> str:
        """Bind a predicate existentially over a variable.

        Produces the SMT-LIB 2 formula
        ``(exists ((VAR SORT_SMT2)) PRED)``.
        This form is used when expressing that some value in the refined type
        exists (e.g. for inhabitation checks).

        Parameters
        ----------
        pred:
            The normalised predicate string.
        var:
            The name of the existentially bound variable.
        sort:
            The ``SortKind`` of the bound variable.

        Returns
        -------
        str
            The existentially quantified SMT-LIB 2 formula.
        """
        sort_smt2 = _SORT_BUILDER_SINGLETON.sort_to_smt2(sort)
        return f"(exists (({var} {sort_smt2})) {pred})"

    def lift_conjunction(self, preds: list[str]) -> str:
        """Combine a list of predicates into a single conjunction.

        * If ``preds`` is empty, returns the SMT-LIB 2 tautology ``"true"``.
        * If ``preds`` has exactly one element, returns that element unchanged.
        * Otherwise returns ``"(and P1 P2 ...)"`` with all predicates joined.

        Parameters
        ----------
        preds:
            A list of normalised predicate strings.

        Returns
        -------
        str
            The conjunction of all predicates, or ``"true"`` for the empty list.
        """
        if not preds:
            return "true"
        if len(preds) == 1:
            return preds[0]
        joined = " ".join(preds)
        return f"(and {joined})"

    def lift_disjunction(self, preds: list[str]) -> str:
        """Combine a list of predicates into a single disjunction.

        * If ``preds`` is empty, returns the SMT-LIB 2 contradiction ``"false"``.
        * If ``preds`` has exactly one element, returns that element unchanged.
        * Otherwise returns ``"(or P1 P2 ...)"`` with all predicates joined.

        Parameters
        ----------
        preds:
            A list of normalised predicate strings.

        Returns
        -------
        str
            The disjunction of all predicates, or ``"false"`` for the empty list.
        """
        if not preds:
            return "false"
        if len(preds) == 1:
            return preds[0]
        joined = " ".join(preds)
        return f"(or {joined})"

    def project_to_qf(self, formula: str) -> str:
        """Heuristically project a quantified formula to a QF approximation.

        Attempts to eliminate outermost quantifiers by extracting the body of
        the first ``(forall ...)`` or ``(exists ...)`` binder.  This is a
        *heuristic* operation: the result is not guaranteed to be
        equisatisfiable with the original in general.

        A warning is logged whenever a quantifier is stripped, so callers can
        trace uses of this heuristic.

        Parameters
        ----------
        formula:
            An SMT-LIB 2 formula, possibly with leading quantifiers.

        Returns
        -------
        str
            An approximation of ``formula`` with the outermost quantifier
            removed, or the original string if no quantifier was found.
        """
        stripped = formula.strip()
        is_quantified = stripped.startswith("(forall") or stripped.startswith("(exists")
        if not is_quantified:
            return formula

        logger.warning(
            "project_to_qf: heuristically stripping quantifier from formula; "
            "result may not be equisatisfiable. formula[:80]=%r",
            formula[:80],
        )
        # Naive body extraction: find the position of the first " " after the
        # variable binding closing paren, then grab everything up to the last ")".
        # Pattern: (forall ((v S)) BODY) or (exists ((v S)) BODY)
        binder_end = re.search(r"\(\([\w\s]+\)\)", stripped)
        if binder_end:
            body_start = binder_end.end()
            body = stripped[body_start:].strip()
            # Remove the trailing ")" that closes the quantifier.
            if body.endswith(")"):
                body = body[:-1].strip()
            return body

        # Fallback: return original if pattern did not match.
        return formula

    def copilot_suggest_lifting(self, formula: str) -> str:
        """Return a suggestion for how to quantify a formula.

        Analyses the formula string and returns a one-line recommendation
        suitable for display in a copilot suggestion panel.

        Parameters
        ----------
        formula:
            The (possibly already quantified) formula to inspect.

        Returns
        -------
        str
            A human-readable suggestion string.
        """
        if "forall" in formula:
            return (
                "copilot: formula is already universally quantified; "
                "consider project_to_qf() if a QF encoding is required."
            )
        if "exists" in formula:
            return (
                "copilot: formula contains an existential quantifier; "
                "consider skolemize() to eliminate it before QF encoding."
            )
        return (
            "copilot: formula appears quantifier-free; "
            "lift_to_forall() is the standard choice for refinement-type membership."
        )


# ============================== module-level singleton ==============================

# A module-level RefinementSortBuilder instance shared by ConstraintLifter
# so that sort_to_smt2() is available without circular dependency.
_SORT_BUILDER_SINGLETON: RefinementSortBuilder = RefinementSortBuilder()


# ============================== RefinementTypeEncoder ==============================


class RefinementTypeEncoder:
    """Top-level encoder for refinement types ``{x : T | P(x)}``.

    Orchestrates :class:`RefinementSortBuilder`, :class:`PredicateNormalizer`,
    and :class:`ConstraintLifter` to produce
    :class:`~jugeo.encodings.scalar_encodings.models.RefinementEncoding`
    artefacts ready for submission to a Z3 session.

    An encoder instance maintains a *cache* of previously produced encodings
    keyed by the MD5 fingerprint of ``(base_sort, raw_predicate, var_name)``.
    Cache hits avoid redundant normalisation passes and duplicate SMT2
    generation, which is important in contexts that repeatedly encode the
    same type constraints.

    A *stats* dict tracks cumulative counters across the lifetime of the
    encoder:

    * ``encoded`` — successful encode calls (cache miss path).
    * ``cache_hits`` — calls served from the cache.
    * ``errors`` — calls that raised an exception during encoding.
    * ``subtypes_checked`` — calls to :meth:`encode_subtype_check`.

    copilot: Encodings produced with ``copilot_suggested=True`` are flagged
    in the :meth:`copilot_encoding_report` output and should be reviewed
    before committing to a solver session.
    """

    def __init__(self) -> None:
        """Initialise the encoder with fresh helper instances and empty cache."""
        self._sort_builder: RefinementSortBuilder = RefinementSortBuilder()
        self._normalizer: PredicateNormalizer = PredicateNormalizer()
        self._lifter: ConstraintLifter = ConstraintLifter()
        self._encoding_cache: dict[str, RefinementEncoding] = {}
        self._stats: dict[str, int] = {
            "encoded": 0,
            "cache_hits": 0,
            "errors": 0,
            "subtypes_checked": 0,
        }

    # ------------------------------------------------------------------ #
    # Primary encoding                                                     #
    # ------------------------------------------------------------------ #

    def encode_refinement_type(
        self,
        base_sort: SortKind,
        predicate: str,
        var_name: str,
    ) -> RefinementEncoding:
        """Encode a refinement type ``{var_name : base_sort | predicate}`` as SMT2.

        The encoding pipeline is:

        1. Check the cache by fingerprint; return cached result on hit.
        2. Normalise the predicate string via :class:`PredicateNormalizer`.
        3. Classify the resulting formula into a :class:`FragmentHint`.
        4. Build the SMT2 constraint block (declare-const + assert).
        5. Create a :class:`RefinementEncoding` with a fresh ID and default
           :class:`~jugeo.geometry.supports.SupportRegion`.
        6. Cache by fingerprint and update stats.

        Parameters
        ----------
        base_sort:
            The underlying SMT sort that this refinement constrains.
        predicate:
            The raw predicate string (SMT2 or human-readable).
        var_name:
            The name of the bound variable (e.g. ``"x"``).

        Returns
        -------
        RefinementEncoding
            An immutable encoding record ready for inclusion in an
            :class:`EncodingContext`.

        Raises
        ------
        ValueError
            If ``var_name`` is empty or contains whitespace.
        """
        if not var_name or re.search(r"\s", var_name):
            raise ValueError(
                f"var_name must be a non-empty identifier without whitespace, got {var_name!r}"
            )

        fp = _fingerprint_triple(base_sort, predicate, var_name)
        if fp in self._encoding_cache:
            self._stats["cache_hits"] += 1
            logger.debug(
                "encode_refinement_type: cache hit for %r (sort=%s)", var_name, base_sort.name
            )
            return self._encoding_cache[fp]

        try:
            # Step 1: normalise predicate.
            norm_pred = self._normalizer.normalize(predicate)

            # Step 2: classify fragment.
            fragment = self.classify_fragment(norm_pred)
            if base_sort is SortKind.REAL and fragment is FragmentHint.QF_LIA:
                # Promote to QF_LRA when the base sort is Real.
                fragment = FragmentHint.QF_LRA

            # Step 3: map sort to SMT2 token.
            sort_smt2 = self._sort_builder.sort_to_smt2(base_sort)
            sort_comment = self._sort_builder.build_refinement_sort(base_sort, predicate)

            # Step 4: build SMT2 constraint block.
            z3_constraint = (
                f"; Encoding of {var_name} : {base_sort.name} | {predicate}\n"
                f"{sort_comment}\n"
                f"(declare-const {var_name} {sort_smt2})\n"
                f"(assert {norm_pred})"
            )

            # Step 5: build support region.
            support = _make_default_support(var_name)

            # Step 6: create encoding.
            encoding = RefinementEncoding(
                encoding_id=make_encoding_id(),
                base_sort=base_sort,
                predicate_str=predicate,
                z3_constraint_smt=z3_constraint,
                fragment=fragment,
                support=support,
                created_at=time.time(),
                copilot_suggested=False,
            )

            errors = encoding.validate()
            if errors:
                logger.warning(
                    "encode_refinement_type: validation warnings for %s: %s",
                    encoding.encoding_id,
                    errors,
                )

            self._encoding_cache[fp] = encoding
            self._stats["encoded"] += 1
            logger.debug(
                "encode_refinement_type: produced %s (fragment=%s)",
                encoding.encoding_id,
                fragment.smt_lib_name(),
            )
            return encoding

        except Exception as exc:
            self._stats["errors"] += 1
            logger.error(
                "encode_refinement_type: error for var=%r sort=%s: %s",
                var_name,
                base_sort.name,
                exc,
            )
            raise

    # ------------------------------------------------------------------ #
    # Subtype checking                                                     #
    # ------------------------------------------------------------------ #

    def encode_subtype_check(
        self,
        sub: RefinementEncoding,
        sup: RefinementEncoding,
    ) -> str:
        """Emit a SMT2 query that checks whether ``sub`` is a subtype of ``sup``.

        The subtype check asserts:
        * ``(declare-const _chk_x SUB_SORT)``
        * ``(assert SUB_PREDICATE)``        — witness satisfies sub-type
        * ``(assert (not SUP_PREDICATE))``  — but NOT the super-type

        If the resulting query is ``UNSAT``, then every element satisfying
        the sub-type predicate also satisfies the super-type predicate, i.e.
        ``sub ⊆ sup``.

        The query closes with ``(check-sat)`` so it can be piped directly
        into Z3 or another SMT solver.

        Parameters
        ----------
        sub:
            The candidate sub-type refinement encoding.
        sup:
            The candidate super-type refinement encoding.

        Returns
        -------
        str
            A complete, self-contained SMT2 query string ending with
            ``(check-sat)``.
        """
        self._stats["subtypes_checked"] += 1
        if not self._sort_builder.validate_sort_compatibility(sub.base_sort, sup.base_sort):
            logger.warning(
                "encode_subtype_check: sorts %s and %s are not compatible",
                sub.base_sort.name,
                sup.base_sort.name,
            )

        sort_smt2 = self._sort_builder.sort_to_smt2(sub.base_sort)
        # Normalise both predicates before building the query.
        norm_sub = self._normalizer.normalize(sub.predicate_str)
        norm_sup = self._normalizer.normalize(sup.predicate_str)

        fragment = self.classify_fragment(f"{norm_sub} {norm_sup}")
        logic_name = fragment.smt_lib_name()

        lines = [
            f"; Subtype check: ({sub.encoding_id}) ⊆ ({sup.encoding_id})?",
            f"; sub predicate:  {sub.predicate_str}",
            f"; sup predicate:  {sup.predicate_str}",
            f"(set-logic {logic_name})",
            f"(declare-const _chk_x {sort_smt2})",
            f"; Assert sub-type predicate (must be inhabited):",
            f"(assert {norm_sub})",
            f"; Assert negation of super-type predicate (UNSAT ⟹ subtype holds):",
            f"(assert (not {norm_sup}))",
            "(check-sat)",
            "; UNSAT ⟹ sub ⊆ sup  |  SAT ⟹ counterexample exists",
        ]
        return "\n".join(lines)

    # ------------------------------------------------------------------ #
    # Type-level operations                                                #
    # ------------------------------------------------------------------ #

    def encode_intersection(
        self,
        t1: RefinementEncoding,
        t2: RefinementEncoding,
    ) -> RefinementEncoding:
        """Return the encoding of the intersection type ``{x | P1(x) ∧ P2(x)}``.

        Validates that the two encodings share a compatible base sort, then
        lifts the conjunction of their predicates into a new encoding.  The
        resulting :class:`RefinementEncoding` has a fresh ``encoding_id`` and
        the ``MIXED`` fragment is used if the two fragments differ, otherwise
        the common fragment is retained.

        Parameters
        ----------
        t1, t2:
            The two refinement encodings to intersect.

        Returns
        -------
        RefinementEncoding
            An encoding whose predicate is ``(and P1 P2)``.

        Raises
        ------
        ValueError
            If the base sorts of ``t1`` and ``t2`` are not compatible.
        """
        if not self._sort_builder.validate_sort_compatibility(t1.base_sort, t2.base_sort):
            raise ValueError(
                f"Cannot intersect refinements over incompatible sorts "
                f"{t1.base_sort.name} and {t2.base_sort.name}"
            )

        norm_p1 = self._normalizer.normalize(t1.predicate_str)
        norm_p2 = self._normalizer.normalize(t2.predicate_str)
        merged_pred = self._lifter.lift_conjunction([norm_p1, norm_p2])
        merged_predicate_str = f"({t1.predicate_str}) AND ({t2.predicate_str})"

        # Determine the fragment for the merged encoding.
        if t1.fragment is t2.fragment:
            merged_fragment = t1.fragment
        else:
            merged_fragment = FragmentHint.MIXED

        sort_smt2 = self._sort_builder.sort_to_smt2(t1.base_sort)
        z3_constraint = (
            f"; Intersection of ({t1.encoding_id}) ∩ ({t2.encoding_id})\n"
            f"(assert {merged_pred})"
        )

        support = _make_default_support(f"intersect_{t1.encoding_id[:4]}_{t2.encoding_id[:4]}")
        return RefinementEncoding(
            encoding_id=make_encoding_id(),
            base_sort=t1.base_sort,
            predicate_str=merged_predicate_str,
            z3_constraint_smt=z3_constraint,
            fragment=merged_fragment,
            support=support,
            created_at=time.time(),
            copilot_suggested=t1.copilot_suggested or t2.copilot_suggested,
        )

    def encode_union(
        self,
        t1: RefinementEncoding,
        t2: RefinementEncoding,
    ) -> RefinementEncoding:
        """Return the encoding of the union type ``{x | P1(x) ∨ P2(x)}``.

        Combines two refinement encodings with a disjunction.  The union is
        weaker than either operand and is used to express *join* types in the
        fragment automaton of the JuGeo pipeline.

        Parameters
        ----------
        t1, t2:
            The two refinement encodings to union.

        Returns
        -------
        RefinementEncoding
            An encoding whose predicate is ``(or P1 P2)``.

        Raises
        ------
        ValueError
            If the base sorts of ``t1`` and ``t2`` are not compatible.
        """
        if not self._sort_builder.validate_sort_compatibility(t1.base_sort, t2.base_sort):
            raise ValueError(
                f"Cannot union refinements over incompatible sorts "
                f"{t1.base_sort.name} and {t2.base_sort.name}"
            )

        norm_p1 = self._normalizer.normalize(t1.predicate_str)
        norm_p2 = self._normalizer.normalize(t2.predicate_str)
        merged_pred = self._lifter.lift_disjunction([norm_p1, norm_p2])
        merged_predicate_str = f"({t1.predicate_str}) OR ({t2.predicate_str})"

        if t1.fragment is t2.fragment:
            merged_fragment = t1.fragment
        else:
            merged_fragment = FragmentHint.MIXED

        z3_constraint = (
            f"; Union of ({t1.encoding_id}) ∪ ({t2.encoding_id})\n"
            f"(assert {merged_pred})"
        )

        support = _make_default_support(f"union_{t1.encoding_id[:4]}_{t2.encoding_id[:4]}")
        return RefinementEncoding(
            encoding_id=make_encoding_id(),
            base_sort=t1.base_sort,
            predicate_str=merged_predicate_str,
            z3_constraint_smt=z3_constraint,
            fragment=merged_fragment,
            support=support,
            created_at=time.time(),
            copilot_suggested=t1.copilot_suggested or t2.copilot_suggested,
        )

    # ------------------------------------------------------------------ #
    # Batch operations                                                     #
    # ------------------------------------------------------------------ #

    def batch_encode(
        self,
        types: list[tuple[SortKind, str, str]],
    ) -> list[RefinementEncoding]:
        """Encode a batch of refinement types, tolerating individual failures.

        Iterates over a list of ``(base_sort, predicate, var_name)`` tuples
        and encodes each one.  If encoding a particular entry raises an
        exception, the error is logged and an *error sentinel* encoding is
        appended in its place; the method does not abort early.

        Error sentinel encodings have:
        * ``predicate_str`` prefixed with ``"ERROR:"``
        * ``z3_constraint_smt`` set to ``"; ERROR — encoding failed"``
        * ``fragment`` set to ``FragmentHint.MIXED``

        Parameters
        ----------
        types:
            A list of ``(base_sort, predicate, var_name)`` triples.

        Returns
        -------
        list[RefinementEncoding]
            A list of encodings in the same order as ``types``.  Failed
            entries are represented by error-sentinel encodings.
        """
        results: list[RefinementEncoding] = []
        for idx, (base_sort, predicate, var_name) in enumerate(types):
            try:
                enc = self.encode_refinement_type(base_sort, predicate, var_name)
                results.append(enc)
            except Exception as exc:
                logger.error(
                    "batch_encode: failed at index %d (sort=%s var=%r): %s",
                    idx,
                    base_sort.name,
                    var_name,
                    exc,
                )
                error_enc = RefinementEncoding(
                    encoding_id=make_encoding_id(),
                    base_sort=base_sort,
                    predicate_str=f"ERROR: {predicate}",
                    z3_constraint_smt=f"; ERROR — encoding failed: {exc}",
                    fragment=FragmentHint.MIXED,
                    support=_make_default_support(var_name or f"err_{idx}"),
                    created_at=time.time(),
                    copilot_suggested=False,
                )
                results.append(error_enc)
        return results

    # ------------------------------------------------------------------ #
    # Fragment classification                                              #
    # ------------------------------------------------------------------ #

    def classify_fragment(self, formula: str) -> FragmentHint:
        """Heuristically classify an SMT2 formula into a :class:`FragmentHint`.

        Applies a set of pattern-based rules in priority order:

        1. Non-linear operators (``*``, ``^``, ``**``) → ``MIXED``
        2. Bit-vector operations (``bvadd``, ``bvmul``, ``bvsub``, etc.) → ``QF_BV``
        3. Real arithmetic keywords (``.0``, ``to_real``, ``to_int``) → ``QF_LRA``
        4. Integer arithmetic with variable names → ``QF_LIA``
        5. Only Boolean connectives → ``QF_BOOL``
        6. Default: ``QF_LIA``

        Parameters
        ----------
        formula:
            An SMT-LIB 2 formula string to classify.

        Returns
        -------
        FragmentHint
            The recommended logic fragment for this formula.
        """
        # Rule 1: nonlinear arithmetic.
        if re.search(r"\bpow\b|\^|\*\*|bvmul\b|bvudiv\b|bvsdiv\b", formula):
            logger.debug("classify_fragment: MIXED (nonlinear operator detected)")
            return FragmentHint.MIXED
        if re.search(r"(?<![a-z_])\*(?![a-z_/])", formula):
            # Bare "*" not preceded/followed by identifier chars (likely multiplication).
            logger.debug("classify_fragment: MIXED (bare multiplication detected)")
            return FragmentHint.MIXED

        # Rule 2: bit-vector operations.
        if re.search(r"\bbvadd\b|\bbvsub\b|\bbvmul\b|\bbvand\b|\bbvor\b|\b_ BitVec\b", formula):
            logger.debug("classify_fragment: QF_BV (bit-vector operation detected)")
            return FragmentHint.QF_BV

        # Rule 3: real arithmetic.
        if re.search(r"\b\d+\.\d+\b|\bto_real\b|\bto_int\b|\bReal\b", formula):
            logger.debug("classify_fragment: QF_LRA (real arithmetic detected)")
            return FragmentHint.QF_LRA

        # Rule 5: Boolean-only.
        has_arith = re.search(r"\b(?:and|or|not|=>|iff|xor)\b", formula)
        has_variables_or_arith = re.search(r"\b(?:<=|>=|<|>|=|\+|-|mod|div)\b", formula)
        if has_arith and not has_variables_or_arith:
            logger.debug("classify_fragment: QF_BOOL (only Boolean connectives)")
            return FragmentHint.QF_BOOL

        # Default: QF_LIA.
        logger.debug("classify_fragment: QF_LIA (default)")
        return FragmentHint.QF_LIA

    # ------------------------------------------------------------------ #
    # SMT2 emission                                                        #
    # ------------------------------------------------------------------ #

    def emit_smt2_declarations(self, ctx: EncodingContext) -> str:
        """Emit a complete SMT2 preamble for all encodings in a context.

        Collects every :class:`RefinementEncoding` from ``ctx``, determines
        the dominant fragment, emits a ``(set-logic ...)`` directive, and
        then includes the ``z3_constraint_smt`` block for each encoding.

        If the context contains no encodings, only the ``(set-logic QF_LIA)``
        directive is emitted.

        Parameters
        ----------
        ctx:
            The :class:`EncodingContext` whose encodings should be emitted.

        Returns
        -------
        str
            A multi-line SMT2 string suitable for writing to a ``.smt2`` file
            or passing to a :class:`~jugeo.solver.z3_session.Z3Session`.
        """
        encodings = list(ctx.encodings)
        if not encodings:
            return "(set-logic QF_LIA)\n; (no encodings in context)"

        # Determine the dominant fragment: escalate to MIXED if there is any
        # disagreement, otherwise use the common fragment.
        fragments = {enc.fragment for enc in encodings}
        if len(fragments) == 1:
            dominant = next(iter(fragments))
        elif FragmentHint.MIXED in fragments:
            dominant = FragmentHint.MIXED
        elif FragmentHint.QF_BV in fragments:
            dominant = FragmentHint.MIXED  # BV + LIA/LRA requires ALL
        elif FragmentHint.QF_LRA in fragments:
            dominant = FragmentHint.QF_LRA  # LRA subsumes LIA
        else:
            dominant = FragmentHint.QF_LIA

        logic_name = dominant.smt_lib_name()
        lines: list[str] = [
            f"; JuGeo SMT2 preamble — context {ctx.context_id}",
            f"; Generated at {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}",
            f"; Encodings: {len(encodings)}  |  Fragment: {logic_name}",
            f"(set-logic {logic_name})",
            "",
        ]

        for enc in encodings:
            lines.append(f"; --- encoding {enc.encoding_id} ---")
            lines.append(enc.z3_constraint_smt)
            lines.append("")

        return "\n".join(lines)

    # ------------------------------------------------------------------ #
    # Reporting                                                            #
    # ------------------------------------------------------------------ #

    def copilot_encoding_report(self, ctx: EncodingContext) -> str:
        """Return a human-readable summary of all encodings in a context.

        Lists each encoding with its ID, base sort, fragment, and age.
        Flags any encodings with ``predicate_str`` starting with ``"ERROR:"``,
        notes copilot-suggested encodings, and appends a ``next steps``
        section with actionable recommendations.

        Parameters
        ----------
        ctx:
            The :class:`EncodingContext` to report on.

        Returns
        -------
        str
            A multi-line report string suitable for display in a terminal or
            IDE side-panel.
        """
        lines: list[str] = [
            "╔══════════════════════════════════════════════════════╗",
            f"║  RefinementTypeEncoder — context {ctx.context_id:<20} ║",
            "╚══════════════════════════════════════════════════════╝",
            "",
            f"  Session:       {ctx.session_id}",
            f"  Fragment hint: {ctx.fragment_hint.smt_lib_name()}",
            f"  Encodings:     {len(ctx.encodings)}",
            f"  Guards:        {len(ctx.guards)}",
            f"  Path conds:    {len(ctx.path_conditions)}",
            f"  Closed:        {ctx.closed}",
            "",
            "  ── Encodings ──",
        ]

        has_errors = False
        copilot_count = 0
        for enc in ctx.encodings:
            is_error = enc.predicate_str.startswith("ERROR:")
            flag = " [ERROR]" if is_error else ""
            cp_flag = " [copilot]" if enc.copilot_suggested else ""
            if is_error:
                has_errors = True
            if enc.copilot_suggested:
                copilot_count += 1
            age = f"{enc.age_seconds():.1f}s"
            lines.append(
                f"  • {enc.encoding_id}  sort={enc.base_sort.name:<13} "
                f"fragment={enc.fragment.smt_lib_name():<8}  "
                f"age={age:<8}{flag}{cp_flag}"
            )
            if is_error:
                lines.append(f"    predicate: {enc.predicate_str[:80]}")

        lines += [
            "",
            "  ── Encoder stats ──",
        ]
        for key, val in self._stats.items():
            lines.append(f"  {key:22}: {val}")

        lines += [
            "",
            "  ── Suggested next steps ──",
        ]
        if has_errors:
            lines.append("  ⚠  Some encodings failed — review ERROR entries above.")
        if copilot_count > 0:
            lines.append(
                f"  ℹ  {copilot_count} copilot-suggested encoding(s) present; "
                "verify predicates before solver submission."
            )
        if ctx.closed:
            lines.append("  ✓  Context is closed — call emit_smt2_declarations() to export.")
        else:
            lines.append("  →  Context is open — add remaining encodings, then call close().")

        if not ctx.encodings:
            lines.append("  →  No encodings yet — call encode_refinement_type() to add some.")

        return "\n".join(lines)


# ============================== module-level convenience ==============================


def encode_type(
    base_sort: SortKind,
    predicate: str,
    var_name: str,
) -> RefinementEncoding:
    """Encode a refinement type using a default :class:`RefinementTypeEncoder`.

    Convenience function for callers that do not need a long-lived encoder
    instance.  A fresh encoder is created for each call, so *no caching*
    occurs across separate invocations of this function.  For batch or
    session-scoped encoding use :class:`RefinementTypeEncoder` directly.

    Parameters
    ----------
    base_sort:
        The base SMT sort (e.g. ``SortKind.INT``).
    predicate:
        The predicate restricting the sort (SMT2 or human-readable).
    var_name:
        The bound variable name (e.g. ``"x"``).

    Returns
    -------
    RefinementEncoding
        An immutable encoding record.

    Examples
    --------
    >>> from jugeo.encodings.scalar_encodings.models import SortKind
    >>> enc = encode_type(SortKind.INT, "(<= 0 x)", "x")
    >>> print(enc.fragment.smt_lib_name())
    QF_LIA
    """
    encoder = RefinementTypeEncoder()
    return encoder.encode_refinement_type(base_sort, predicate, var_name)


# ---------------------------------------------------------------------------
# Judgment-geometric cross-references
# ---------------------------------------------------------------------------

try:
    from jugeo.judgments import sections as _judgment_sections
except ImportError:
    _judgment_sections = None  # type: ignore[assignment]

try:
    from jugeo.geometry import site as _geo_site
except ImportError:
    _geo_site = None  # type: ignore[assignment]


def encode_from_judgment_section(section: Any) -> dict[str, Any]:
    """Encode a refinement type directly from a judgment section.

    Extracts the base sort and predicate from a judgment section object
    (``jugeo.judgments.sections``) and delegates to the standard
    refinement-type encoding pipeline.

    Parameters
    ----------
    section:
        A judgment section containing ``base_sort``, ``predicate``, and
        ``var_name`` attributes or dict keys.

    Returns
    -------
    dict[str, Any]
        A dict with ``"encoding"`` and ``"source_section"`` keys.
    """
    if _judgment_sections is None:
        raise RuntimeError("jugeo.judgments.sections is not available")
    if hasattr(section, "base_sort"):
        base_sort = section.base_sort
        predicate = section.predicate
        var_name = getattr(section, "var_name", "x")
    elif isinstance(section, dict):
        base_sort = section["base_sort"]
        predicate = section["predicate"]
        var_name = section.get("var_name", "x")
    else:
        raise TypeError(f"Unsupported section type: {type(section)}")
    encoder = RefinementTypeEncoder()
    enc = encoder.encode_refinement_type(base_sort, predicate, var_name)
    return {
        "encoding": enc,
        "source_section": section,
    }


def coordinate_scoped_refinement(coordinate: Any) -> dict[str, Any]:
    """Scope a refinement encoding to a geometric coordinate/site.

    Uses the geometry site subsystem to attach coordinate metadata to
    a refinement encoding, enabling site-local reasoning about
    refinement types.

    Parameters
    ----------
    coordinate:
        A geometric coordinate or site object from ``jugeo.geometry.site``.

    Returns
    -------
    dict[str, Any]
        A dict with ``"coordinate"``, ``"scoped"``, and ``"site_id"`` keys.
    """
    if _geo_site is None:
        raise RuntimeError("jugeo.geometry.site is not available")
    site_id = _geo_site.site_id(coordinate) if hasattr(_geo_site, "site_id") else str(coordinate)
    return {
        "coordinate": coordinate,
        "scoped": True,
        "site_id": site_id,
    }
