r"""Core data models for partiality_model_reconstruction, theory2.tex Ch31 §31.1-31.5.

Defines the primary data structures used throughout Ch31:

- :class:`PartialFunctionEncoding` — SMT2 encoding of a partial function
  via an explicit domain predicate and a relation.

- :class:`ExceptionValuedSemantics` — function semantics that may raise
  typed exceptions, encoded as Z3 sum types.

- :class:`AlgebraicSurface` — the "surface" (interface) of a Z3 algebraic
  datatype, providing constructor/recognizer/accessor helpers.

- :class:`ModelReconstruction` — result of reconstructing a Z3 model into
  evidence for the JuGeo judgment engine.

- :class:`BranchSensitivity` — which branches are live in a given model.

.. math::

   f : A \rightharpoonup B
   \;\equiv\;
   (\mathrm{dom}_f : A \to \mathbb{B},\;
    R_f : A \times B \to \mathbb{B})
   \;\text{s.t.}\;
   \forall x.\, \mathrm{dom}_f(x) \Rightarrow
     \exists! y.\, R_f(x, y)

.. math::

   \text{eval}(f, x) =
   \begin{cases}
     \iota y.\, R_f(x,y) & \text{if } \mathrm{dom}_f(x) \\
     \bot               & \text{otherwise}
   \end{cases}
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# Standard library imports
# ---------------------------------------------------------------------------
import dataclasses
import hashlib
import json
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, ClassVar

# ---------------------------------------------------------------------------
# Optional jugeo subpackage imports — gracefully degrade when unavailable
# ---------------------------------------------------------------------------

try:
    from jugeo.solver.z3_session import Z3Session, Z3Formula, Z3Encoder, Z3Decoder, Z3Result
    _Z3_SESSION_AVAILABLE = True
except ImportError:
    _Z3_SESSION_AVAILABLE = False
    class Z3Session: pass  # type: ignore[misc]
    class Z3Formula: pass  # type: ignore[misc]
    class Z3Encoder: pass  # type: ignore[misc]
    class Z3Decoder: pass  # type: ignore[misc]
    class Z3Result: pass  # type: ignore[misc]

try:
    from jugeo.solver.reconstruction import ModelReconstructor as SolverModelReconstruction
    _RECONSTRUCTION_AVAILABLE = True
except ImportError:
    _RECONSTRUCTION_AVAILABLE = False
    class SolverModelReconstruction: pass  # type: ignore[misc]

try:
    from jugeo.judgments.judgment_terms import JudgmentTerm, Judgment
    _JUDGMENTS_AVAILABLE = True
except ImportError:
    _JUDGMENTS_AVAILABLE = False
    class JudgmentTerm: pass  # type: ignore[misc]
    class Judgment: pass  # type: ignore[misc]

try:
    from jugeo.evidence.trust import TrustAlgebra, TrustLevel
    _TRUST_AVAILABLE = True
except ImportError:
    _TRUST_AVAILABLE = False
    class TrustAlgebra: pass  # type: ignore[misc]
    class TrustLevel: pass  # type: ignore[misc]

# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class PartialityKind(str, Enum):
    """Classifies *how* a function is partial.

    UNDEFINED      — the function simply has no defined value at some points
                     (no annotation as to why).
    BOTTOM         — the function explicitly returns ⊥ (bottom / error value).
    DIVERGE        — evaluation diverges (non-termination) at some inputs.
    EXCEPTION      — the function throws a typed exception.
    PARTIAL_DOMAIN — the function's domain predicate is a strict subset of
                     the ambient sort; values outside the domain are
                     unconstrained by the SMT encoding.
    """

    UNDEFINED = "undefined"
    BOTTOM = "bottom"
    DIVERGE = "diverge"
    EXCEPTION = "exception"
    PARTIAL_DOMAIN = "partial_domain"


class ExceptionKind(str, Enum):
    """Classification of exception behaviour in exception-valued semantics.

    DOMAIN_ERROR    — raised when the argument is outside the domain.
    RANGE_ERROR     — raised when the result cannot be represented.
    UNDEFINED_CASE  — raised at a missing case in a pattern match.
    PROPAGATED      — re-emitted from a callee; the current frame does not
                      handle it.
    CAUGHT          — intercepted by a local handler; execution continues.
    RETHROWN        — caught but then re-raised (possibly wrapped).
    """

    DOMAIN_ERROR = "domain_error"
    RANGE_ERROR = "range_error"
    UNDEFINED_CASE = "undefined_case"
    PROPAGATED = "propagated"
    CAUGHT = "caught"
    RETHROWN = "rethrown"


class ReconstructionStatus(str, Enum):
    """Lifecycle status of a :class:`ModelReconstruction` task.

    PENDING      — reconstruction has been requested but not started.
    IN_PROGRESS  — reconstruction is currently running.
    COMPLETE     — all information has been successfully reconstructed.
    PARTIAL      — some fields were reconstructed; others are missing.
    FAILED       — reconstruction encountered an unrecoverable error.
    VERIFIED     — reconstruction is complete and has been independently
                   checked for faithfulness.
    """

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETE = "complete"
    PARTIAL = "partial"
    FAILED = "failed"
    VERIFIED = "verified"


class TrustAnnotationKind(str, Enum):
    """Source of a trust annotation attached to a reconstruction result.

    SOLVER_INFERRED  — the trust level was inferred automatically by the
                       SMT solver.
    COPILOT_PROPOSED — the trust level was proposed by the Copilot assistant
                       layer.
    HUMAN_REVIEWED   — a human engineer has manually reviewed and annotated
                       the reconstruction.
    AUTOMATED        — an automated pipeline (e.g. CI) assigned the trust
                       level based on test results.
    """

    SOLVER_INFERRED = "solver_inferred"
    COPILOT_PROPOSED = "copilot_proposed"
    HUMAN_REVIEWED = "human_reviewed"
    AUTOMATED = "automated"


# ---------------------------------------------------------------------------
# PartialFunctionEncoding
# ---------------------------------------------------------------------------


@dataclass
class PartialFunctionEncoding:
    """SMT2 encoding of a partial function f : A ⇀ B.

    Represents a partial function as a pair ``(domain_pred, relation)``
    where ``domain_pred : A → Bool`` characterises the defined domain and
    ``relation : A → B`` gives the function value on that domain.

    The encoding follows theory2.tex §31.1:

    .. math::

       \\mathrm{enc}(f) = \\bigl(
         \\mathtt{domain\\_pred} : A \\to \\mathbb{B},\\;
         \\mathtt{relation} : A \\to B
       \\bigr)

    such that ``(assert (=> (domain_pred x) (well-defined (relation x))))``
    holds globally.

    Parameters
    ----------
    name:
        Human-readable identifier for this function encoding.
    domain_sort:
        SMT2 sort name for the domain.
    range_sort:
        SMT2 sort name for the co-domain / range.
    domain_pred:
        SMT2 symbol for the domain predicate.
    relation:
        SMT2 symbol for the underlying relation / function.
    encoding_id:
        Unique identifier; auto-generated if not supplied.
    trust_level:
        Trust annotation string (e.g. "UNVERIFIED", "VERIFIED").
    partiality_kind:
        Classifies the nature of partiality.
    metadata:
        Arbitrary key-value metadata for extensibility.
    """

    # Required fields — must be provided by caller
    name: str
    domain_sort: str
    range_sort: str
    domain_pred: str
    relation: str

    # Optional fields with sensible defaults
    encoding_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    trust_level: str = "UNVERIFIED"
    partiality_kind: PartialityKind = PartialityKind.PARTIAL_DOMAIN
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Validate required string fields after construction.

        Raises
        ------
        ValueError
            If ``name``, ``domain_sort``, or ``range_sort`` is an empty string.
        """
        # Each of these must be a non-empty identifier
        if not self.name or not self.name.strip():
            raise ValueError("PartialFunctionEncoding.name must be a non-empty string.")
        if not self.domain_sort or not self.domain_sort.strip():
            raise ValueError(
                "PartialFunctionEncoding.domain_sort must be a non-empty string."
            )
        if not self.range_sort or not self.range_sort.strip():
            raise ValueError(
                "PartialFunctionEncoding.range_sort must be a non-empty string."
            )
        # Auto-generate encoding_id if the caller passed an empty string
        if not self.encoding_id:
            # Use object assignment since dataclass is NOT frozen
            object.__setattr__(self, "encoding_id", str(uuid.uuid4()))

    # ---------------------------------------------------------------------------
    # Application and domain helpers
    # ---------------------------------------------------------------------------

    def apply(self, argument: str) -> str:
        """Produce the SMT2 term representing ``relation(argument)``.

        Parameters
        ----------
        argument:
            A non-empty SMT2 term or variable name.

        Returns
        -------
        str
            The application ``(relation argument)`` as an SMT2 S-expression.

        Raises
        ------
        ValueError
            If *argument* is an empty string.
        """
        # Guard: empty arguments are a programming error
        if not argument or not argument.strip():
            raise ValueError("apply: argument must be a non-empty SMT2 term.")
        # Produce a standard S-expression application
        return f"({self.relation} {argument})"

    def is_defined_at(self, point: str) -> str:
        """Produce the SMT2 formula asserting that *point* is in the domain.

        Parameters
        ----------
        point:
            An SMT2 term of sort ``domain_sort``.

        Returns
        -------
        str
            The predicate application ``(domain_pred point)``.
        """
        # Simply apply the domain predicate to the given point
        return f"({self.domain_pred} {point})"

    def total_extension(self, default_value: str) -> str:
        """Produce the SMT2 term for the total extension of this partial function.

        The total extension maps every element of the domain sort to a value
        in the range sort by substituting *default_value* wherever the function
        is undefined:

        .. math::

           \\hat{f}(x) = \\begin{cases}
             f(x) & \\text{if } \\mathrm{dom}_f(x) \\\\
             \\mathtt{default\\_value} & \\text{otherwise}
           \\end{cases}

        Parameters
        ----------
        default_value:
            An SMT2 constant or expression of sort ``range_sort`` used as the
            default when the function is undefined.

        Returns
        -------
        str
            An SMT2 ``ite`` expression using a fresh variable ``x``.
        """
        # Use a fresh variable name scoped to this encoding to avoid capture
        fresh_var = f"x_{self.encoding_id[:8]}"
        # ite: if domain_pred holds, apply relation; otherwise use default_value
        return (
            f"(ite ({self.domain_pred} {fresh_var})"
            f" ({self.relation} {fresh_var})"
            f" {default_value})"
        )

    # ---------------------------------------------------------------------------
    # Domain restriction and composition
    # ---------------------------------------------------------------------------

    def restrict_domain(self, new_pred: str) -> PartialFunctionEncoding:
        """Return a new encoding whose domain is the conjunction of this
        encoding's domain predicate and *new_pred*.

        Formally: ``dom(f|_P)(x) ≡ dom_f(x) ∧ P(x)``.

        Parameters
        ----------
        new_pred:
            An SMT2 predicate symbol of sort ``domain_sort → Bool``.

        Returns
        -------
        PartialFunctionEncoding
            A new :class:`PartialFunctionEncoding` with the restricted domain.
            The ``relation`` and ``range_sort`` are unchanged.
        """
        # Combine the existing domain predicate with the new one via conjunction
        combined_pred = f"(and {self.domain_pred} {new_pred})"
        return PartialFunctionEncoding(
            name=f"{self.name}_restricted",
            domain_sort=self.domain_sort,
            range_sort=self.range_sort,
            domain_pred=combined_pred,
            relation=self.relation,
            # New encoding gets a fresh ID to avoid identity confusion
            encoding_id=str(uuid.uuid4()),
            trust_level=self.trust_level,
            partiality_kind=self.partiality_kind,
            metadata=dict(self.metadata),
        )

    def compose(self, other: PartialFunctionEncoding) -> PartialFunctionEncoding:
        """Return the sequential composition ``other ∘ self``.

        The composition ``g ∘ f`` is defined at ``x`` iff ``f`` is defined at
        ``x`` *and* ``g`` is defined at ``f(x)``.  Its value is ``g(f(x))``.

        Parameters
        ----------
        other:
            The function to apply *after* self.  The range sort of ``self``
            should match the domain sort of ``other`` for a well-typed
            composition.

        Returns
        -------
        PartialFunctionEncoding
            A new encoding representing the composed function.
        """
        # Composed domain predicate: x is in the domain of (other ∘ self) iff
        # self is defined at x AND other is defined at self(x).
        composed_domain = (
            f"(and ({self.domain_pred} x)"
            f" ({other.domain_pred} ({self.relation} x)))"
        )
        # Composed relation: apply self then other
        composed_relation_name = f"{self.name}_then_{other.name}_rel"
        # We describe the composed relation symbolically here; the actual SMT2
        # definition would be emitted by to_smt2 on the composed encoding.
        composed_relation = f"(lambda ((x {self.domain_sort})) ({other.relation} ({self.relation} x)))"
        return PartialFunctionEncoding(
            name=f"{self.name}_then_{other.name}",
            domain_sort=self.domain_sort,
            range_sort=other.range_sort,
            domain_pred=composed_domain,
            relation=composed_relation_name,
            encoding_id=str(uuid.uuid4()),
            trust_level="UNVERIFIED",
            partiality_kind=PartialityKind.PARTIAL_DOMAIN,
            metadata={
                "composed_from": [self.encoding_id, other.encoding_id],
                "composed_relation_lambda": composed_relation,
            },
        )

    # ---------------------------------------------------------------------------
    # SMT2 emission
    # ---------------------------------------------------------------------------

    def to_smt2(self) -> str:
        """Generate the SMT2 declarations and axioms for this partial function.

        Produces:
        1. A comment header.
        2. ``declare-fun`` for the relation.
        3. ``declare-fun`` for the domain predicate.
        4. A universally quantified assertion expressing the domain restriction
           axiom: whenever ``domain_pred(x)`` holds, ``relation(x)`` is
           well-constrained.

        Returns
        -------
        str
            A multi-line SMT2 string ready to be emitted into a solver session.
        """
        lines: list[str] = [
            f"; Partial function encoding: {self.name}",
            f"; encoding_id: {self.encoding_id}",
            f"; trust_level: {self.trust_level}",
            f"; partiality_kind: {self.partiality_kind.value}",
            "",
            f"(declare-fun {self.relation} ({self.domain_sort}) {self.range_sort})",
            f"(declare-fun {self.domain_pred} ({self.domain_sort}) Bool)",
            "",
            "; Domain restriction axiom:",
            "; For all x in domain_sort, if domain_pred(x) holds then",
            "; relation(x) is a well-defined value in range_sort.",
            f"(assert (forall ((x {self.domain_sort}))",
            f"  (=> ({self.domain_pred} x)",
            f"      (= ({self.relation} x) ({self.relation} x)))))",
            "; (The tautological equality above serves as a placeholder;",
            ";  concrete constraints are added by callers.)",
        ]
        return "\n".join(lines)

    # ---------------------------------------------------------------------------
    # Validation and guard helpers
    # ---------------------------------------------------------------------------

    def validate(self) -> list[str]:
        """Run internal consistency checks and return a list of error strings.

        Checks that all required string fields are non-empty.

        Returns
        -------
        list[str]
            A (possibly empty) list of human-readable error descriptions.
        """
        errors: list[str] = []
        if not self.name:
            errors.append("PartialFunctionEncoding: 'name' is empty.")
        if not self.domain_sort:
            errors.append("PartialFunctionEncoding: 'domain_sort' is empty.")
        if not self.range_sort:
            errors.append("PartialFunctionEncoding: 'range_sort' is empty.")
        if not self.domain_pred:
            errors.append("PartialFunctionEncoding: 'domain_pred' is empty.")
        if not self.relation:
            errors.append("PartialFunctionEncoding: 'relation' is empty.")
        return errors

    def encode_guard(self, condition: str) -> str:
        """Return an SMT2 formula that guards *condition* under the domain predicate.

        Produces ``(and (domain_pred x) condition)`` — useful when building
        assertions that should only fire inside the function's domain.

        Parameters
        ----------
        condition:
            An SMT2 boolean formula.

        Returns
        -------
        str
            The guarded conjunction.
        """
        # Conjunction of the domain predicate applied to the free variable 'x'
        # and the caller-provided condition
        return f"(and ({self.domain_pred} x) {condition})"

    # ---------------------------------------------------------------------------
    # Serialization
    # ---------------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Serialize this encoding to a plain JSON-compatible dict.

        Returns
        -------
        dict[str, Any]
            All fields serialized; enum values use ``.value``.
        """
        return {
            "name": self.name,
            "domain_sort": self.domain_sort,
            "range_sort": self.range_sort,
            "domain_pred": self.domain_pred,
            "relation": self.relation,
            "encoding_id": self.encoding_id,
            "trust_level": self.trust_level,
            # Store the enum as its string value for JSON portability
            "partiality_kind": self.partiality_kind.value,
            "metadata": self.metadata,
        }


# ---------------------------------------------------------------------------
# ExceptionValuedSemantics
# ---------------------------------------------------------------------------


@dataclass
class ExceptionValuedSemantics:
    """Semantics for a function that may raise typed exceptions.

    Models the exception-valued extension of a base function by tracking
    which exception sorts it may raise and how each is handled.  The
    encoding uses Z3 sum types (see theory2.tex §31.2):

    .. math::

       \\text{eval}_{\\text{exc}}(f, x) =
         \\begin{cases}
           \\mathtt{Ok}(f(x)) & \\text{if } \\mathrm{dom}_f(x) \\\\
           \\mathtt{Exc}(e)   & \\text{if exception } e \\text{ is raised}
         \\end{cases}

    Parameters
    ----------
    base_function:
        SMT2 symbol for the underlying (possibly partial) function.
    exception_sorts:
        List of SMT2 sort names for exceptions that may be raised.
    handler_map:
        Mapping from exception sort name to the SMT2 handler expression.
    semantics_id:
        Unique identifier; auto-generated if not supplied.
    exception_kind:
        Default classification of exceptions raised by this function.
    propagation_depth:
        How many stack frames this exception has propagated through.
    metadata:
        Arbitrary key-value metadata for extensibility.
    """

    base_function: str
    exception_sorts: list[str] = field(default_factory=list)
    handler_map: dict[str, str] = field(default_factory=dict)
    semantics_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    exception_kind: ExceptionKind = ExceptionKind.DOMAIN_ERROR
    propagation_depth: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)

    # ---------------------------------------------------------------------------
    # Evaluation
    # ---------------------------------------------------------------------------

    def evaluate(self, argument: str) -> str:
        """Produce the SMT2 term that evaluates ``base_function(argument)``
        with exception dispatch.

        If no exception sorts are declared, returns a simple application.
        Otherwise builds a nested ``ite`` chain: for each exception sort,
        checks whether the function raises that exception and wraps the
        result accordingly.

        Parameters
        ----------
        argument:
            An SMT2 term of the appropriate domain sort.

        Returns
        -------
        str
            An SMT2 term representing the exception-dispatched evaluation.
        """
        # Fast path: no exceptions declared — plain application
        if not self.exception_sorts:
            return f"({self.base_function} {argument})"

        # Build nested ite chain from innermost (last exception) outward
        # Innermost case: the function is defined and returns a value
        result_term = f"(Ok ({self.base_function} {argument}))"

        # Wrap each exception sort from last to first (so first sort is outermost)
        for exc_sort in reversed(self.exception_sorts):
            # Predicate: does evaluating base_function on argument raise exc_sort?
            raises_pred = f"(raises-{exc_sort} {self.base_function} {argument})"
            exception_term = f"(Exc-{exc_sort})"
            # Wrap: if raises_pred then Exc else (previous result)
            result_term = f"(ite {raises_pred} {exception_term} {result_term})"

        return result_term

    # ---------------------------------------------------------------------------
    # Exception membership
    # ---------------------------------------------------------------------------

    def may_raise(self, exception_sort: str) -> bool:
        """Return ``True`` iff *exception_sort* is in the declared exception sorts.

        Parameters
        ----------
        exception_sort:
            The SMT2 sort name to check.

        Returns
        -------
        bool
        """
        return exception_sort in self.exception_sorts

    # ---------------------------------------------------------------------------
    # Handler manipulation
    # ---------------------------------------------------------------------------

    def catch(self, exception_sort: str, handler: str) -> ExceptionValuedSemantics:
        """Return a new semantics with an additional exception handler.

        If *exception_sort* is not yet in ``exception_sorts`` it is appended.
        The handler is stored in ``handler_map`` under the sort name.

        Parameters
        ----------
        exception_sort:
            The SMT2 sort name of the exception to catch.
        handler:
            An SMT2 expression that computes the recovery value.

        Returns
        -------
        ExceptionValuedSemantics
            A new instance with the handler registered.
        """
        # Copy exception_sorts, adding the new one if absent
        new_sorts = list(self.exception_sorts)
        if exception_sort not in new_sorts:
            new_sorts.append(exception_sort)
        # Copy handler_map and add / overwrite the new handler
        new_handlers = dict(self.handler_map)
        new_handlers[exception_sort] = handler
        return ExceptionValuedSemantics(
            base_function=self.base_function,
            exception_sorts=new_sorts,
            handler_map=new_handlers,
            semantics_id=str(uuid.uuid4()),
            exception_kind=self.exception_kind,
            propagation_depth=self.propagation_depth,
            metadata=dict(self.metadata),
        )

    def propagate_exception(self, context: str) -> str:
        """Produce an SMT2 comment + assertion describing exception propagation.

        Models the propagation of an exception from this function up to the
        calling *context*, incrementing the conceptual propagation depth.

        Parameters
        ----------
        context:
            A string identifying the calling context (e.g. a function name).

        Returns
        -------
        str
            A multi-line SMT2 snippet with comment and assertion.
        """
        depth_note = f"depth={self.propagation_depth}"
        lines = [
            f"; Exception propagation from '{self.base_function}' into '{context}'",
            f"; {depth_note}",
            f"; exception_kind: {self.exception_kind.value}",
            f"(assert (propagate-exception {self.base_function} {context} {depth_note}))",
        ]
        return "\n".join(lines)

    # ---------------------------------------------------------------------------
    # Merging
    # ---------------------------------------------------------------------------

    def merge_handlers(self, other: ExceptionValuedSemantics) -> ExceptionValuedSemantics:
        """Return a new semantics combining handlers from *self* and *other*.

        ``self`` wins on conflicts (i.e. if both define a handler for the same
        exception sort, self's handler takes precedence).  The exception_sorts
        list is the union of both lists (order: self's first, then other's new).

        Parameters
        ----------
        other:
            The other semantics object to merge handlers from.

        Returns
        -------
        ExceptionValuedSemantics
            A new instance with merged handlers.
        """
        # Start with self's exception sorts
        merged_sorts: list[str] = list(self.exception_sorts)
        # Add any sorts from other that are not already present
        for exc in other.exception_sorts:
            if exc not in merged_sorts:
                merged_sorts.append(exc)

        # Start with other's handlers (lower priority)
        merged_handlers: dict[str, str] = dict(other.handler_map)
        # Overwrite with self's handlers (higher priority)
        merged_handlers.update(self.handler_map)

        return ExceptionValuedSemantics(
            base_function=self.base_function,
            exception_sorts=merged_sorts,
            handler_map=merged_handlers,
            semantics_id=str(uuid.uuid4()),
            exception_kind=self.exception_kind,
            propagation_depth=max(self.propagation_depth, other.propagation_depth),
            metadata={**other.metadata, **self.metadata},
        )

    # ---------------------------------------------------------------------------
    # SMT2 sum type emission
    # ---------------------------------------------------------------------------

    def to_z3_sum_type(self) -> str:
        """Produce an SMT2 algebraic datatype declaration for the result type.

        The result type is a sum type with one ``Ok`` constructor (carrying the
        successful return value) and one constructor per exception sort.

        Returns
        -------
        str
            An SMT2 ``declare-datatypes`` form.
        """
        sort_name = f"{self.base_function}_Result"
        # Build the constructor list
        constructors: list[str] = ["(Ok (ok-val ResultPayload))"]
        for exc_sort in self.exception_sorts:
            # Each exception constructor wraps the exception value
            constructors.append(f"(Exc-{exc_sort} (exc-{exc_sort}-val {exc_sort}))")
        constructors_str = " ".join(constructors)
        lines = [
            f"; Sum type for exception-valued semantics of '{self.base_function}'",
            f"(declare-datatypes () (({sort_name}",
            f"  {constructors_str}",
            f")))",
        ]
        return "\n".join(lines)

    # ---------------------------------------------------------------------------
    # Status helpers
    # ---------------------------------------------------------------------------

    def is_total(self) -> bool:
        """Return ``True`` iff all declared exception sorts have handlers.

        A semantics is *total* in the exception-handling sense when every
        exception that may be raised has a corresponding handler registered
        in ``handler_map``.

        Returns
        -------
        bool
        """
        # Every declared exception sort must have an entry in handler_map
        return all(exc in self.handler_map for exc in self.exception_sorts)

    def exception_trace(self) -> list[str]:
        """Return the exception sorts in a stable sorted order.

        Useful for deterministic output and for comparing two semantics
        objects for equivalence (modulo ordering).

        Returns
        -------
        list[str]
            Sorted list of exception sort names.
        """
        return sorted(self.exception_sorts)


# ---------------------------------------------------------------------------
# AlgebraicSurface
# ---------------------------------------------------------------------------


@dataclass
class AlgebraicSurface:
    """The "surface" (public interface) of a Z3 algebraic datatype.

    Captures the constructor names, recognizer predicates (``is-C``),
    and accessor functions (``C-field-i``) for a single SMT2 algebraic
    sort.  Provides helpers for constructing terms, pattern-matching, and
    folding over the datatype.

    See theory2.tex §31.3 for the algebraic surface semantics.

    Parameters
    ----------
    sort_name:
        The SMT2 sort name (e.g. ``"List"``).
    constructors:
        Ordered list of constructor names (e.g. ``["nil", "cons"]``).
    recognizers:
        Optional mapping from constructor name to its recognizer predicate.
        If absent, defaults to ``is-{constructor}``.
    accessors:
        Optional mapping from constructor name to its list of accessor names.
        If absent, defaults to ``{constructor}-field-{i}``.
    surface_id:
        Unique identifier; auto-generated if not supplied.
    metadata:
        Arbitrary key-value metadata for extensibility.
    """

    sort_name: str
    constructors: list[str] = field(default_factory=list)
    recognizers: dict[str, str] = field(default_factory=dict)
    accessors: dict[str, list[str]] = field(default_factory=dict)
    surface_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    metadata: dict[str, Any] = field(default_factory=dict)

    # ---------------------------------------------------------------------------
    # Recognition and access
    # ---------------------------------------------------------------------------

    def recognize(self, constructor: str, term: str) -> str:
        """Return the SMT2 formula that recognizes *term* as a *constructor* value.

        Uses the ``recognizers`` dict if available; otherwise falls back to
        the standard SMT2 convention ``(is-{constructor} term)``.

        Parameters
        ----------
        constructor:
            A constructor name registered in ``constructors``.
        term:
            An SMT2 term of sort ``sort_name``.

        Returns
        -------
        str
            The recognizer application as an SMT2 S-expression.
        """
        if constructor in self.recognizers:
            # Use the explicitly registered recognizer symbol
            recognizer_sym = self.recognizers[constructor]
            return f"({recognizer_sym} {term})"
        # Fall back to the SMT2-standard naming convention is-{constructor}
        return f"(is-{constructor} {term})"

    def access(self, constructor: str, field_idx: int, term: str) -> str:
        """Return the SMT2 term projecting field *field_idx* of *constructor* from *term*.

        Uses the ``accessors`` dict if available; otherwise falls back to the
        generated accessor name ``{constructor}-field-{field_idx}``.

        Parameters
        ----------
        constructor:
            A constructor name.
        field_idx:
            Zero-based index of the field to project.
        term:
            An SMT2 term of sort ``sort_name``.

        Returns
        -------
        str
            The accessor application as an SMT2 S-expression.
        """
        if constructor in self.accessors:
            accessor_list = self.accessors[constructor]
            if 0 <= field_idx < len(accessor_list):
                return f"({accessor_list[field_idx]} {term})"
        # Default: synthesize an accessor name from the constructor and index
        default_accessor = f"{constructor}-field-{field_idx}"
        return f"({default_accessor} {term})"

    def construct(self, constructor: str, args: list[str]) -> str:
        """Return the SMT2 term constructing a value with *constructor* applied to *args*.

        Parameters
        ----------
        constructor:
            A constructor name registered in ``constructors``.
        args:
            Ordered list of SMT2 argument terms.

        Returns
        -------
        str
            The constructor application, or just ``({constructor})`` if
            *args* is empty (nullary constructor).
        """
        if not args:
            # Nullary constructor — no arguments
            return f"({constructor})"
        # Variadic constructor — separate args with spaces
        return f"({constructor} {' '.join(args)})"

    # ---------------------------------------------------------------------------
    # Pattern matching via ite chain
    # ---------------------------------------------------------------------------

    def match_case(self, term: str, cases: dict[str, str]) -> str:
        """Build an SMT2 pattern-match expression as a nested ``ite`` chain.

        Each entry in *cases* maps a constructor name to the result expression
        for that branch.  Constructors not present in *cases* fall through to
        the innermost ``undefined`` sentinel.

        Parameters
        ----------
        term:
            The SMT2 term to pattern-match against.
        cases:
            Dict mapping constructor name to result SMT2 expression.

        Returns
        -------
        str
            A nested SMT2 ``ite`` expression.
        """
        # Start with a sentinel for unmatched cases
        result = "undefined"
        # Iterate in reverse so that the first case ends up outermost
        for constructor, branch_result in reversed(list(cases.items())):
            recognizer_formula = self.recognize(constructor, term)
            result = f"(ite {recognizer_formula} {branch_result} {result})"
        return result

    # ---------------------------------------------------------------------------
    # Fold and unfold
    # ---------------------------------------------------------------------------

    def fold(self, base_cases: dict[str, str], rec_case: str) -> str:
        """Produce a descriptive SMT2 comment block for a fold over this datatype.

        This does not emit a runnable SMT2 term but provides a structured
        documentation artefact that can be included in solver output logs.

        Parameters
        ----------
        base_cases:
            Mapping from base-case constructor names to their result expressions.
        rec_case:
            Description or SMT2 expression for the recursive case.

        Returns
        -------
        str
            A multi-line SMT2 comment describing the fold.
        """
        lines = [
            f"; Fold over algebraic surface '{self.sort_name}'",
            f"; surface_id: {self.surface_id}",
            "; Base cases:",
        ]
        for ctor, expr in base_cases.items():
            lines.append(f";   {ctor} => {expr}")
        lines.append(f"; Recursive case: {rec_case}")
        lines.append("; (Fold bodies are inlined by the encoding pipeline.)")
        return "\n".join(lines)

    def unfold(self, depth: int, term: str) -> str:
        """Produce a depth-bounded unfolding description as an SMT2 comment block.

        Describes how *term* can be recursively destructured up to *depth*
        levels deep — used as documentation in generated solver output.

        Parameters
        ----------
        depth:
            Maximum unfolding depth (non-negative).
        term:
            The SMT2 term to unfold.

        Returns
        -------
        str
            A multi-line SMT2 comment describing the unfolding.
        """
        # Build a structured comment describing each unfolding level
        lines = [
            f"; Bounded unfold of '{term}' over sort '{self.sort_name}'",
            f"; depth bound: {depth}",
        ]
        for level in range(depth):
            indent = "  " * level
            lines.append(f"; {indent}level {level}: destructure {term}")
            for ctor in self.constructors:
                lines.append(f"; {indent}  case {ctor}: {self.recognize(ctor, term)}")
        lines.append(f"; (Unfolding stops at depth {depth}.)")
        return "\n".join(lines)

    # ---------------------------------------------------------------------------
    # Membership and projection helpers
    # ---------------------------------------------------------------------------

    def is_constructor(self, name: str) -> bool:
        """Return ``True`` iff *name* is a registered constructor for this sort.

        Parameters
        ----------
        name:
            The constructor name to check.

        Returns
        -------
        bool
        """
        return name in self.constructors

    def project(self, constructor: str, field_name: str, term: str) -> str:
        """Return the SMT2 projection of field *field_name* from *term*.

        Assumes the standard SMT2 naming convention
        ``({constructor}-{field_name} term)``.

        Parameters
        ----------
        constructor:
            The constructor name (used to form the accessor symbol).
        field_name:
            The field name as declared in the datatype.
        term:
            An SMT2 term of sort ``sort_name``.

        Returns
        -------
        str
            The projection application.
        """
        return f"({constructor}-{field_name} {term})"

    # ---------------------------------------------------------------------------
    # SMT2 datatype declaration emission
    # ---------------------------------------------------------------------------

    def to_z3_datatype(self) -> str:
        """Produce the full SMT2 ``declare-datatypes`` declaration for this surface.

        Uses ``self.constructors`` and ``self.accessors`` to emit a fully
        typed declaration.  When accessor lists are absent, placeholder
        names are used.

        Returns
        -------
        str
            A valid SMT2 ``declare-datatypes`` form.
        """
        lines = [
            f"; Algebraic surface for sort '{self.sort_name}'",
            f"; surface_id: {self.surface_id}",
            f"(declare-datatypes () (({self.sort_name}",
        ]
        for ctor in self.constructors:
            if ctor in self.accessors and self.accessors[ctor]:
                # Emit accessor-typed constructor
                field_strs = []
                for i, acc_name in enumerate(self.accessors[ctor]):
                    # Placeholder sort — actual sorts must be supplied by callers
                    field_strs.append(f"({acc_name} FieldSort{i})")
                fields_joined = " ".join(field_strs)
                lines.append(f"  ({ctor} {fields_joined})")
            else:
                # Nullary or untyped constructor
                lines.append(f"  ({ctor})")
        lines.append(")))")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# ModelReconstruction
# ---------------------------------------------------------------------------


@dataclass
class ModelReconstruction:
    """Result of reconstructing a Z3 model into evidence for the JuGeo engine.

    After the SMT solver returns a satisfying model, this class captures
    the model's variable assignments, provenance information, trust annotations,
    and the reconstructed partial/exception-valued semantics.

    See theory2.tex §31.4 for the reconstruction pipeline.

    Parameters
    ----------
    z3_model:
        Raw variable-to-value assignments from the Z3 model.
    query_id:
        Identifier of the query that produced this model.
    reconstruction_id:
        Unique identifier for this reconstruction; auto-generated.
    trust_level:
        Trust annotation string.
    provenance:
        Ordered list of provenance notes describing how the reconstruction
        was produced.
    status:
        Current lifecycle status of the reconstruction.
    partial_assignments:
        Partial variable assignments collected incrementally before the
        full model was available.
    metadata:
        Arbitrary key-value metadata.
    """

    z3_model: dict[str, Any] = field(default_factory=dict)
    query_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    reconstruction_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    trust_level: str = "UNVERIFIED"
    provenance: list[str] = field(default_factory=list)
    status: ReconstructionStatus = ReconstructionStatus.PENDING
    partial_assignments: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    # ---------------------------------------------------------------------------
    # Assignment extraction
    # ---------------------------------------------------------------------------

    def extract_assignment(self, var_name: str) -> Any:
        """Look up the value assigned to *var_name* in the model.

        Checks ``z3_model`` first, then ``partial_assignments`` as a fallback.
        Appends a provenance note when a value is found.

        Parameters
        ----------
        var_name:
            The variable or function symbol name to look up.

        Returns
        -------
        Any
            The assigned value, or ``None`` if not found.
        """
        # First try the full z3_model
        if var_name in self.z3_model:
            value = self.z3_model[var_name]
            # Record that we extracted this assignment
            self.provenance.append(
                f"extract_assignment: '{var_name}' = {value!r} (from z3_model)"
            )
            return value

        # Fall back to partial_assignments
        if var_name in self.partial_assignments:
            value = self.partial_assignments[var_name]
            self.provenance.append(
                f"extract_assignment: '{var_name}' = {value!r} (from partial_assignments)"
            )
            return value

        # Variable not found in either store
        self.provenance.append(
            f"extract_assignment: '{var_name}' not found in model or partial_assignments"
        )
        return None

    # ---------------------------------------------------------------------------
    # Specialised reconstruction helpers
    # ---------------------------------------------------------------------------

    def reconstruct_partial(
        self, partial_encoding: PartialFunctionEncoding
    ) -> dict[str, Any]:
        """Extract the relevant model values for a :class:`PartialFunctionEncoding`.

        Parameters
        ----------
        partial_encoding:
            The encoding whose relation and domain predicate we want to look up.

        Returns
        -------
        dict[str, Any]
            A dict with keys ``function_name``, ``domain_pred_value``,
            ``relation_value``, ``encoding_id``, ``status``.
        """
        # Look up the relation and domain predicate values from the model
        relation_value = self.extract_assignment(partial_encoding.relation)
        domain_pred_value = self.extract_assignment(partial_encoding.domain_pred)

        # Determine reconstruction status based on availability of values
        if relation_value is not None and domain_pred_value is not None:
            rec_status = "COMPLETE"
        elif relation_value is not None or domain_pred_value is not None:
            rec_status = "PARTIAL"
        else:
            rec_status = "NOT_FOUND"

        return {
            "function_name": partial_encoding.name,
            "domain_pred_value": domain_pred_value,
            "relation_value": relation_value,
            "encoding_id": partial_encoding.encoding_id,
            "status": rec_status,
        }

    def reconstruct_exception(
        self, exc_semantics: ExceptionValuedSemantics
    ) -> dict[str, Any]:
        """Extract model values relevant to an :class:`ExceptionValuedSemantics`.

        Parameters
        ----------
        exc_semantics:
            The exception semantics to reconstruct.

        Returns
        -------
        dict[str, Any]
            Dict with ``base_function``, ``exception_assignments``,
            ``handler_assignments``, and ``is_total``.
        """
        # Look up the base function's model value
        base_value = self.extract_assignment(exc_semantics.base_function)

        # Collect the model value for each exception sort
        exception_assignments: dict[str, Any] = {}
        for exc_sort in exc_semantics.exception_sorts:
            exc_value = self.extract_assignment(exc_sort)
            exception_assignments[exc_sort] = exc_value

        # Collect the model value for each handler
        handler_assignments: dict[str, Any] = {}
        for exc_sort, handler_expr in exc_semantics.handler_map.items():
            # Handler expressions are SMT2 terms; we try to look them up directly
            handler_value = self.extract_assignment(handler_expr)
            handler_assignments[exc_sort] = handler_value

        return {
            "base_function": exc_semantics.base_function,
            "base_value": base_value,
            "exception_assignments": exception_assignments,
            "handler_assignments": handler_assignments,
            "is_total": exc_semantics.is_total(),
        }

    def reconstruct_surface(self, surface: AlgebraicSurface) -> dict[str, Any]:
        """Extract model values relevant to an :class:`AlgebraicSurface`.

        Parameters
        ----------
        surface:
            The algebraic surface to reconstruct.

        Returns
        -------
        dict[str, Any]
            Dict with ``sort_name``, ``constructor_assignments``,
            ``recognizer_assignments``.
        """
        # Collect the model value for each constructor symbol
        constructor_assignments: dict[str, Any] = {}
        for ctor in surface.constructors:
            ctor_value = self.extract_assignment(ctor)
            constructor_assignments[ctor] = ctor_value

        # Collect the model value for each recognizer predicate
        recognizer_assignments: dict[str, Any] = {}
        for ctor, recognizer in surface.recognizers.items():
            rec_value = self.extract_assignment(recognizer)
            recognizer_assignments[ctor] = rec_value

        return {
            "sort_name": surface.sort_name,
            "surface_id": surface.surface_id,
            "constructor_assignments": constructor_assignments,
            "recognizer_assignments": recognizer_assignments,
        }

    # ---------------------------------------------------------------------------
    # Evidence packaging
    # ---------------------------------------------------------------------------

    def to_evidence(self) -> dict[str, Any]:
        """Package this reconstruction as a JuGeo evidence dict.

        Returns
        -------
        dict[str, Any]
            A JSON-serializable evidence package including reconstruction ID,
            query ID, trust level, status, provenance chain, partial
            assignments, a list of z3_model keys, and a timestamp.
        """
        return {
            "reconstruction_id": self.reconstruction_id,
            "query_id": self.query_id,
            "trust_level": self.trust_level,
            "status": self.status.value,
            "provenance": list(self.provenance),
            "partial_assignments": dict(self.partial_assignments),
            # Only export keys (values may be non-serializable Z3 objects)
            "z3_model_keys": list(self.z3_model.keys()),
            "timestamp": time.time(),
        }

    # ---------------------------------------------------------------------------
    # Validation
    # ---------------------------------------------------------------------------

    def validate_reconstruction(self) -> list[str]:
        """Run internal consistency checks and return a list of error strings.

        Returns
        -------
        list[str]
            Error descriptions, or an empty list if everything is consistent.
        """
        errors: list[str] = []
        if not self.query_id:
            errors.append("ModelReconstruction: 'query_id' is empty.")
        if not self.reconstruction_id:
            errors.append("ModelReconstruction: 'reconstruction_id' is empty.")
        if self.status == ReconstructionStatus.FAILED:
            errors.append(
                f"ModelReconstruction '{self.reconstruction_id}' has FAILED status."
            )
        return errors

    # ---------------------------------------------------------------------------
    # Merging
    # ---------------------------------------------------------------------------

    def merge_partial_models(self, other: ModelReconstruction) -> ModelReconstruction:
        """Merge *other* into *self*, producing a new :class:`ModelReconstruction`.

        ``self`` wins on key conflicts in ``z3_model``.  Provenance lists
        are concatenated.

        Parameters
        ----------
        other:
            The reconstruction to merge from.

        Returns
        -------
        ModelReconstruction
            A new instance containing the merged data.
        """
        # Merge z3_models: start with other's assignments, overwrite with self's
        merged_model: dict[str, Any] = dict(other.z3_model)
        merged_model.update(self.z3_model)

        # Union the provenance chains
        merged_provenance = list(other.provenance) + list(self.provenance)
        merged_provenance.append(
            f"merge_partial_models: merged {self.reconstruction_id} "
            f"with {other.reconstruction_id}"
        )

        return ModelReconstruction(
            z3_model=merged_model,
            query_id=self.query_id,
            reconstruction_id=str(uuid.uuid4()),
            trust_level=self.trust_level,
            provenance=merged_provenance,
            status=self.status,
            partial_assignments={**other.partial_assignments, **self.partial_assignments},
            metadata={**other.metadata, **self.metadata},
        )

    def annotate_trust(self, trust_kind: TrustAnnotationKind) -> ModelReconstruction:
        """Set the trust level and record a provenance note, then return *self*.

        This method mutates the instance in place (the dataclass is not frozen)
        and returns ``self`` to allow method chaining.

        Parameters
        ----------
        trust_kind:
            The new trust annotation to apply.

        Returns
        -------
        ModelReconstruction
            ``self`` after the trust annotation has been applied.
        """
        # Update trust_level to the enum's string value
        self.trust_level = trust_kind.value
        # Record the annotation event in the provenance chain
        self.provenance.append(
            f"annotate_trust: trust_level set to '{trust_kind.value}' at {time.time():.3f}"
        )
        return self


# ---------------------------------------------------------------------------
# BranchSensitivity
# ---------------------------------------------------------------------------


@dataclass
class BranchSensitivity:
    """Records which conditional branches are live in a given Z3 model.

    Used in the model reconstruction pipeline (§31.4) to track which
    branches of ``if-then-else`` or ``match`` expressions are satisfied by
    the model, enabling the reconstructor to skip dead branches and focus
    evidence extraction on live ones.

    Parameters
    ----------
    branch_conditions:
        Ordered list of SMT2 boolean conditions, one per branch.
    active_branches:
        Set of indices (into ``branch_conditions``) that are live in
        the current model.
    sensitivity_id:
        Unique identifier; auto-generated if not supplied.
    metadata:
        Arbitrary key-value metadata.
    """

    branch_conditions: list[str] = field(default_factory=list)
    active_branches: set[int] = field(default_factory=set)
    sensitivity_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    metadata: dict[str, Any] = field(default_factory=dict)

    # ---------------------------------------------------------------------------
    # Branch activation / deactivation
    # ---------------------------------------------------------------------------

    def activate(self, branch_idx: int) -> None:
        """Mark branch *branch_idx* as active (live in the current model).

        Parameters
        ----------
        branch_idx:
            Zero-based index into ``branch_conditions``.

        Raises
        ------
        IndexError
            If *branch_idx* is out of range.
        """
        # Validate the index before activating
        if branch_idx < 0 or branch_idx >= len(self.branch_conditions):
            raise IndexError(
                f"activate: branch_idx {branch_idx} out of range "
                f"[0, {len(self.branch_conditions)})."
            )
        self.active_branches.add(branch_idx)

    def deactivate(self, branch_idx: int) -> None:
        """Mark branch *branch_idx* as inactive (dead in the current model).

        A no-op if *branch_idx* is not currently active.

        Parameters
        ----------
        branch_idx:
            Zero-based index into ``branch_conditions``.
        """
        # discard is safe even if the element is absent
        self.active_branches.discard(branch_idx)

    # ---------------------------------------------------------------------------
    # Sensitivity query
    # ---------------------------------------------------------------------------

    def is_sensitive(self, condition: str) -> bool:
        """Return ``True`` iff *condition* is both registered and active.

        Parameters
        ----------
        condition:
            An SMT2 boolean condition string.

        Returns
        -------
        bool
            ``True`` if the condition appears in ``branch_conditions`` at an
            index that is currently in ``active_branches``.
        """
        # Scan branch_conditions for a matching active entry
        for idx, cond in enumerate(self.branch_conditions):
            if cond == condition and idx in self.active_branches:
                return True
        return False

    # ---------------------------------------------------------------------------
    # Lattice operations
    # ---------------------------------------------------------------------------

    def branch_join(self, other: BranchSensitivity) -> BranchSensitivity:
        """Return the join (union) of *self* and *other*.

        The join has the union of branch conditions (where they overlap by
        index) and the union of active branch sets.  When both objects have
        the same length, the result is well-defined.  When lengths differ,
        the longer list is used as the base and active indices from the
        shorter object that fall within range are included.

        Parameters
        ----------
        other:
            The other sensitivity to join with.

        Returns
        -------
        BranchSensitivity
            A new instance representing the join.
        """
        # Determine the longer condition list (union)
        if len(self.branch_conditions) >= len(other.branch_conditions):
            joined_conditions = list(self.branch_conditions)
        else:
            joined_conditions = list(other.branch_conditions)

        # Union of active branch indices, constrained to the joined range
        max_idx = len(joined_conditions)
        joined_active = {
            idx for idx in self.active_branches | other.active_branches
            if idx < max_idx
        }

        return BranchSensitivity(
            branch_conditions=joined_conditions,
            active_branches=joined_active,
            sensitivity_id=str(uuid.uuid4()),
            metadata={**self.metadata, **other.metadata},
        )

    def branch_meet(self, other: BranchSensitivity) -> BranchSensitivity:
        """Return the meet (intersection) of *self* and *other*.

        The meet has the intersection of active branch sets.  The condition
        list is taken from *self*.

        Parameters
        ----------
        other:
            The other sensitivity to meet with.

        Returns
        -------
        BranchSensitivity
            A new instance representing the meet.
        """
        # Intersection of active indices
        met_active = self.active_branches & other.active_branches

        return BranchSensitivity(
            branch_conditions=list(self.branch_conditions),
            active_branches=set(met_active),
            sensitivity_id=str(uuid.uuid4()),
            metadata={**self.metadata},
        )

    # ---------------------------------------------------------------------------
    # Projection
    # ---------------------------------------------------------------------------

    def project_to(self, indices: list[int]) -> BranchSensitivity:
        """Return a new :class:`BranchSensitivity` restricted to the given branch indices.

        The result is re-indexed: the first valid index in *indices* maps to 0
        in the result, the second to 1, etc.

        Parameters
        ----------
        indices:
            List of zero-based indices to keep (out-of-range indices are
            silently skipped).

        Returns
        -------
        BranchSensitivity
            A new instance with only the selected branches, re-indexed.
        """
        new_conditions: list[str] = []
        new_active: set[int] = set()

        for new_idx, old_idx in enumerate(indices):
            # Skip out-of-range indices gracefully
            if 0 <= old_idx < len(self.branch_conditions):
                new_conditions.append(self.branch_conditions[old_idx])
                # Re-map the active status from the old index to the new one
                if old_idx in self.active_branches:
                    new_active.add(new_idx)

        return BranchSensitivity(
            branch_conditions=new_conditions,
            active_branches=new_active,
            sensitivity_id=str(uuid.uuid4()),
            metadata=dict(self.metadata),
        )

    # ---------------------------------------------------------------------------
    # Summary
    # ---------------------------------------------------------------------------

    def summarize(self) -> dict[str, Any]:
        """Return a high-level statistical summary of this branch sensitivity.

        Returns
        -------
        dict[str, Any]
            Dict with ``total_branches``, ``active_count``, ``inactive_count``,
            ``active_indices`` (sorted list), and ``sensitivity_id``.
        """
        total = len(self.branch_conditions)
        active_count = len(self.active_branches)
        return {
            "total_branches": total,
            "active_count": active_count,
            "inactive_count": total - active_count,
            # Sorted for deterministic output
            "active_indices": sorted(list(self.active_branches)),
            "sensitivity_id": self.sensitivity_id,
        }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    "PartialityKind",
    "ExceptionKind",
    "ReconstructionStatus",
    "TrustAnnotationKind",
    "PartialFunctionEncoding",
    "ExceptionValuedSemantics",
    "AlgebraicSurface",
    "ModelReconstruction",
    "BranchSensitivity",
]
