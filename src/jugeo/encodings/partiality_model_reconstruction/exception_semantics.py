r"""theory2.tex Ch31 §31.2 — Exception-Valued Semantics and Sum Type Encodings.

Exception-valued semantics extends partial-function semantics by replacing the
undefined case with a typed exception value.  The result type of an
exception-valued function :math:`f : A \\to B + E` is a sum type:

.. math::

   B + E
   \\;\\cong\\;
   \\{\\mathrm{Ok}(b) \\mid b \\in B\\}
   \\cup
   \\{\\mathrm{Err}(e) \\mid e \\in E\\}

Propagation rules (§31.2.3):

.. math::

   \\mathrm{propagate}(\\mathrm{Err}(e), k)
   = \\mathrm{Err}(e)
   \\qquad (\\text{strict propagation})

Sum type Maybe (§31.2.1):

.. math::

   \\mathrm{Maybe}(A) = \\mathrm{Nothing} \\mid \\mathrm{Just}(a : A)
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# Standard library imports
# ---------------------------------------------------------------------------

import hashlib
import json
import time
import uuid
from collections import deque
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
# Enumerations
# ---------------------------------------------------------------------------

class PropagationRule(str, Enum):
    """Rule governing how exceptions propagate through an exception graph.

    Propagation rules are associated with edges in an
    :class:`ExceptionPropagationGraph` and determine how an exception that
    arrives at one node is forwarded, suppressed, or transformed when it
    reaches the next.

    Attributes
    ----------
    STRICT:
        The exception propagates immediately without any interception; the
        function is strict in its exception argument.
    LAZY:
        The exception is deferred and only propagated when the result is
        forced (non-strict / lazy evaluation).
    CATCH_AND_RETHROW:
        The exception is caught, possibly inspected or transformed, and
        then re-raised as a (potentially different) exception.
    ABSORB:
        The exception is silently absorbed; control continues normally with
        a designated fallback value.
    BUBBLE_UP:
        The exception propagates upward through a call stack until a handler
        is found; analogous to unchecked exceptions in Java.
    """

    STRICT = "strict"
    LAZY = "lazy"
    CATCH_AND_RETHROW = "catch_and_rethrow"
    ABSORB = "absorb"
    BUBBLE_UP = "bubble_up"


class SumTypeKind(str, Enum):
    """Classifies the sum-type encoding used for exception-valued results.

    Different sum-type flavours have different constructor names and
    semantic conventions.  The choice of kind affects how the SMT2
    datatype declaration is generated and how client code pattern-matches
    on results.

    Attributes
    ----------
    MAYBE:
        ``Nothing | Just(a)`` — represents optional values.
    EITHER:
        ``Left(a) | Right(b)`` — represents a disjoint union of two types.
    RESULT:
        ``Ok(b) | Err(e)`` — represents success or failure with typed error.
    VALIDATED:
        ``Valid(b) | Invalid(errs)`` — carries a list of validation errors.
    EXCEPTIONAL:
        ``Value(b) | Exception(e)`` — a raw exception-value pair.
    """

    MAYBE = "maybe"
    EITHER = "either"
    RESULT = "result"
    VALIDATED = "validated"
    EXCEPTIONAL = "exceptional"


# ---------------------------------------------------------------------------
# ExceptionSort dataclass
# ---------------------------------------------------------------------------

@dataclass
class ExceptionSort:
    """An SMT2 algebraic datatype that classifies exception values.

    An exception sort groups named exception constructors together, each
    optionally carrying typed fields.  The resulting SMT2 datatype serves as
    the *E* component in a ``B + E`` sum type.

    Parameters
    ----------
    sort_name:
        The SMT2 type name for the exception sort (e.g. ``"IOException"``).
    constructors:
        A mapping from constructor names to lists of field-sort names.
        Example: ``{"FileNotFound": ["String"], "PermissionDenied": []}``.
    sort_id:
        Unique UUID4 identifier for this sort definition.
    smt2_declaration:
        Cached SMT2 declaration text (populated lazily by :meth:`declare_in_smt2`).
    """

    sort_name: str
    constructors: dict[str, list[str]] = field(default_factory=dict)
    sort_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    smt2_declaration: str = ""

    # ------------------------------------------------------------------
    # SMT2 declaration
    # ------------------------------------------------------------------

    def declare_in_smt2(self) -> str:
        """Produce the SMT2 ``declare-datatypes`` form for this exception sort.

        Each constructor is emitted with its field projections using the
        naming convention ``<constructor>-field-<index>``.

        Returns
        -------
        str
            A complete ``declare-datatypes`` SMT2 string.

        Examples
        --------
        >>> exc = ExceptionSort("IOError", {"FileNotFound": ["String"], "Timeout": []})
        >>> print(exc.declare_in_smt2())
        (declare-datatypes ((IOError 0)) (( (FileNotFound (FileNotFound-field-0 String)) (Timeout) )))
        """
        constructor_parts: list[str] = []
        for ctor_name, field_sorts in self.constructors.items():
            if not field_sorts:
                # Nullary constructor — no fields.
                constructor_parts.append(f"({ctor_name})")
            else:
                # Build field projection names: <ctor>-field-<idx>.
                field_decls = " ".join(
                    f"({ctor_name}-field-{idx} {sort})"
                    for idx, sort in enumerate(field_sorts)
                )
                constructor_parts.append(f"({ctor_name} {field_decls})")
        constructors_str = " ".join(constructor_parts)
        decl = (
            f"(declare-datatypes (({self.sort_name} 0))"
            f" (( {constructors_str} )))"
        )
        # Cache the result for repeated access.
        self.smt2_declaration = decl
        return decl

    # ------------------------------------------------------------------
    # Constructor application / projection helpers
    # ------------------------------------------------------------------

    def inject_exception(self, constructor: str, args: list[str]) -> str:
        """Produce an SMT2 term that constructs an exception value.

        Parameters
        ----------
        constructor:
            The constructor name (must be a key in ``self.constructors``).
        args:
            A list of SMT2 argument terms for the constructor's fields.

        Returns
        -------
        str
            An SMT2 constructor application term.
        """
        if not args:
            return f"({constructor})"
        joined_args = " ".join(args)
        return f"({constructor} {joined_args})"

    def project_exception(self, constructor: str, field_idx: int, term: str) -> str:
        """Project the *field_idx*-th field from a constructor application.

        Parameters
        ----------
        constructor:
            The constructor whose field is being projected.
        field_idx:
            Zero-based index of the field to project.
        term:
            The SMT2 term holding the exception value.

        Returns
        -------
        str
            An SMT2 field projection expression.
        """
        return f"({constructor}-field-{field_idx} {term})"

    def is_exception_of(self, constructor: str, term: str) -> str:
        """Produce an SMT2 Boolean test for a specific constructor.

        Parameters
        ----------
        constructor:
            The constructor to test for.
        term:
            The SMT2 term to test.

        Returns
        -------
        str
            An SMT2 ``is-<constructor>`` recogniser application.
        """
        return f"(is-{constructor} {term})"

    def all_constructors(self) -> list[str]:
        """Return a sorted list of all constructor names for this sort.

        Returns
        -------
        list[str]
            Alphabetically sorted constructor names.
        """
        return sorted(list(self.constructors.keys()))

    def validate(self) -> list[str]:
        """Check that this exception sort is well-formed.

        Returns
        -------
        list[str]
            A list of error messages (empty iff valid).
        """
        errors: list[str] = []
        if not self.sort_name.strip():
            errors.append("ExceptionSort.sort_name must not be empty.")
        if not self.constructors:
            errors.append(
                f"ExceptionSort '{self.sort_name}' must have at least one constructor."
            )
        # Validate that each constructor has a non-empty name.
        for ctor_name in self.constructors:
            if not ctor_name.strip():
                errors.append("Constructor names must not be empty or whitespace.")
        return errors


# ---------------------------------------------------------------------------
# MaybeEncoding dataclass
# ---------------------------------------------------------------------------

@dataclass
class MaybeEncoding:
    """SMT2 encoding of the Maybe monad / optional type.

    ``Maybe A`` is the sum type ``Nothing | Just(a : A)``.  This encoding
    generates the corresponding SMT2 datatype declaration and provides
    helper methods for constructing and eliminating Maybe values.

    Parameters
    ----------
    value_sort:
        The SMT2 sort of the wrapped value type (the *A* in ``Maybe A``).
    maybe_sort_name:
        The SMT2 type name for the Maybe sort.  Auto-derived from
        *value_sort* in :meth:`__post_init__` if left empty.
    nothing_constructor:
        SMT2 constructor name for the absent case (default: ``"Nothing"``).
    just_constructor:
        SMT2 constructor name for the present case (default: ``"Just"``).
    encoding_id:
        Unique UUID4 identifier for this encoding instance.
    """

    value_sort: str
    maybe_sort_name: str = ""
    nothing_constructor: str = "Nothing"
    just_constructor: str = "Just"
    encoding_id: str = field(default_factory=lambda: str(uuid.uuid4()))

    def __post_init__(self) -> None:
        """Derive *maybe_sort_name* from *value_sort* when unset."""
        if not self.maybe_sort_name:
            self.maybe_sort_name = f"Maybe_{self.value_sort}"

    # ------------------------------------------------------------------
    # SMT2 declaration
    # ------------------------------------------------------------------

    def declare_sort(self) -> str:
        """Produce the SMT2 ``declare-datatypes`` form for this Maybe type.

        Returns
        -------
        str
            A complete ``declare-datatypes`` SMT2 string for ``Maybe_<sort>``.
        """
        return (
            f"(declare-datatypes (({self.maybe_sort_name} 0)) (("
            f" ({self.nothing_constructor})"
            f" ({self.just_constructor} (just-val {self.value_sort}))"
            f" )))"
        )

    # ------------------------------------------------------------------
    # Constructor helpers
    # ------------------------------------------------------------------

    def just(self, value: str) -> str:
        """Wrap *value* in the Just constructor.

        Parameters
        ----------
        value:
            An SMT2 term of sort *value_sort*.

        Returns
        -------
        str
            The SMT2 constructor application ``(Just <value>)``.
        """
        return f"({self.just_constructor} {value})"

    def nothing(self) -> str:
        """Return the Nothing constructor term (no arguments).

        Returns
        -------
        str
            The SMT2 nullary constructor term ``(Nothing)``.
        """
        return f"({self.nothing_constructor})"

    # ------------------------------------------------------------------
    # Recogniser helpers
    # ------------------------------------------------------------------

    def is_just(self, term: str) -> str:
        """Test whether *term* is a Just value.

        Parameters
        ----------
        term:
            An SMT2 term of sort *maybe_sort_name*.

        Returns
        -------
        str
            An SMT2 Boolean recogniser expression.
        """
        return f"(is-{self.just_constructor} {term})"

    def is_nothing(self, term: str) -> str:
        """Test whether *term* is Nothing.

        Parameters
        ----------
        term:
            An SMT2 term of sort *maybe_sort_name*.

        Returns
        -------
        str
            An SMT2 Boolean recogniser expression.
        """
        return f"(is-{self.nothing_constructor} {term})"

    # ------------------------------------------------------------------
    # Projection
    # ------------------------------------------------------------------

    def from_just(self, term: str) -> str:
        """Project the wrapped value out of a Just term.

        Calling this on a Nothing term is undefined behaviour at the SMT2
        level; the caller is responsible for guarding with :meth:`is_just`.

        Parameters
        ----------
        term:
            An SMT2 term of sort *maybe_sort_name* known to be Just.

        Returns
        -------
        str
            An SMT2 field projection expression ``(just-val <term>)``.
        """
        return f"(just-val {term})"

    # ------------------------------------------------------------------
    # Monadic operations
    # ------------------------------------------------------------------

    def bind(self, term: str, f_name: str) -> str:
        """Monadic bind: apply *f_name* to the value inside *term* if Just.

        Implements ``term >>= f`` in SMT2 via an ``ite`` expression.

        Parameters
        ----------
        term:
            An SMT2 term of sort *maybe_sort_name*.
        f_name:
            SMT2 function symbol of type ``value_sort → maybe_sort_name``.

        Returns
        -------
        str
            An SMT2 expression computing the monadic bind.
        """
        return (
            f"(ite (is-{self.just_constructor} {term})"
            f" ({f_name} (just-val {term}))"
            f" ({self.nothing_constructor}))"
        )

    def fold_maybe(self, term: str, nothing_val: str, just_f: str) -> str:
        """Eliminate a Maybe value by case analysis.

        Encodes ``maybe nothing_val just_f term`` as an SMT2 ``ite``.

        Parameters
        ----------
        term:
            An SMT2 term of sort *maybe_sort_name*.
        nothing_val:
            The SMT2 value to return in the Nothing case.
        just_f:
            SMT2 function symbol applied to the unwrapped value in the Just case.

        Returns
        -------
        str
            An SMT2 elimination expression.
        """
        return (
            f"(ite (is-{self.nothing_constructor} {term})"
            f" {nothing_val}"
            f" ({just_f} (just-val {term})))"
        )


# ---------------------------------------------------------------------------
# EitherEncoding dataclass
# ---------------------------------------------------------------------------

@dataclass
class EitherEncoding:
    """SMT2 encoding of the Either sum type.

    ``Either A B`` is the sum type ``Left(a : A) | Right(b : B)``.  In
    exception-valued semantics the convention is ``Either E B`` where
    *Left* carries an exception and *Right* carries a success value.

    Parameters
    ----------
    left_sort:
        SMT2 sort for the Left variant (typically the exception type).
    right_sort:
        SMT2 sort for the Right variant (the success value type).
    either_sort_name:
        SMT2 type name for the Either sort.  Auto-derived in
        :meth:`__post_init__` if left empty.
    left_constructor:
        SMT2 constructor name for the left case (default: ``"Left"``).
    right_constructor:
        SMT2 constructor name for the right case (default: ``"Right"``).
    encoding_id:
        Unique UUID4 identifier for this encoding instance.
    """

    left_sort: str
    right_sort: str
    either_sort_name: str = ""
    left_constructor: str = "Left"
    right_constructor: str = "Right"
    encoding_id: str = field(default_factory=lambda: str(uuid.uuid4()))

    def __post_init__(self) -> None:
        """Derive *either_sort_name* from the component sorts when unset."""
        if not self.either_sort_name:
            self.either_sort_name = f"Either_{self.left_sort}_{self.right_sort}"

    # ------------------------------------------------------------------
    # SMT2 declaration
    # ------------------------------------------------------------------

    def declare_sort(self) -> str:
        """Produce the SMT2 ``declare-datatypes`` form for this Either type.

        Returns
        -------
        str
            A complete ``declare-datatypes`` SMT2 string for the Either type.
        """
        return (
            f"(declare-datatypes (({self.either_sort_name} 0)) (("
            f" ({self.left_constructor} (left-val {self.left_sort}))"
            f" ({self.right_constructor} (right-val {self.right_sort}))"
            f" )))"
        )

    # ------------------------------------------------------------------
    # Constructor helpers
    # ------------------------------------------------------------------

    def left(self, value: str) -> str:
        """Construct a Left value.

        Parameters
        ----------
        value:
            An SMT2 term of sort *left_sort*.

        Returns
        -------
        str
            SMT2 constructor application ``(Left <value>)``.
        """
        return f"({self.left_constructor} {value})"

    def right(self, value: str) -> str:
        """Construct a Right value.

        Parameters
        ----------
        value:
            An SMT2 term of sort *right_sort*.

        Returns
        -------
        str
            SMT2 constructor application ``(Right <value>)``.
        """
        return f"({self.right_constructor} {value})"

    # ------------------------------------------------------------------
    # Recogniser helpers
    # ------------------------------------------------------------------

    def is_left(self, term: str) -> str:
        """Test whether *term* is a Left value.

        Parameters
        ----------
        term:
            An SMT2 term of sort *either_sort_name*.

        Returns
        -------
        str
            An SMT2 Boolean recogniser expression.
        """
        return f"(is-{self.left_constructor} {term})"

    def is_right(self, term: str) -> str:
        """Test whether *term* is a Right value.

        Parameters
        ----------
        term:
            An SMT2 term of sort *either_sort_name*.

        Returns
        -------
        str
            An SMT2 Boolean recogniser expression.
        """
        return f"(is-{self.right_constructor} {term})"

    # ------------------------------------------------------------------
    # Projection
    # ------------------------------------------------------------------

    def from_left(self, term: str) -> str:
        """Project the Left payload from *term*.

        The caller must ensure *term* is a Left value (use :meth:`is_left`).

        Parameters
        ----------
        term:
            An SMT2 term of sort *either_sort_name* known to be Left.

        Returns
        -------
        str
            An SMT2 projection expression ``(left-val <term>)``.
        """
        return f"(left-val {term})"

    def from_right(self, term: str) -> str:
        """Project the Right payload from *term*.

        The caller must ensure *term* is a Right value (use :meth:`is_right`).

        Parameters
        ----------
        term:
            An SMT2 term of sort *either_sort_name* known to be Right.

        Returns
        -------
        str
            An SMT2 projection expression ``(right-val <term>)``.
        """
        return f"(right-val {term})"

    # ------------------------------------------------------------------
    # Functor / fold operations
    # ------------------------------------------------------------------

    def map_right(self, term: str, f_name: str) -> str:
        """Map a function over the Right payload, leaving Left unchanged.

        Encodes the bifunctor ``fmap`` restricted to the right component.

        Parameters
        ----------
        term:
            An SMT2 term of sort *either_sort_name*.
        f_name:
            SMT2 function symbol of type ``right_sort → <new_sort>``.

        Returns
        -------
        str
            An SMT2 expression computing ``bimap id f term``.
        """
        return (
            f"(ite (is-{self.right_constructor} {term})"
            f" ({self.right_constructor} ({f_name} (right-val {term})))"
            f" {term})"
        )

    def fold_either(self, term: str, left_f: str, right_f: str) -> str:
        """Eliminate an Either value by case analysis.

        Encodes ``either left_f right_f term``.

        Parameters
        ----------
        term:
            An SMT2 term of sort *either_sort_name*.
        left_f:
            SMT2 function symbol applied to the Left payload.
        right_f:
            SMT2 function symbol applied to the Right payload.

        Returns
        -------
        str
            An SMT2 ``ite``-based elimination expression.
        """
        return (
            f"(ite (is-{self.left_constructor} {term})"
            f" ({left_f} (left-val {term}))"
            f" ({right_f} (right-val {term})))"
        )


# ---------------------------------------------------------------------------
# ExceptionPropagationGraph
# ---------------------------------------------------------------------------

class ExceptionPropagationGraph:
    """Directed graph representing exception propagation paths in a program.

    Each node in the graph corresponds to a program point annotated with a
    sort name (the exception type at that point) and a role (``"source"``,
    ``"handler"``, ``"relay"``, etc.).  Each directed edge is labelled with a
    :class:`PropagationRule` describing how an exception transitions between
    program points.

    Attributes
    ----------
    _nodes:
        Dict mapping node_id to metadata dict
        ``{"sort_name": str, "kind": str}``.
    _edges:
        List of ``(from_id, to_id, PropagationRule)`` tuples.
    graph_id:
        Unique UUID4 identifier for this graph instance.
    """

    def __init__(self) -> None:
        """Initialise an empty propagation graph."""
        self._nodes: dict[str, dict[str, Any]] = {}
        self._edges: list[tuple[str, str, PropagationRule]] = []
        self.graph_id: str = str(uuid.uuid4())

    # ------------------------------------------------------------------
    # Graph construction
    # ------------------------------------------------------------------

    def add_node(self, node_id: str, sort_name: str, kind: str) -> None:
        """Add a program-point node to the graph.

        If a node with *node_id* already exists its metadata is updated.

        Parameters
        ----------
        node_id:
            Unique identifier for the program point.
        sort_name:
            The exception sort associated with this node.
        kind:
            Role of the node: ``"source"``, ``"handler"``, or ``"relay"``.
        """
        self._nodes[node_id] = {"sort_name": sort_name, "kind": kind}

    def add_propagation(
        self, from_id: str, to_id: str, rule: PropagationRule
    ) -> None:
        """Add a directed propagation edge between two program points.

        Multiple edges between the same pair of nodes are permitted (to model
        different exception types propagating along the same control-flow edge).

        Parameters
        ----------
        from_id:
            Source node identifier.
        to_id:
            Destination node identifier.
        rule:
            The propagation semantics for this edge.
        """
        self._edges.append((from_id, to_id, rule))

    # ------------------------------------------------------------------
    # Path search
    # ------------------------------------------------------------------

    def propagation_path(self, source: str, target: str) -> list[str]:
        """Find a propagation path from *source* to *target* via BFS.

        Returns the shortest path (in terms of edge count) from *source* to
        *target*.  If no path exists, returns an empty list.

        Parameters
        ----------
        source:
            Starting node identifier.
        target:
            Destination node identifier.

        Returns
        -------
        list[str]
            Ordered list of node IDs forming the path, including both
            endpoints.  Empty iff no path exists.
        """
        if source == target:
            return [source]

        # Build an adjacency list for fast lookup.
        adjacency: dict[str, list[str]] = {}
        for from_id, to_id, _rule in self._edges:
            adjacency.setdefault(from_id, []).append(to_id)

        # Standard BFS with predecessor tracking for path reconstruction.
        visited: set[str] = {source}
        predecessors: dict[str, str | None] = {source: None}
        queue: deque[str] = deque([source])

        while queue:
            current = queue.popleft()
            for neighbour in adjacency.get(current, []):
                if neighbour not in visited:
                    visited.add(neighbour)
                    predecessors[neighbour] = current
                    if neighbour == target:
                        # Reconstruct path by following predecessors back.
                        path: list[str] = []
                        node: str | None = target
                        while node is not None:
                            path.append(node)
                            node = predecessors.get(node)
                        path.reverse()
                        return path
                    queue.append(neighbour)

        # No path found — return empty list.
        return []

    # ------------------------------------------------------------------
    # Handler query
    # ------------------------------------------------------------------

    def all_handlers_for(self, exception_sort: str) -> list[str]:
        """Return all handler node IDs for a given exception sort.

        A handler node is one whose ``kind`` is ``"handler"`` and whose
        ``sort_name`` matches *exception_sort*.

        Parameters
        ----------
        exception_sort:
            The exception sort name to match against.

        Returns
        -------
        list[str]
            Sorted list of node IDs that handle *exception_sort*.
        """
        handlers: list[str] = []
        for node_id, meta in self._nodes.items():
            if meta.get("kind") == "handler" and meta.get("sort_name") == exception_sort:
                handlers.append(node_id)
        return sorted(handlers)

    # ------------------------------------------------------------------
    # Rule analysis
    # ------------------------------------------------------------------

    def dominant_rule(self, node_id: str) -> PropagationRule:
        """Return the most common propagation rule on outgoing edges from *node_id*.

        If the node has no outgoing edges the default rule ``STRICT`` is
        returned, as strict propagation is the safest assumption.

        Parameters
        ----------
        node_id:
            The node whose outgoing edges are analysed.

        Returns
        -------
        PropagationRule
            The most frequently occurring rule on outgoing edges.
        """
        rule_counts: dict[PropagationRule, int] = {}
        for from_id, _to_id, rule in self._edges:
            if from_id == node_id:
                rule_counts[rule] = rule_counts.get(rule, 0) + 1

        if not rule_counts:
            return PropagationRule.STRICT

        # Return the rule with the highest count (ties broken by enum ordering).
        dominant = max(rule_counts, key=lambda r: (rule_counts[r], r.value))
        return dominant

    # ------------------------------------------------------------------
    # SMT2 constraint generation
    # ------------------------------------------------------------------

    def to_smt2_constraints(self) -> list[str]:
        """Produce SMT2 assertions encoding all propagation edges.

        Each edge generates an assertion that constrains the relationship
        between the exception states at the source and destination nodes.

        Returns
        -------
        list[str]
            A list of SMT2 ``assert`` strings, one per edge.
        """
        constraints: list[str] = []
        for from_id, to_id, rule in self._edges:
            from_exc = f"exc_{from_id.replace('-', '_')}"
            to_exc = f"exc_{to_id.replace('-', '_')}"

            if rule == PropagationRule.STRICT:
                # Strict: if an exception is present at source it must appear at dest.
                constraint = (
                    f"(assert (=> (is-exception {from_exc})"
                    f" (= {to_exc} {from_exc})))"
                    f"  ; strict propagation {from_id} -> {to_id}"
                )
            elif rule == PropagationRule.LAZY:
                # Lazy: exception is forwarded only when dest is forced.
                constraint = (
                    f"(assert (=> (and (is-forced {to_exc}) (is-exception {from_exc}))"
                    f" (= {to_exc} {from_exc})))"
                    f"  ; lazy propagation {from_id} -> {to_id}"
                )
            elif rule == PropagationRule.CATCH_AND_RETHROW:
                # The exception is transformed via a handler function.
                constraint = (
                    f"(assert (=> (is-exception {from_exc})"
                    f" (is-exception (handler_{to_id} {from_exc}))))"
                    f"  ; catch-and-rethrow {from_id} -> {to_id}"
                )
            elif rule == PropagationRule.ABSORB:
                # The exception is absorbed — destination is not an exception.
                constraint = (
                    f"(assert (=> (is-exception {from_exc})"
                    f" (not (is-exception {to_exc}))))"
                    f"  ; absorb {from_id} -> {to_id}"
                )
            else:
                # BUBBLE_UP — exception propagates upward unconditionally.
                constraint = (
                    f"(assert (= {to_exc} {from_exc}))"
                    f"  ; bubble-up {from_id} -> {to_id}"
                )
            constraints.append(constraint)
        return constraints

    # ------------------------------------------------------------------
    # Trace analysis
    # ------------------------------------------------------------------

    def copilot_trace_exceptions(
        self, entry_point: str
    ) -> list[dict[str, Any]]:
        """Walk the graph from *entry_point* and produce an annotated trace.

        Performs a BFS from *entry_point*, annotating each visited node with
        its depth, sort name, kind, and the dominant propagation rule on the
        edge used to reach it.

        Parameters
        ----------
        entry_point:
            Node ID from which the trace begins.

        Returns
        -------
        list[dict[str, Any]]
            Ordered list of trace records.  Each record has keys:
            ``node_id``, ``sort_name``, ``kind``, ``rule``, ``depth``.
        """
        # Build adjacency including edge rules.
        adjacency: dict[str, list[tuple[str, PropagationRule]]] = {}
        for from_id, to_id, rule in self._edges:
            adjacency.setdefault(from_id, []).append((to_id, rule))

        trace: list[dict[str, Any]] = []
        visited: set[str] = {entry_point}
        # Queue items: (node_id, depth, incoming_rule)
        queue: deque[tuple[str, int, PropagationRule | None]] = deque(
            [(entry_point, 0, None)]
        )

        while queue:
            current, depth, incoming_rule = queue.popleft()
            node_meta = self._nodes.get(current, {})
            record: dict[str, Any] = {
                "node_id": current,
                "sort_name": node_meta.get("sort_name", "unknown"),
                "kind": node_meta.get("kind", "unknown"),
                "rule": incoming_rule.value if incoming_rule is not None else "entry",
                "depth": depth,
            }
            trace.append(record)

            # Enqueue unvisited neighbours.
            for neighbour, edge_rule in adjacency.get(current, []):
                if neighbour not in visited:
                    visited.add(neighbour)
                    queue.append((neighbour, depth + 1, edge_rule))

        return trace

    # ------------------------------------------------------------------
    # Graph combination
    # ------------------------------------------------------------------

    def merge(self, other: ExceptionPropagationGraph) -> ExceptionPropagationGraph:
        """Produce a new graph that is the union of *self* and *other*.

        Node metadata from *other* takes precedence when the same node_id
        appears in both graphs.

        Parameters
        ----------
        other:
            The graph to merge with this one.

        Returns
        -------
        ExceptionPropagationGraph
            A fresh graph containing all nodes and edges from both inputs.
        """
        merged = ExceptionPropagationGraph()
        # Merge nodes: self first, then other (other overwrites on conflict).
        for node_id, meta in self._nodes.items():
            merged._nodes[node_id] = dict(meta)
        for node_id, meta in other._nodes.items():
            merged._nodes[node_id] = dict(meta)
        # Merge edges: both sets combined (duplicates preserved).
        merged._edges = list(self._edges) + list(other._edges)
        return merged

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------

    def summary(self) -> dict[str, Any]:
        """Return a summary statistics dictionary for this graph.

        Returns
        -------
        dict[str, Any]
            A dictionary with keys: ``node_count``, ``edge_count``,
            ``graph_id``, and ``rule_distribution`` (mapping each
            :class:`PropagationRule` value to its edge count).
        """
        rule_distribution: dict[str, int] = {}
        for _from, _to, rule in self._edges:
            key = rule.value
            rule_distribution[key] = rule_distribution.get(key, 0) + 1

        return {
            "node_count": len(self._nodes),
            "edge_count": len(self._edges),
            "graph_id": self.graph_id,
            "rule_distribution": rule_distribution,
        }


# ---------------------------------------------------------------------------
# Module-level helper functions
# ---------------------------------------------------------------------------

def encode_maybe_in_z3(value_sort: str) -> MaybeEncoding:
    """Create a :class:`MaybeEncoding` for the given *value_sort*.

    This is the primary factory for Maybe encodings.  The returned encoding
    can be used to generate the SMT2 datatype declaration and to construct
    or eliminate Maybe terms.

    Parameters
    ----------
    value_sort:
        The SMT2 sort name of the value type to be wrapped.

    Returns
    -------
    MaybeEncoding
        A fully initialised Maybe encoding for *value_sort*.
    """
    return MaybeEncoding(value_sort=value_sort)


def encode_either_in_z3(left_sort: str, right_sort: str) -> EitherEncoding:
    """Create an :class:`EitherEncoding` for the given component sorts.

    Parameters
    ----------
    left_sort:
        The SMT2 sort for the Left variant (typically the error type).
    right_sort:
        The SMT2 sort for the Right variant (the success type).

    Returns
    -------
    EitherEncoding
        A fully initialised Either encoding for ``left_sort + right_sort``.
    """
    return EitherEncoding(left_sort=left_sort, right_sort=right_sort)


def propagate_exception_strict(
    source_expr: str, exception_sort: ExceptionSort
) -> str:
    """Produce an SMT2 term encoding strict exception propagation.

    Under strict semantics an exception is forwarded without transformation:
    if *source_expr* is already an exception, it is returned unchanged;
    otherwise the expression is promoted into an exception using the sort's
    ``raise`` pseudo-constructor.

    Parameters
    ----------
    source_expr:
        An SMT2 term that may or may not carry an exception.
    exception_sort:
        The :class:`ExceptionSort` governing the exception type.

    Returns
    -------
    str
        An SMT2 ``ite`` expression encoding strict propagation.
    """
    sort_name = exception_sort.sort_name
    return (
        f"(ite (is-exception {source_expr})"
        f" {source_expr}"
        f" (raise-{sort_name} {source_expr}))"
    )


def resolve_handler(exception_sort: str, handler_map: dict[str, str]) -> str:
    """Look up the handler for *exception_sort* in *handler_map*.

    Returns the associated handler expression if found, or an
    ``(unhandled-exception ...)`` term if no matching handler is registered.

    Parameters
    ----------
    exception_sort:
        The exception sort name to resolve.
    handler_map:
        A mapping from exception sort names to SMT2 handler expressions.

    Returns
    -------
    str
        The handler expression, or an unhandled-exception fallback term.
    """
    handler = handler_map.get(exception_sort)
    if handler is not None:
        return handler
    return f"(unhandled-exception {exception_sort})"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    "PropagationRule",
    "SumTypeKind",
    "ExceptionSort",
    "MaybeEncoding",
    "EitherEncoding",
    "ExceptionPropagationGraph",
    "encode_maybe_in_z3",
    "encode_either_in_z3",
    "propagate_exception_strict",
    "resolve_handler",
]
