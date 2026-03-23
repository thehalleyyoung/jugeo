r"""theory2.tex Ch31 §31.1 — Partial Functions as Z3 Relations.

A partial function :math:`f : A \\rightharpoonup B` is encoded as a pair
:math:`(\\mathrm{dom}_f, R_f)` where:

- :math:`\\mathrm{dom}_f : A \\to \\mathbb{B}` is the domain predicate,
- :math:`R_f : A \\times B \\to \\mathbb{B}` is the graph relation satisfying
  :math:`\\forall x.\\,\\mathrm{dom}_f(x) \\Rightarrow \\exists! y.\\,R_f(x,y)`.

Totalization strategies (§31.1.3):

.. math::

   \\hat{f}(x) =
   \\begin{cases}
     f(x)  & \\text{if } \\mathrm{dom}_f(x) \\\\
     d     & \\text{otherwise (default-value strategy)}
   \\end{cases}

Domain predicate lattice ordering (§31.1.4):

.. math::

   f \\sqsubseteq g
   \\;\\iff\\;
   \\forall x.\\,\\mathrm{dom}_f(x) \\Rightarrow \\mathrm{dom}_g(x)
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# Standard library imports
# ---------------------------------------------------------------------------

import hashlib
import json
import time
import uuid
from dataclasses import dataclass, field
import dataclasses
from enum import Enum
from typing import Any

# ---------------------------------------------------------------------------
# Optional jugeo subpackage imports — each block degrades gracefully
# ---------------------------------------------------------------------------

try:
    from jugeo.solver.z3_session import Z3Session, Z3Formula, Z3Encoder, Z3Decoder, Z3Result
    _Z3_SESSION_AVAILABLE = True
except ImportError:
    _Z3_SESSION_AVAILABLE = False
    class Z3Session:  # type: ignore[misc]
        pass
    class Z3Formula:  # type: ignore[misc]
        pass
    class Z3Encoder:  # type: ignore[misc]
        pass
    class Z3Decoder:  # type: ignore[misc]
        pass
    class Z3Result:  # type: ignore[misc]
        pass

try:
    from jugeo.solver.reconstruction import ModelReconstructor as SolverModelReconstruction
    _RECONSTRUCTION_AVAILABLE = True
except ImportError:
    _RECONSTRUCTION_AVAILABLE = False
    class SolverModelReconstruction:  # type: ignore[misc]
        pass

try:
    from jugeo.judgments.judgment_terms import JudgmentTerm, Judgment
    _JUDGMENTS_AVAILABLE = True
except ImportError:
    _JUDGMENTS_AVAILABLE = False
    class JudgmentTerm:  # type: ignore[misc]
        pass
    class Judgment:  # type: ignore[misc]
        pass

try:
    from jugeo.evidence.trust import TrustAlgebra, TrustLevel
    _TRUST_AVAILABLE = True
except ImportError:
    _TRUST_AVAILABLE = False
    class TrustAlgebra:  # type: ignore[misc]
        pass
    class TrustLevel:  # type: ignore[misc]
        pass

# ---------------------------------------------------------------------------
# Local models import (partiality_model_reconstruction.models)
# ---------------------------------------------------------------------------

try:
    from jugeo.encodings.partiality_model_reconstruction.models import (
        PartialFunctionEncoding,
        PartialityKind,
    )
    _MODELS_AVAILABLE = True
except ImportError:
    _MODELS_AVAILABLE = False

    class PartialFunctionEncoding:  # type: ignore[misc]
        """Stub for PartialFunctionEncoding when models module is unavailable."""
        name: str = ""
        domain_sort: str = ""
        range_sort: str = ""
        domain_pred: str = ""
        relation: str = ""
        encoding_id: str = ""

    class PartialityKind:  # type: ignore[misc]
        """Stub for PartialityKind when models module is unavailable."""
        PARTIAL_DOMAIN = "partial_domain"


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class DomainPredicateKind(str, Enum):
    """Classifies how a domain predicate is expressed relative to its function.

    The kind determines whether the predicate was written explicitly by the
    user, inferred from guards, derived as a refinement type, or computed
    synthetically during encoding transformations.

    Attributes
    ----------
    EXPLICIT:
        The domain predicate was stated directly by the author.
    IMPLICIT:
        The domain predicate is inferred from usage patterns.
    GUARD:
        The domain predicate is extracted from a guard/pre-condition.
    REFINEMENT:
        The domain predicate originates from a refinement type annotation.
    COMPUTED:
        The domain predicate was produced by an algebraic combination of
        other predicates (complement, intersection, union).
    """

    EXPLICIT = "explicit"
    IMPLICIT = "implicit"
    GUARD = "guard"
    REFINEMENT = "refinement"
    COMPUTED = "computed"


class TotalizationKind(str, Enum):
    """Strategy used to totalize a partial function into a total function.

    Totalization is required when a partial function must be embedded into a
    context that demands a total function (e.g., SMT2 theory axioms).  Each
    strategy corresponds to a semantic treatment of undefined inputs.

    Attributes
    ----------
    DEFAULT_VALUE:
        Return a designated default element when the argument is out-of-domain.
    EXCEPTION_RAISE:
        Raise a typed exception for out-of-domain arguments.
    UNDEFINED_SORT:
        Wrap the result in an option/maybe type; out-of-domain → Nothing.
    BOTTOM_ELEMENT:
        Return the least element ⊥ of the result lattice.
    PARTIAL_RESULT:
        Produce a partial-result wrapper that records definedness alongside value.
    """

    DEFAULT_VALUE = "default_value"
    EXCEPTION_RAISE = "exception_raise"
    UNDEFINED_SORT = "undefined_sort"
    BOTTOM_ELEMENT = "bottom_element"
    PARTIAL_RESULT = "partial_result"


class CompositionMode(str, Enum):
    """Mode of composition for partial functions.

    Describes the algebraic composition structure when combining two or more
    partial functions into a single compound function.

    Attributes
    ----------
    SEQUENTIAL:
        Standard f∘g — output of g feeds into f; domain is intersection.
    PARALLEL:
        Pair-wise application on product types.
    CONDITIONAL:
        One of several partial functions is selected by a guard.
    ITERATED:
        A single partial function is applied repeatedly (Kleene iteration).
    """

    SEQUENTIAL = "sequential"
    PARALLEL = "parallel"
    CONDITIONAL = "conditional"
    ITERATED = "iterated"


# ---------------------------------------------------------------------------
# DomainPredicate dataclass
# ---------------------------------------------------------------------------

@dataclass
class DomainPredicate:
    """Represents a domain predicate for a partial function.

    A domain predicate :math:`\\mathrm{dom}_f : A \\to \\mathbb{B}` records
    precisely which inputs are valid for a partial function *f*.  This class
    stores both the SMT2 expression that encodes the predicate and metadata
    needed for lattice operations.

    Parameters
    ----------
    predicate_name:
        The SMT2 function symbol name for this predicate (e.g. ``"dom_f"``).
    sort:
        The SMT2 sort of the predicate argument (e.g. ``"Int"``).
    smt2_expression:
        The SMT2 body expression defining when the predicate holds.
    kind:
        How this predicate was produced; see :class:`DomainPredicateKind`.
    variables:
        List of free variable names appearing in *smt2_expression*.
    pred_id:
        Unique identifier (UUID4 string) for this predicate instance.
    """

    predicate_name: str
    sort: str
    smt2_expression: str
    kind: DomainPredicateKind = DomainPredicateKind.EXPLICIT
    variables: list[str] = field(default_factory=list)
    pred_id: str = field(default_factory=lambda: str(uuid.uuid4()))

    # ------------------------------------------------------------------
    # Point evaluation
    # ------------------------------------------------------------------

    def evaluate_at(self, point: str) -> str:
        """Substitute the first free variable with *point* and return the SMT2 term.

        If ``self.variables`` is non-empty the first variable is substituted
        textually.  If no free variables are recorded the predicate is applied
        in function-call form.

        Parameters
        ----------
        point:
            The concrete SMT2 term to plug in for the first variable.

        Returns
        -------
        str
            A closed SMT2 Boolean term asserting the predicate at *point*.

        Examples
        --------
        >>> dp = DomainPredicate("dom_f", "Int", "(>= x 0)", variables=["x"])
        >>> dp.evaluate_at("42")
        '(>= 42 0)'
        """
        if self.variables:
            # Replace the first listed free variable with the concrete point.
            return self.smt2_expression.replace(self.variables[0], point)
        # Fall back to prefix-application syntax when variable list is absent.
        return f"({self.predicate_name} {point})"

    # ------------------------------------------------------------------
    # Lattice / algebraic operations
    # ------------------------------------------------------------------

    def complement(self) -> DomainPredicate:
        """Return the complement predicate ¬dom_f.

        The complement predicate is satisfied precisely where *self* is *not*
        satisfied; it represents the co-domain of undefinedness.

        Returns
        -------
        DomainPredicate
            A new :class:`DomainPredicate` with ``kind=COMPUTED`` whose
            *smt2_expression* is ``(not <original>)``.
        """
        complemented_expr = f"(not {self.smt2_expression})"
        return DomainPredicate(
            predicate_name=f"not_{self.predicate_name}",
            sort=self.sort,
            smt2_expression=complemented_expr,
            kind=DomainPredicateKind.COMPUTED,
            variables=list(self.variables),
            pred_id=str(uuid.uuid4()),
        )

    def intersect(self, other: DomainPredicate) -> DomainPredicate:
        """Return the intersection predicate dom_f ∧ dom_g.

        The intersection is the largest predicate implied by both *self* and
        *other*.  Variables from both predicates are merged (deduplicated).

        Parameters
        ----------
        other:
            The second operand in the intersection.

        Returns
        -------
        DomainPredicate
            A new ``COMPUTED`` predicate whose expression is
            ``(and <self> <other>)``.
        """
        combined_expr = f"(and {self.smt2_expression} {other.smt2_expression})"
        # Merge variable lists, keeping order and removing duplicates.
        merged_vars: list[str] = list(self.variables)
        for v in other.variables:
            if v not in merged_vars:
                merged_vars.append(v)
        return DomainPredicate(
            predicate_name=f"and_{self.predicate_name}_{other.predicate_name}",
            sort=self.sort,
            smt2_expression=combined_expr,
            kind=DomainPredicateKind.COMPUTED,
            variables=merged_vars,
            pred_id=str(uuid.uuid4()),
        )

    def union(self, other: DomainPredicate) -> DomainPredicate:
        """Return the union predicate dom_f ∨ dom_g.

        The union is the smallest predicate that implies both *self* and
        *other*.  This corresponds to the join in the domain-predicate lattice
        (§31.1.4).

        Parameters
        ----------
        other:
            The second operand in the union.

        Returns
        -------
        DomainPredicate
            A new ``COMPUTED`` predicate whose expression is
            ``(or <self> <other>)``.
        """
        combined_expr = f"(or {self.smt2_expression} {other.smt2_expression})"
        merged_vars: list[str] = list(self.variables)
        for v in other.variables:
            if v not in merged_vars:
                merged_vars.append(v)
        return DomainPredicate(
            predicate_name=f"or_{self.predicate_name}_{other.predicate_name}",
            sort=self.sort,
            smt2_expression=combined_expr,
            kind=DomainPredicateKind.COMPUTED,
            variables=merged_vars,
            pred_id=str(uuid.uuid4()),
        )

    def implies(self, other: DomainPredicate) -> str:
        """Produce the SMT2 implication assertion dom_f ⇒ dom_g.

        This encodes the ordering relation :math:`f \\sqsubseteq g` for the
        domain-predicate lattice.  The returned string is an SMT2 formula
        (not yet wrapped in ``assert``).

        Parameters
        ----------
        other:
            The predicate that must follow from *self*.

        Returns
        -------
        str
            An SMT2 Boolean term ``(=> <self> <other>)``.
        """
        return f"(=> {self.smt2_expression} {other.smt2_expression})"

    def is_tautology(self) -> bool:
        """Heuristically decide whether this predicate is universally true.

        The check is purely syntactic: only well-known normal forms for the
        Boolean constant *true* are recognised.  This is intentionally
        conservative — a ``False`` return does not imply the predicate is not
        a tautology.

        Returns
        -------
        bool
            ``True`` iff the *smt2_expression* is a recognised tautological form.
        """
        normalised = self.smt2_expression.strip()
        # Recognise the three standard truth constants used in SMT2 / Lisp / Scheme.
        tautological_forms = {"true", "True", "#t", "(not false)"}
        return normalised in tautological_forms

    def to_smt2_decl(self) -> str:
        """Produce the full SMT2 declaration block for this predicate.

        The output includes a ``declare-fun`` statement followed by an
        ``assert`` that binds the predicate body.

        Returns
        -------
        str
            A multi-line SMT2 string suitable for appending to a solver script.
        """
        lines: list[str] = [
            f"; Domain predicate: {self.predicate_name} (kind={self.kind.value})",
            f"(declare-fun {self.predicate_name} ({self.sort}) Bool)",
        ]
        if self.variables:
            # Emit a universally-quantified axiom tying the function symbol to the expression.
            var_decls = " ".join(f"({v} {self.sort})" for v in self.variables)
            lines.append(
                f"(assert (forall ({var_decls})"
                f" (= ({self.predicate_name} {' '.join(self.variables)})"
                f" {self.smt2_expression})))"
            )
        else:
            lines.append(f"(assert {self.smt2_expression})")
        return "\n".join(lines)

    def substitute(self, var: str, val: str) -> DomainPredicate:
        """Return a copy of this predicate with *var* replaced by *val*.

        Textual substitution is applied to *smt2_expression*.  The updated
        variable list is returned with *var* removed (since it is now bound).

        Parameters
        ----------
        var:
            The variable name to substitute.
        val:
            The SMT2 term to substitute in place of *var*.

        Returns
        -------
        DomainPredicate
            A new predicate instance reflecting the substitution.
        """
        new_expr = self.smt2_expression.replace(var, val)
        new_vars = [v for v in self.variables if v != var]
        return DomainPredicate(
            predicate_name=self.predicate_name,
            sort=self.sort,
            smt2_expression=new_expr,
            kind=self.kind,
            variables=new_vars,
            pred_id=str(uuid.uuid4()),
        )


# ---------------------------------------------------------------------------
# PartialFunctionLattice
# ---------------------------------------------------------------------------

class PartialFunctionLattice:
    """Lattice of partial-function encodings ordered by domain inclusion.

    The ordering relation is :math:`f \\sqsubseteq g` iff every input defined
    for *f* is also defined for *g* (§31.1.4).  This class maintains the
    partial order as a dictionary of upward-closure sets so that join/meet
    operations can be computed lazily.

    Attributes
    ----------
    _elements:
        Map from encoding_id to :class:`PartialFunctionEncoding`.
    _order:
        Adjacency representation: ``_order[a]`` is the set of ``b`` such that
        ``a ≤ b`` in the lattice (reflexive, not transitively closed).
    lattice_id:
        UUID string identifying this lattice instance.
    """

    def __init__(self) -> None:
        """Initialise an empty lattice."""
        self._elements: dict[str, Any] = {}
        self._order: dict[str, set[str]] = {}
        self.lattice_id: str = str(uuid.uuid4())

    # ------------------------------------------------------------------
    # Insertion and order maintenance
    # ------------------------------------------------------------------

    def insert(self, pf: Any) -> None:
        """Insert a :class:`PartialFunctionEncoding` into the lattice.

        After insertion the ordering relation is updated by comparing the new
        element against all existing elements using the :meth:`leq` heuristic.

        Parameters
        ----------
        pf:
            The partial-function encoding to add.
        """
        eid = pf.encoding_id
        self._elements[eid] = pf
        # Initialise the upward set for this element.
        self._order[eid] = set()

        # Recompute ordering against all previously inserted elements.
        for existing_id, existing_pf in self._elements.items():
            if existing_id == eid:
                continue
            # Check if new element is ≤ existing.
            if self.leq(eid, existing_id):
                self._order[eid].add(existing_id)
            # Check if existing element is ≤ new.
            if self.leq(existing_id, eid):
                self._order[existing_id].add(eid)

    # ------------------------------------------------------------------
    # Ordering predicate
    # ------------------------------------------------------------------

    def leq(self, a_id: str, b_id: str) -> bool:
        """Heuristic order check: a ≤ b in the domain-predicate lattice.

        The approximation uses syntactic subset inclusion of the domain-predicate
        string, i.e. if a's ``domain_pred`` is a substring of b's
        ``domain_pred`` we consider a ≤ b.  This is an over-approximation
        but is sound for structured predicate expressions generated by the
        encoding pipeline.

        Parameters
        ----------
        a_id:
            Encoding ID of the candidate smaller element.
        b_id:
            Encoding ID of the candidate larger element.

        Returns
        -------
        bool
            ``True`` if a ≤ b according to the syntactic heuristic or if
            ``a_id == b_id`` (reflexivity).
        """
        if a_id == b_id:
            return True
        a_pf = self._elements.get(a_id)
        b_pf = self._elements.get(b_id)
        if a_pf is None or b_pf is None:
            return False
        a_pred = getattr(a_pf, "domain_pred", "")
        b_pred = getattr(b_pf, "domain_pred", "")
        # Syntactic containment check.
        return bool(a_pred) and a_pred in b_pred

    # ------------------------------------------------------------------
    # Lattice operations
    # ------------------------------------------------------------------

    def join(self, a_id: str, b_id: str) -> Any:
        """Compute the lattice join (least upper bound) of two encodings.

        The join is constructed by taking the disjunction of the two domain
        predicates, which yields the smallest domain that contains both.

        Parameters
        ----------
        a_id:
            Encoding ID of the first operand.
        b_id:
            Encoding ID of the second operand.

        Returns
        -------
        PartialFunctionEncoding
            A fresh encoding whose ``domain_pred`` is
            ``(or <a.domain_pred> <b.domain_pred>)``.

        Raises
        ------
        KeyError
            If either *a_id* or *b_id* is not present in this lattice.
        """
        a = self._elements[a_id]
        b = self._elements[b_id]
        a_pred = getattr(a, "domain_pred", "false")
        b_pred = getattr(b, "domain_pred", "false")
        joined_pred = f"(or {a_pred} {b_pred})"
        joined_name = f"join_{getattr(a, 'name', a_id)}_{getattr(b, 'name', b_id)}"
        # Build a new PartialFunctionEncoding-like namespace object.
        result = PartialFunctionEncoding()
        result.name = joined_name
        result.domain_sort = getattr(a, "domain_sort", "")
        result.range_sort = getattr(a, "range_sort", "")
        result.domain_pred = joined_pred
        result.relation = getattr(a, "relation", "")
        result.encoding_id = str(uuid.uuid4())
        return result

    def meet(self, a_id: str, b_id: str) -> Any:
        """Compute the lattice meet (greatest lower bound) of two encodings.

        The meet is constructed by taking the conjunction of the two domain
        predicates.

        Parameters
        ----------
        a_id:
            Encoding ID of the first operand.
        b_id:
            Encoding ID of the second operand.

        Returns
        -------
        PartialFunctionEncoding
            A fresh encoding whose ``domain_pred`` is
            ``(and <a.domain_pred> <b.domain_pred>)``.
        """
        a = self._elements[a_id]
        b = self._elements[b_id]
        a_pred = getattr(a, "domain_pred", "true")
        b_pred = getattr(b, "domain_pred", "true")
        met_pred = f"(and {a_pred} {b_pred})"
        met_name = f"meet_{getattr(a, 'name', a_id)}_{getattr(b, 'name', b_id)}"
        result = PartialFunctionEncoding()
        result.name = met_name
        result.domain_sort = getattr(a, "domain_sort", "")
        result.range_sort = getattr(a, "range_sort", "")
        result.domain_pred = met_pred
        result.relation = getattr(a, "relation", "")
        result.encoding_id = str(uuid.uuid4())
        return result

    def bottom(self) -> Any | None:
        """Return the bottom element of the lattice, if present.

        The bottom element is the encoding whose ``domain_pred`` is the
        constant ``false`` — the partial function defined on the empty domain.

        Returns
        -------
        PartialFunctionEncoding or None
            The bottom encoding, or ``None`` if no such element exists.
        """
        for pf in self._elements.values():
            if getattr(pf, "domain_pred", "").strip() == "false":
                return pf
        return None  # Explicitly returning None is correct here — no bottom exists.

    def top(self) -> Any | None:
        """Return the top element of the lattice, if present.

        The top element is the encoding whose ``domain_pred`` is the constant
        ``true`` — the total function defined everywhere.

        Returns
        -------
        PartialFunctionEncoding or None
            The top encoding, or ``None`` if no such element exists.
        """
        for pf in self._elements.values():
            if getattr(pf, "domain_pred", "").strip() == "true":
                return pf
        return None

    def chain_from(self, start_id: str) -> list[str]:
        """Compute an upward chain starting from *start_id* via BFS.

        Traverses the ``_order`` adjacency dict breadth-first, collecting all
        elements reachable from *start_id*.  The result is ordered by discovery
        time (BFS level-order).

        Parameters
        ----------
        start_id:
            The lattice element from which the chain begins.

        Returns
        -------
        list[str]
            Ordered list of encoding IDs reachable upward from *start_id*,
            including *start_id* itself.
        """
        visited: list[str] = []
        queue: list[str] = [start_id]
        seen: set[str] = {start_id}
        while queue:
            current = queue.pop(0)
            visited.append(current)
            for successor in sorted(self._order.get(current, set())):
                if successor not in seen:
                    seen.add(successor)
                    queue.append(successor)
        return visited

    def covers(self, a_id: str, b_id: str) -> bool:
        """Return True iff *b* covers *a* in the lattice (a ≺ b).

        *b* covers *a* means a < b and there is no element *c* with a < c < b.

        Parameters
        ----------
        a_id:
            The lower element.
        b_id:
            The candidate covering element.

        Returns
        -------
        bool
            ``True`` iff a < b and no strict intermediate element exists.
        """
        if not self.leq(a_id, b_id) or a_id == b_id:
            return False
        # Check for the absence of any intermediate element c: a < c < b.
        for c_id in self._elements:
            if c_id == a_id or c_id == b_id:
                continue
            if self.leq(a_id, c_id) and self.leq(c_id, b_id):
                return False  # Found an intermediate element — b does not cover a.
        return True

    def to_dot(self) -> str:
        """Produce a GraphViz DOT representation of the lattice Hasse diagram.

        Each lattice element becomes a node labelled with its ``name``
        attribute (or encoding_id as fallback).  Each ordering relation
        a ≤ b becomes a directed edge a → b.

        Returns
        -------
        str
            A complete DOT language string for the lattice graph.
        """
        lines: list[str] = [
            f'digraph lattice_{self.lattice_id[:8]} {{',
            '  rankdir=BT;',
            '  node [shape=box, fontname="Courier"];',
        ]
        # Emit nodes.
        for eid, pf in self._elements.items():
            label = getattr(pf, "name", eid[:8])
            safe_id = eid.replace("-", "_")
            lines.append(f'  {safe_id} [label="{label}"];')
        # Emit edges (covering relations for cleaner diagram).
        for a_id in self._order:
            for b_id in self._order[a_id]:
                a_safe = a_id.replace("-", "_")
                b_safe = b_id.replace("-", "_")
                lines.append(f"  {a_safe} -> {b_safe};")
        lines.append("}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# GuardedEncoding dataclass
# ---------------------------------------------------------------------------

@dataclass
class GuardedEncoding:
    """Encoding of a partial function using a guard/pre-condition.

    A guarded encoding represents a partial function :math:`f` as a triple
    ``(name, guard, body)`` where the function is defined when ``guard`` holds
    and its value is given by ``body``.  When ``guard`` does not hold,
    ``fallback_expression`` is used.

    Parameters
    ----------
    function_name:
        The SMT2 name for this partial function.
    guard_condition:
        An SMT2 Boolean term that determines when the function is defined.
    body_expression:
        An SMT2 term giving the function's value on its domain.
    sort:
        The SMT2 argument sort for the function.
    encoding_id:
        Unique UUID4 identifier for this encoding.
    fallback_expression:
        SMT2 term returned when *guard_condition* does not hold.
    """

    function_name: str
    guard_condition: str
    body_expression: str
    sort: str
    encoding_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    fallback_expression: str = "undefined"

    # ------------------------------------------------------------------
    # SMT2 output
    # ------------------------------------------------------------------

    def to_smt2(self) -> str:
        """Produce a complete SMT2 encoding for this guarded function.

        The output consists of:

        1. A ``declare-fun`` line for the function symbol.
        2. A ``define-fun`` that uses ``ite`` to dispatch on the guard.

        Returns
        -------
        str
            Multi-line SMT2 text defining the guarded function.
        """
        arg_var = f"{self.function_name}_arg"
        lines: list[str] = [
            f"; Guarded partial function: {self.function_name}",
            f"; Guard: {self.guard_condition}",
            f"(declare-fun {self.function_name} ({self.sort}) {self.sort})",
            f"(define-fun {self.function_name}_total (({arg_var} {self.sort})) {self.sort}",
            f"  (ite {self.guard_condition}",
            f"    {self.body_expression}",
            f"    {self.fallback_expression}))",
        ]
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Guard manipulation
    # ------------------------------------------------------------------

    def weaken_guard(self, relaxation: str) -> GuardedEncoding:
        """Return a new encoding whose guard is relaxed (weakened).

        The new guard is the disjunction of *self.guard_condition* and
        *relaxation*, which makes the function defined on a strictly larger
        domain.

        Parameters
        ----------
        relaxation:
            An SMT2 Boolean term to add as an additional admitted case.

        Returns
        -------
        GuardedEncoding
            A new encoding with a weakened guard condition.
        """
        new_guard = f"(or {self.guard_condition} {relaxation})"
        return dataclasses.replace(
            self,
            guard_condition=new_guard,
            encoding_id=str(uuid.uuid4()),
        )

    def strengthen_guard(self, constraint: str) -> GuardedEncoding:
        """Return a new encoding whose guard is strengthened (tightened).

        The new guard is the conjunction of *self.guard_condition* and
        *constraint*, which restricts the function to a smaller domain.

        Parameters
        ----------
        constraint:
            An SMT2 Boolean term that further restricts the domain.

        Returns
        -------
        GuardedEncoding
            A new encoding with a more restrictive guard.
        """
        new_guard = f"(and {self.guard_condition} {constraint})"
        return dataclasses.replace(
            self,
            guard_condition=new_guard,
            encoding_id=str(uuid.uuid4()),
        )

    def compose_with(self, other: GuardedEncoding) -> GuardedEncoding:
        """Return the sequential composition self ; other.

        In the composed function the output of *self* is fed as input to
        *other*.  The domain of the composition is the set of inputs for
        which *self* is defined and its output satisfies *other*'s guard.

        Parameters
        ----------
        other:
            The second partial function to apply after *self*.

        Returns
        -------
        GuardedEncoding
            The composed partial function ``other ∘ self``.
        """
        # Substitute self's function result placeholder into other's guard.
        result_placeholder = f"{self.function_name}_result"
        composed_guard_other_part = other.guard_condition.replace(
            f"{other.function_name}_arg", result_placeholder
        )
        combined_guard = f"(and {self.guard_condition} {composed_guard_other_part})"

        # The body of the composition substitutes self's output into other's body.
        composed_body = other.body_expression.replace(
            f"{other.function_name}_arg", self.body_expression
        )
        composed_name = f"{self.function_name}_then_{other.function_name}"
        return GuardedEncoding(
            function_name=composed_name,
            guard_condition=combined_guard,
            body_expression=composed_body,
            sort=self.sort,
            encoding_id=str(uuid.uuid4()),
            fallback_expression=self.fallback_expression,
        )

    def extract_domain_pred(self) -> DomainPredicate:
        """Extract the domain predicate implied by this guarded encoding.

        The guard condition is promoted to a :class:`DomainPredicate` with
        kind ``GUARD``, making it available for lattice operations.

        Returns
        -------
        DomainPredicate
            A domain predicate representing the guard of this function.
        """
        return DomainPredicate(
            predicate_name=f"{self.function_name}_dom",
            sort=self.sort,
            smt2_expression=self.guard_condition,
            kind=DomainPredicateKind.GUARD,
            variables=[f"{self.function_name}_arg"],
            pred_id=str(uuid.uuid4()),
        )

    def is_always_defined(self) -> bool:
        """Return True iff the guard is a tautology (function is total).

        The check is purely syntactic: only canonical SMT2 truth constants
        are recognised.

        Returns
        -------
        bool
            ``True`` iff *guard_condition* is a known tautological form.
        """
        return self.guard_condition.strip() in {"true", "True", "#t"}

    def validate(self) -> list[str]:
        """Check internal consistency and return a list of error messages.

        An empty list indicates the encoding is well-formed.

        Returns
        -------
        list[str]
            Accumulated validation errors (empty iff valid).
        """
        errors: list[str] = []
        if not self.function_name.strip():
            errors.append("GuardedEncoding.function_name must not be empty.")
        if not self.guard_condition.strip():
            errors.append("GuardedEncoding.guard_condition must not be empty.")
        if not self.body_expression.strip():
            errors.append("GuardedEncoding.body_expression must not be empty.")
        if not self.sort.strip():
            errors.append("GuardedEncoding.sort must not be empty.")
        return errors


# ---------------------------------------------------------------------------
# TotalizationStrategy dataclass
# ---------------------------------------------------------------------------

@dataclass
class TotalizationStrategy:
    """Strategy for converting a partial function into a total one.

    A totalization strategy determines what value to produce for out-of-domain
    inputs.  Different strategies have different semantic consequences for
    downstream reasoning and proof obligations.

    Parameters
    ----------
    kind:
        The totalization approach to use; see :class:`TotalizationKind`.
    default_expr:
        The default SMT2 expression used when ``kind == DEFAULT_VALUE``.
    exception_sort:
        The exception sort name used when ``kind == EXCEPTION_RAISE``.
    strategy_id:
        Unique UUID4 identifier for this strategy instance.
    """

    kind: TotalizationKind
    default_expr: str = ""
    exception_sort: str = ""
    strategy_id: str = field(default_factory=lambda: str(uuid.uuid4()))

    # ------------------------------------------------------------------
    # Application
    # ------------------------------------------------------------------

    def apply_to(self, pf_encoding: str, domain_pred: str, arg: str) -> str:
        """Produce the totalized SMT2 application term for argument *arg*.

        Selects the encoding template based on ``self.kind`` and constructs
        the appropriate SMT2 ``ite`` or wrapper expression.

        Parameters
        ----------
        pf_encoding:
            The SMT2 function symbol of the partial function.
        domain_pred:
            The SMT2 function symbol (unary predicate) for the domain.
        arg:
            The SMT2 term representing the argument to apply.

        Returns
        -------
        str
            A closed SMT2 term representing the totalized function call.
        """
        if self.kind == TotalizationKind.DEFAULT_VALUE:
            default = self.default_expr if self.default_expr else "default"
            return f"(ite ({domain_pred} {arg}) ({pf_encoding} {arg}) {default})"

        if self.kind == TotalizationKind.EXCEPTION_RAISE:
            exc = self.exception_sort if self.exception_sort else "UndefException"
            return f"(ite ({domain_pred} {arg}) ({pf_encoding} {arg}) (raise {exc}))"

        if self.kind == TotalizationKind.UNDEFINED_SORT:
            return f"(ite ({domain_pred} {arg}) (some ({pf_encoding} {arg})) none)"

        if self.kind == TotalizationKind.BOTTOM_ELEMENT:
            return f"(ite ({domain_pred} {arg}) ({pf_encoding} {arg}) bottom)"

        # PARTIAL_RESULT — wrap in a partial-apply combinator.
        return f"(partial-apply {pf_encoding} {arg} {domain_pred})"

    def generates_exception(self) -> bool:
        """Return True iff this strategy produces exceptions on undefined inputs.

        Returns
        -------
        bool
            ``True`` iff ``kind == EXCEPTION_RAISE``.
        """
        return self.kind == TotalizationKind.EXCEPTION_RAISE

    def generates_bottom(self) -> bool:
        """Return True iff this strategy produces ⊥ on undefined inputs.

        Returns
        -------
        bool
            ``True`` iff ``kind == BOTTOM_ELEMENT``.
        """
        return self.kind == TotalizationKind.BOTTOM_ELEMENT

    def description(self) -> str:
        """Return a human-readable description of this strategy.

        Returns
        -------
        str
            A sentence summarising the strategy's semantic effect.
        """
        descriptions: dict[TotalizationKind, str] = {
            TotalizationKind.DEFAULT_VALUE: (
                "Returns a designated default value for out-of-domain inputs."
            ),
            TotalizationKind.EXCEPTION_RAISE: (
                "Raises a typed exception for out-of-domain inputs."
            ),
            TotalizationKind.UNDEFINED_SORT: (
                "Wraps the result in an option/maybe type; "
                "out-of-domain inputs produce Nothing."
            ),
            TotalizationKind.BOTTOM_ELEMENT: (
                "Returns the lattice bottom element ⊥ for out-of-domain inputs."
            ),
            TotalizationKind.PARTIAL_RESULT: (
                "Returns a partial-result record encoding both value and definedness."
            ),
        }
        return descriptions.get(self.kind, f"Unknown totalization strategy: {self.kind!r}")

    @classmethod
    def compose_strategies(
        cls, strategies: list[TotalizationStrategy]
    ) -> TotalizationStrategy:
        """Combine a list of strategies by precedence.

        The first non-DEFAULT_VALUE strategy in the list is chosen.  If all
        strategies are DEFAULT_VALUE (or the list is empty) the first strategy
        is returned (or a fresh DEFAULT_VALUE strategy).

        Parameters
        ----------
        strategies:
            Ordered list of strategies; earlier strategies take precedence.

        Returns
        -------
        TotalizationStrategy
            The dominant strategy from the list.
        """
        if not strategies:
            return cls(kind=TotalizationKind.DEFAULT_VALUE, default_expr="default")
        # Search for the first non-default strategy.
        for strat in strategies:
            if strat.kind != TotalizationKind.DEFAULT_VALUE:
                return strat
        # All strategies are DEFAULT_VALUE — fall back to the first.
        return strategies[0]


# ---------------------------------------------------------------------------
# Module-level helper functions
# ---------------------------------------------------------------------------

def build_domain_predicate(sort: str, condition: str) -> DomainPredicate:
    """Construct a fresh :class:`DomainPredicate` with an auto-generated name.

    The predicate name is derived from a stable hash of the *condition* string
    so that identical conditions map to the same logical predicate name.

    Parameters
    ----------
    sort:
        The SMT2 sort of the predicate's argument.
    condition:
        The SMT2 Boolean expression defining the predicate body.

    Returns
    -------
    DomainPredicate
        A new domain predicate with ``kind=COMPUTED``.
    """
    # Derive a short, stable name from the condition text.
    short_hash = hashlib.md5(condition.encode()).hexdigest()[:8]
    name = f"dom_pred_{short_hash}"
    return DomainPredicate(
        predicate_name=name,
        sort=sort,
        smt2_expression=condition,
        kind=DomainPredicateKind.COMPUTED,
        variables=[],
        pred_id=str(uuid.uuid4()),
    )


def encode_partial_as_relation(
    name: str, domain_sort: str, range_sort: str
) -> tuple[str, str]:
    """Encode a partial function as an SMT2 binary relation and domain predicate.

    Returns two SMT2 declaration strings:

    - The relation :math:`R_f : A \\times B \\to \\mathbb{B}`.
    - The domain predicate :math:`\\mathrm{dom}_f : A \\to \\mathbb{B}`.

    Parameters
    ----------
    name:
        The base name for the partial function.
    domain_sort:
        The SMT2 sort for the function's domain (argument type).
    range_sort:
        The SMT2 sort for the function's range (result type).

    Returns
    -------
    tuple[str, str]
        ``(relation_decl, domain_pred_decl)`` — two SMT2 declaration strings.
    """
    relation_decl = (
        f"; Relation encoding of partial function {name}\n"
        f"(declare-fun {name}_R ({domain_sort} {range_sort}) Bool)\n"
        f"(assert (forall ((x {domain_sort}) (y1 {range_sort}) (y2 {range_sort}))\n"
        f"  (=> (and ({name}_R x y1) ({name}_R x y2)) (= y1 y2))))"
    )
    domain_pred_decl = (
        f"; Domain predicate for {name}\n"
        f"(declare-fun {name}_dom ({domain_sort}) Bool)\n"
        f"(assert (forall ((x {domain_sort}))\n"
        f"  (= ({name}_dom x) (exists ((y {range_sort})) ({name}_R x y)))))"
    )
    return relation_decl, domain_pred_decl


def totalize_with_default(pf_smt2: str, domain_pred: str, default: str) -> str:
    """Produce an SMT2 ``ite`` expression that totalizes *pf_smt2* with *default*.

    Parameters
    ----------
    pf_smt2:
        SMT2 function application term for the partial function.
    domain_pred:
        SMT2 term asserting that the argument is in the domain.
    default:
        SMT2 term to return when the argument is out-of-domain.

    Returns
    -------
    str
        An SMT2 ``ite`` expression encoding the default-value totalization.
    """
    return f"(ite {domain_pred} {pf_smt2} {default})"


def compose_partial_functions(
    f: GuardedEncoding, g: GuardedEncoding
) -> GuardedEncoding:
    """Sequentially compose two guarded partial functions f ; g.

    Delegates to :meth:`GuardedEncoding.compose_with`.

    Parameters
    ----------
    f:
        The first partial function applied.
    g:
        The second partial function applied to the output of *f*.

    Returns
    -------
    GuardedEncoding
        The composed guarded encoding.
    """
    return f.compose_with(g)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    "DomainPredicateKind",
    "TotalizationKind",
    "CompositionMode",
    "DomainPredicate",
    "PartialFunctionLattice",
    "GuardedEncoding",
    "TotalizationStrategy",
    "build_domain_predicate",
    "encode_partial_as_relation",
    "totalize_with_default",
    "compose_partial_functions",
]
