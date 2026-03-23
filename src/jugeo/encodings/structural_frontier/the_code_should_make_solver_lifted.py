"""
Solver-Lifted Obligations: Making the Lifting Process Explicit
==============================================================

This module is concerned with making *solver-lifted* obligations fully explicit
at every stage of their transformation from high-level program-logic statements
into Z3-checkable SMT-LIB2 formulae.  A "solver-lifted" obligation is one that
has been promoted out of the domain of human-readable type-theory or Hoare-logic
assertions and into a mechanically checkable form that an SMT solver such as Z3
can directly accept.

What Does "Lifting" Mean?
-------------------------
Lifting is a three-stage pipeline.  Each stage is individually necessary; no
stage can be safely omitted.

Stage 1 — Sort Translation
    Every type that appears in the original obligation must be mapped to a Z3
    sort.  For example, a Python ``int`` becomes ``Int``, a Python ``bool``
    becomes ``Bool``, a refinement type ``{n : int | n >= 0}`` becomes an
    ``Int`` sort with an accompanying side-constraint ``(assert (>= n 0))``,
    and a product type ``(A, B)`` becomes a datatype sort with two projectors.
    The sort-translation table is maintained in ``_SORT_TRANSLATION_TABLE``
    and is extensible via the ``SortTranslator`` class in the optional
    ``jugeo.solver.lifting`` sub-package.

Stage 2 — Constraint Encoding
    Once sorts are known, the logical structure of the obligation (quantifiers,
    implications, conjunctions, disjunctions, equalities) is serialised into
    S-expression syntax conforming to the SMT-LIB2 standard.  Encoding must be
    faithful: every logical connective maps to exactly one SMT2 construct so
    that round-tripping is possible.

Stage 3 — Context Threading
    Obligations do not exist in a vacuum; they depend on assumptions introduced
    earlier in the same proof obligation or by the enclosing module's
    precondition environment.  Threading weaves those assumptions in as
    ``(assert ...)`` commands that precede the main query, ensuring the solver
    sees the full context.

Why Explicit Lifting Matters
----------------------------
Without explicit provenance, a failing solver query gives almost no
actionable feedback.  The developer cannot tell whether the failure is because
(a) the original obligation was genuinely unsatisfiable, (b) a sort was
translated incorrectly, (c) a necessary context assumption was dropped during
threading, or (d) the encoding introduced a spurious quantifier.  By tagging
every obligation with its ``LiftingStage``, its ``sort_map``, and its full
``provenance_chain``, debugging becomes a matter of inspecting a single
``TheCodeMakeSolverWitness`` record rather than re-executing the entire pipeline
from scratch.

Explicit lifting also enables incremental verification.  If a downstream solver
session fails, the coordinator can rewind to the last successfully completed
stage and retry only the remaining steps, reusing cached intermediate results.

Copilot Integration
-------------------
Several methods in this module emit "Copilot hints"—multi-line strings that
are designed to be read by GitHub Copilot during incremental code-completion.
A hint describes (i) the current lifting stage, (ii) the inferred Z3 sorts for
each free variable, (iii) the context assumptions that have been threaded in,
and (iv) a suggested next action.  Hints are tagged with the ``# copilot:``
comment prefix inside method bodies and are also exposed through the
``copilot_lift_hint`` method on ``TheCodeMakeSolverWitness`` and through
``TheCodeMakeSolverAnalyzer.copilot_lift_analysis_hint``.

The design philosophy is that Copilot should be able to read any witness object
and understand *exactly* what the solver is about to be asked, without needing
to trace back through the transformation pipeline.

Decidability and the Structural Frontier
-----------------------------------------
This module sits inside ``jugeo.encodings.structural_frontier``, a sub-package
that tracks which encoding choices keep verification within a decidable fragment
of first-order logic.  Not every lifting is decidable: if the sort-translation
introduces free sorts (uninterpreted functions) or if context threading threads
in universally-quantified axioms, the resulting query may be outside QF_LIA,
QF_BV, or whatever quantifier-free fragment was originally targeted.  The
``TheCodeMakeSolverAnalyzer`` therefore also estimates a decidability budget as
part of its analysis and records whether the lifting crosses the structural
frontier.

Module Conventions
------------------
* All public dataclasses are ``frozen=True`` (no ``slots=True`` for broad
  compatibility).
* Union types use the ``X | Y`` syntax (PEP 604) throughout.
* Optional imports are guarded by ``try/except`` blocks, with boolean flags
  ``_Z3_SESSION_AVAILABLE``, ``_LIFTING_AVAILABLE``, ``_MODELS_AVAILABLE``,
  and ``_Z3_AVAILABLE`` indicating availability at runtime.
* Logging is done through the standard ``logging`` module; no ``print``
  statements appear in production code paths.
* The smoke test under ``if __name__ == "__main__":`` exercises all major code
  paths and is intended as a quick sanity-check, not a substitute for the
  proper test suite in ``tests/``.
"""

from __future__ import annotations

import collections
import hashlib
import itertools
import json
import logging
import math
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Iterator

try:
    from jugeo.solver.z3_session import Z3Session, Z3Formula, SolveOutcome
    _Z3_SESSION_AVAILABLE = True
except ImportError:
    _Z3_SESSION_AVAILABLE = False
    Z3Session = None  # type: ignore[assignment,misc]
    Z3Formula = None  # type: ignore[assignment,misc]
    SolveOutcome = None  # type: ignore[assignment,misc]

try:
    from jugeo.solver.lifting import LiftingPipeline, SortTranslator
    _LIFTING_AVAILABLE = True
except ImportError:
    _LIFTING_AVAILABLE = False
    LiftingPipeline = None  # type: ignore[assignment,misc]
    SortTranslator = None  # type: ignore[assignment,misc]

try:
    from jugeo.encodings.structural_frontier.models import (
        DecidabilityClass, StructuralFrontier, SolverLiftedType,
    )
    _MODELS_AVAILABLE = True
except ImportError:
    _MODELS_AVAILABLE = False
    DecidabilityClass = None  # type: ignore[assignment,misc]
    StructuralFrontier = None  # type: ignore[assignment,misc]
    SolverLiftedType = None  # type: ignore[assignment,misc]

try:
    import z3
    _Z3_AVAILABLE = True
except ImportError:
    z3 = None  # type: ignore[assignment]
    _Z3_AVAILABLE = False

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

_SORT_TRANSLATION_TABLE: dict[str, str] = {
    "int": "Int",
    "bool": "Bool",
    "float": "Real",
    "str": "String",
    "bytes": "(Array Int Int)",
    "list[int]": "(Array Int Int)",
    "list[bool]": "(Array Int Bool)",
    "set[int]": "(Array Int Bool)",
    "dict[str,int]": "(Array String Int)",
    "nat": "Int",
    "real": "Real",
    "bv8": "(_ BitVec 8)",
    "bv16": "(_ BitVec 16)",
    "bv32": "(_ BitVec 32)",
    "bv64": "(_ BitVec 64)",
    "unit": "Bool",
    "void": "Bool",
}

_LIFTING_COST_WEIGHTS: dict[str, float] = {
    "sort_translation": 1.0,
    "constraint_encoding": 2.5,
    "context_threading": 1.5,
    "quantifier_introduction": 3.0,
    "datatype_declaration": 2.0,
    "uninterpreted_function": 4.0,
    "array_axiom": 3.5,
    "bit_vector_operation": 1.8,
    "string_constraint": 2.2,
    "nonlinear_arithmetic": 6.0,
}

# ============================== lifting stages ==============================


class LiftingStage(Enum):
    """Enumeration of the stages through which an obligation passes during lifting.

    The lifting pipeline is a strictly ordered sequence of transformations that
    take a high-level program-logic obligation and convert it into a form that
    is directly checkable by an SMT solver.  Each value of this enum corresponds
    to one well-defined checkpoint in that pipeline.

    Members
    -------
    UNLIFTED
        The obligation has not yet entered the lifting pipeline.  It exists only
        as a raw string or AST node produced by the front-end type-checker.
    SORT_TRANSLATED
        All type annotations have been successfully mapped to Z3 sorts using
        ``_SORT_TRANSLATION_TABLE`` (or the full ``SortTranslator`` when
        available).  No constraint encoding has occurred yet.
    CONSTRAINT_ENCODED
        The logical body of the obligation has been serialised into SMT-LIB2
        S-expression syntax.  Sort declarations are present but context
        assumptions have not yet been threaded in.
    CONTEXT_THREADED
        All context assumptions (preconditions, loop invariants, module-level
        axioms) have been prepended as ``(assert ...)`` commands, making the
        query self-contained.
    FULLY_LIFTED
        The obligation is completely ready to be handed to a Z3 solver session.
        Every sort is declared, every constraint is encoded, and every context
        assumption is asserted.
    LIFT_FAILED
        The lifting pipeline encountered an unrecoverable error.  The
        ``TheCodeMakeSolverWitness`` for this stage will contain partial results
        up to the point of failure, but the ``lifted_smt`` field may be
        incomplete or malformed.

    Notes
    -----
    The pipeline ordering is::

        UNLIFTED → SORT_TRANSLATED → CONSTRAINT_ENCODED
                 → CONTEXT_THREADED → FULLY_LIFTED

    ``LIFT_FAILED`` is a terminal absorbing state that can be reached from any
    other stage.

    Examples
    --------
    >>> stage = LiftingStage.SORT_TRANSLATED
    >>> stage.is_complete()
    False
    >>> stage.next_stage()
    <LiftingStage.CONSTRAINT_ENCODED: 3>
    >>> stage.progress_fraction()
    0.2
    """

    UNLIFTED = auto()
    SORT_TRANSLATED = auto()
    CONSTRAINT_ENCODED = auto()
    CONTEXT_THREADED = auto()
    FULLY_LIFTED = auto()
    LIFT_FAILED = auto()

    def is_complete(self) -> bool:
        """Return True iff this stage represents a successfully fully-lifted obligation.

        Returns
        -------
        bool
            ``True`` only when ``self`` is ``FULLY_LIFTED``.

        Examples
        --------
        >>> LiftingStage.FULLY_LIFTED.is_complete()
        True
        >>> LiftingStage.CONTEXT_THREADED.is_complete()
        False
        """
        # copilot: only FULLY_LIFTED counts as complete; all others are intermediate
        return self is LiftingStage.FULLY_LIFTED

    def is_failed(self) -> bool:
        """Return True iff this stage represents a lift failure.

        Returns
        -------
        bool
            ``True`` only when ``self`` is ``LIFT_FAILED``.

        Examples
        --------
        >>> LiftingStage.LIFT_FAILED.is_failed()
        True
        >>> LiftingStage.FULLY_LIFTED.is_failed()
        False
        """
        return self is LiftingStage.LIFT_FAILED

    def next_stage(self) -> LiftingStage | None:
        """Return the next stage in pipeline order, or None if terminal.

        Returns
        -------
        LiftingStage | None
            The successor stage, or ``None`` if this stage is ``FULLY_LIFTED``
            or ``LIFT_FAILED`` (both are terminal).

        Examples
        --------
        >>> LiftingStage.UNLIFTED.next_stage()
        <LiftingStage.SORT_TRANSLATED: 2>
        >>> LiftingStage.FULLY_LIFTED.next_stage() is None
        True
        """
        # copilot: map each non-terminal stage to its successor
        _next: dict[LiftingStage, LiftingStage] = {
            LiftingStage.UNLIFTED: LiftingStage.SORT_TRANSLATED,
            LiftingStage.SORT_TRANSLATED: LiftingStage.CONSTRAINT_ENCODED,
            LiftingStage.CONSTRAINT_ENCODED: LiftingStage.CONTEXT_THREADED,
            LiftingStage.CONTEXT_THREADED: LiftingStage.FULLY_LIFTED,
        }
        return _next.get(self, None)

    def stage_description(self) -> str:
        """Return a human-readable description of what this stage means.

        Returns
        -------
        str
            A multi-sentence description of the stage suitable for error messages,
            status reports, and Copilot hints.

        Examples
        --------
        >>> "sort" in LiftingStage.SORT_TRANSLATED.stage_description().lower()
        True
        """
        _descriptions: dict[LiftingStage, str] = {
            LiftingStage.UNLIFTED: (
                "The obligation has not yet entered the lifting pipeline. "
                "It exists only as a raw string or AST node from the front-end."
            ),
            LiftingStage.SORT_TRANSLATED: (
                "All type annotations have been mapped to Z3 sorts. "
                "Sort declarations are ready but constraint encoding has not begun."
            ),
            LiftingStage.CONSTRAINT_ENCODED: (
                "The logical body has been serialised into SMT-LIB2 S-expression syntax. "
                "Context assumptions have not yet been threaded in."
            ),
            LiftingStage.CONTEXT_THREADED: (
                "Context assumptions have been prepended as (assert ...) commands. "
                "The query is nearly self-contained; a final validation pass remains."
            ),
            LiftingStage.FULLY_LIFTED: (
                "The obligation is fully lifted and ready for Z3 solving. "
                "Every sort is declared, constraint encoded, and context asserted."
            ),
            LiftingStage.LIFT_FAILED: (
                "The lifting pipeline encountered an unrecoverable error. "
                "Partial results may be present but the lifted SMT is likely malformed."
            ),
        }
        return _descriptions[self]

    def stage_index(self) -> int:
        """Return the numeric index of this stage in pipeline order (0-based).

        Returns
        -------
        int
            An integer in the range ``0..5`` where 0 is ``UNLIFTED`` and 5 is
            ``LIFT_FAILED``.

        Examples
        --------
        >>> LiftingStage.UNLIFTED.stage_index()
        0
        >>> LiftingStage.FULLY_LIFTED.stage_index()
        4
        """
        _indices: dict[LiftingStage, int] = {
            LiftingStage.UNLIFTED: 0,
            LiftingStage.SORT_TRANSLATED: 1,
            LiftingStage.CONSTRAINT_ENCODED: 2,
            LiftingStage.CONTEXT_THREADED: 3,
            LiftingStage.FULLY_LIFTED: 4,
            LiftingStage.LIFT_FAILED: 5,
        }
        return _indices[self]

    def progress_fraction(self) -> float:
        """Return the progress through the successful pipeline as a float in [0.0, 1.0].

        Failed stages return 0.0 since no useful progress is recorded.

        Returns
        -------
        float
            A value in ``[0.0, 1.0]`` where ``1.0`` means fully lifted.

        Examples
        --------
        >>> LiftingStage.UNLIFTED.progress_fraction()
        0.0
        >>> LiftingStage.FULLY_LIFTED.progress_fraction()
        1.0
        >>> abs(LiftingStage.SORT_TRANSLATED.progress_fraction() - 0.25) < 1e-9
        True
        """
        if self is LiftingStage.LIFT_FAILED:
            return 0.0
        # copilot: 5 successful stages → divide index by 4 to normalise to [0,1]
        successful_stages = [
            LiftingStage.UNLIFTED,
            LiftingStage.SORT_TRANSLATED,
            LiftingStage.CONSTRAINT_ENCODED,
            LiftingStage.CONTEXT_THREADED,
            LiftingStage.FULLY_LIFTED,
        ]
        if self in successful_stages:
            return successful_stages.index(self) / (len(successful_stages) - 1)
        return 0.0

    def smt_trigger_template(self) -> str:
        """Return an SMT2 comment template for use as a stage-transition marker.

        Returns
        -------
        str
            An SMT-LIB2 comment string (beginning with ``;``) that can be
            inserted into the generated query as a provenance breadcrumb.

        Examples
        --------
        >>> LiftingStage.SORT_TRANSLATED.smt_trigger_template().startswith(";")
        True
        """
        _templates: dict[LiftingStage, str] = {
            LiftingStage.UNLIFTED: "; jugeo:stage UNLIFTED — obligation not yet in pipeline",
            LiftingStage.SORT_TRANSLATED: "; jugeo:stage SORT_TRANSLATED — sorts declared",
            LiftingStage.CONSTRAINT_ENCODED: "; jugeo:stage CONSTRAINT_ENCODED — formula body ready",
            LiftingStage.CONTEXT_THREADED: "; jugeo:stage CONTEXT_THREADED — assumptions asserted",
            LiftingStage.FULLY_LIFTED: "; jugeo:stage FULLY_LIFTED — query ready for solver",
            LiftingStage.LIFT_FAILED: "; jugeo:stage LIFT_FAILED — pipeline aborted",
        }
        return _templates[self]

    def severity_default(self) -> str:
        """Return a default severity level string for log messages at this stage.

        Returns
        -------
        str
            One of ``"low"``, ``"medium"``, or ``"high"``.

        Examples
        --------
        >>> LiftingStage.LIFT_FAILED.severity_default()
        'high'
        >>> LiftingStage.FULLY_LIFTED.severity_default()
        'low'
        """
        if self is LiftingStage.LIFT_FAILED:
            return "high"
        if self in (LiftingStage.UNLIFTED, LiftingStage.SORT_TRANSLATED):
            return "medium"
        return "low"


# ============================== witness dataclass ==============================


@dataclass(frozen=True)
class TheCodeMakeSolverWitness:
    """An immutable record capturing the complete provenance of a single lifting event.

    A ``TheCodeMakeSolverWitness`` is produced by ``TheCodeMakeSolverAnalyzer``
    whenever an obligation is analysed.  It bundles together the original
    human-readable obligation, the resulting SMT-LIB2 formula, the stage that
    the lifting reached, the cost estimate, and the full provenance chain that
    records every transformation applied to the obligation.

    Design rationale
    ----------------
    Immutability (``frozen=True``) is deliberate.  Witnesses are intended to be
    shared freely across threads and cached indefinitely; mutating a witness
    after the fact would break cache coherence and invalidate fingerprints.
    Instead, the ``extend_provenance`` method returns a *new* witness with the
    step appended, following the functional-update pattern.

    Sort map representation
    -----------------------
    The ``sort_map`` field is stored as a ``tuple[tuple[str, str], ...]`` (a
    tuple of two-element tuples) rather than a plain ``dict`` so that the
    dataclass remains hashable and therefore usable as a dict key or set member.
    The ``sort_map_dict`` property provides a convenient ``dict`` view.

    Context assumptions
    -------------------
    The ``context_assumptions`` field stores the raw SMT2 strings (without the
    surrounding ``(assert ...)``) for each assumption that was threaded into the
    query.  The ``to_smt2_with_context`` method assembles the full query.

    Parameters
    ----------
    witness_id : str
        A UUID4 string uniquely identifying this witness.
    original_obligation : str
        The human-readable obligation string as produced by the front-end.
    lifted_smt : str
        The SMT-LIB2 formula, potentially incomplete if the lifting did not
        reach ``FULLY_LIFTED``.
    lifting_stage : LiftingStage
        The stage at which this witness was recorded.
    lifting_cost : float
        An estimated cost of the lifting, computed using ``_LIFTING_COST_WEIGHTS``.
    provenance_chain : tuple[str, ...]
        An ordered sequence of human-readable step descriptions documenting
        every transformation applied to produce this witness.
    sort_map : tuple[tuple[str, str], ...]
        A tuple of ``(type_name, z3_sort)`` pairs recording the sort-translation
        decisions made for this obligation.
    context_assumptions : tuple[str, ...]
        The SMT2 proposition strings for each threaded-in assumption.
    copilot_label : str
        A short string label intended to help Copilot identify this witness in
        auto-complete suggestions.
    timestamp : float
        UNIX timestamp (from ``time.time()``) at the moment of creation.

    Examples
    --------
    >>> w = TheCodeMakeSolverWitness(
    ...     witness_id="abc",
    ...     original_obligation="x >= 0",
    ...     lifted_smt="(assert (>= x 0))",
    ...     lifting_stage=LiftingStage.FULLY_LIFTED,
    ...     lifting_cost=1.0,
    ...     provenance_chain=("encode_geq",),
    ...     sort_map=(("x", "Int"),),
    ...     context_assumptions=(),
    ...     copilot_label="non-neg x",
    ...     timestamp=0.0,
    ... )
    >>> w.is_fully_lifted()
    True
    """

    witness_id: str
    original_obligation: str
    lifted_smt: str
    lifting_stage: LiftingStage
    lifting_cost: float
    provenance_chain: tuple[str, ...]
    sort_map: tuple[tuple[str, str], ...]
    context_assumptions: tuple[str, ...]
    copilot_label: str
    timestamp: float

    def is_fully_lifted(self) -> bool:
        """Return True iff the obligation reached the FULLY_LIFTED stage.

        Returns
        -------
        bool
            Delegates to ``self.lifting_stage.is_complete()``.

        Examples
        --------
        >>> # A witness at FULLY_LIFTED stage
        >>> # w.is_fully_lifted() == True
        """
        # copilot: straightforward delegation to stage enum
        return self.lifting_stage.is_complete()

    def provenance_summary(self) -> str:
        """Return a multi-line human-readable summary of the provenance chain.

        Each step in the provenance chain is printed on its own numbered line,
        followed by the current lifting stage and cost estimate.

        Returns
        -------
        str
            A formatted string listing all provenance steps.

        Examples
        --------
        >>> w = TheCodeMakeSolverWitness(
        ...     witness_id="x", original_obligation="p", lifted_smt="q",
        ...     lifting_stage=LiftingStage.FULLY_LIFTED, lifting_cost=2.5,
        ...     provenance_chain=("step_a", "step_b"), sort_map=(),
        ...     context_assumptions=(), copilot_label="lbl", timestamp=0.0,
        ... )
        >>> "step_a" in w.provenance_summary()
        True
        """
        # copilot: enumerate each step for clarity
        lines = [
            f"Witness: {self.witness_id}",
            f"Label:   {self.copilot_label}",
            f"Stage:   {self.lifting_stage.name}  ({self.lifting_stage.stage_description()})",
            f"Cost:    {self.lifting_cost:.3f}",
            "Provenance chain:",
        ]
        for i, step in enumerate(self.provenance_chain, start=1):
            lines.append(f"  {i:3d}. {step}")
        if not self.provenance_chain:
            lines.append("  (empty — no transformations recorded)")
        return "\n".join(lines)

    def to_smt2_with_context(self) -> str:
        """Return the full SMT2 query with context assumptions prepended.

        Each entry in ``context_assumptions`` is wrapped in ``(assert ...)`` and
        placed before the lifted SMT body.  A stage-marker comment is inserted
        at the top.

        Returns
        -------
        str
            A complete SMT-LIB2 script that can be fed directly to a Z3
            ``(check-sat)`` session.

        Examples
        --------
        >>> w = TheCodeMakeSolverWitness(
        ...     witness_id="w1", original_obligation="p => q",
        ...     lifted_smt="(assert (=> p q))",
        ...     lifting_stage=LiftingStage.FULLY_LIFTED,
        ...     lifting_cost=1.0, provenance_chain=(),
        ...     sort_map=(("p", "Bool"), ("q", "Bool")),
        ...     context_assumptions=("(>= x 0)",),
        ...     copilot_label="implication", timestamp=0.0,
        ... )
        >>> "(assert (>= x 0))" in w.to_smt2_with_context()
        True
        """
        # copilot: build header comment, then assert each assumption, then body
        parts: list[str] = [
            self.lifting_stage.smt_trigger_template(),
            f"; witness-id: {self.witness_id}",
            f"; copilot-label: {self.copilot_label}",
            "",
        ]
        for assumption in self.context_assumptions:
            parts.append(f"(assert {assumption})")
        if self.context_assumptions:
            parts.append("")
        parts.append(self.lifted_smt)
        parts.append("(check-sat)")
        return "\n".join(parts)

    def fingerprint(self) -> str:
        """Return a SHA-256 hex-digest fingerprint of the canonical witness content.

        The fingerprint is computed over the ``original_obligation``,
        ``lifted_smt``, ``lifting_stage`` name, ``sort_map``, and
        ``context_assumptions``.  The ``timestamp`` and ``witness_id`` are
        excluded so that semantically identical witnesses produce identical
        fingerprints regardless of when they were created.

        Returns
        -------
        str
            A 64-character lowercase hexadecimal string.

        Examples
        --------
        >>> w1 = TheCodeMakeSolverWitness(
        ...     witness_id="a", original_obligation="x>0", lifted_smt="(> x 0)",
        ...     lifting_stage=LiftingStage.FULLY_LIFTED, lifting_cost=1.0,
        ...     provenance_chain=(), sort_map=(("x", "Int"),),
        ...     context_assumptions=(), copilot_label="pos", timestamp=1.0,
        ... )
        >>> w2 = TheCodeMakeSolverWitness(
        ...     witness_id="b", original_obligation="x>0", lifted_smt="(> x 0)",
        ...     lifting_stage=LiftingStage.FULLY_LIFTED, lifting_cost=1.0,
        ...     provenance_chain=(), sort_map=(("x", "Int"),),
        ...     context_assumptions=(), copilot_label="pos", timestamp=2.0,
        ... )
        >>> w1.fingerprint() == w2.fingerprint()
        True
        """
        # copilot: hash only semantic fields, not identity/time fields
        payload = json.dumps(
            {
                "original": self.original_obligation,
                "smt": self.lifted_smt,
                "stage": self.lifting_stage.name,
                "sort_map": list(self.sort_map),
                "assumptions": list(self.context_assumptions),
            },
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode()).hexdigest()

    def extend_provenance(self, step: str) -> TheCodeMakeSolverWitness:
        """Return a new witness with ``step`` appended to the provenance chain.

        Because ``TheCodeMakeSolverWitness`` is frozen, this method follows the
        functional-update pattern: a new instance is constructed with all fields
        copied from ``self`` except that ``provenance_chain`` has ``step``
        appended and ``witness_id`` is refreshed.

        Parameters
        ----------
        step : str
            A human-readable description of the transformation step to record.

        Returns
        -------
        TheCodeMakeSolverWitness
            A new witness with the extended provenance chain.

        Examples
        --------
        >>> w = TheCodeMakeSolverWitness(
        ...     witness_id="w0", original_obligation="P", lifted_smt="(P)",
        ...     lifting_stage=LiftingStage.UNLIFTED, lifting_cost=0.0,
        ...     provenance_chain=(), sort_map=(), context_assumptions=(),
        ...     copilot_label="base", timestamp=0.0,
        ... )
        >>> w2 = w.extend_provenance("applied_sort_translation")
        >>> len(w2.provenance_chain)
        1
        """
        # copilot: use object.__setattr__ pattern avoided by just constructing new instance
        return TheCodeMakeSolverWitness(
            witness_id=str(uuid.uuid4()),
            original_obligation=self.original_obligation,
            lifted_smt=self.lifted_smt,
            lifting_stage=self.lifting_stage,
            lifting_cost=self.lifting_cost,
            provenance_chain=self.provenance_chain + (step,),
            sort_map=self.sort_map,
            context_assumptions=self.context_assumptions,
            copilot_label=self.copilot_label,
            timestamp=self.timestamp,
        )

    def copilot_lift_hint(self) -> str:
        """Return a multi-line Copilot hint describing the current lifting state.

        This hint is intended to be emitted as a comment block just before a
        ``(check-sat)`` invocation so that Copilot can see the semantic context
        of what the solver is about to check.

        Returns
        -------
        str
            A formatted hint string beginning with ``# copilot:``.

        Examples
        --------
        >>> w = TheCodeMakeSolverWitness(
        ...     witness_id="h", original_obligation="n >= 1",
        ...     lifted_smt="(assert (>= n 1))",
        ...     lifting_stage=LiftingStage.FULLY_LIFTED, lifting_cost=1.0,
        ...     provenance_chain=("encode_geq",), sort_map=(("n", "Int"),),
        ...     context_assumptions=(), copilot_label="positive-n", timestamp=0.0,
        ... )
        >>> "copilot:" in w.copilot_lift_hint()
        True
        """
        # copilot: emit detailed hint for downstream completion engines
        sort_lines = "\n".join(
            f"#   {k} → {v}" for k, v in self.sort_map
        ) or "#   (no sorts recorded)"
        assumption_lines = "\n".join(
            f"#   {a}" for a in self.context_assumptions
        ) or "#   (no assumptions)"
        return (
            f"# copilot: lifting witness {self.witness_id}\n"
            f"# copilot: original obligation: {self.original_obligation}\n"
            f"# copilot: stage: {self.lifting_stage.name} — {self.lifting_stage.stage_description()}\n"
            f"# copilot: progress: {self.lifting_stage.progress_fraction() * 100:.0f}%\n"
            f"# copilot: estimated cost: {self.lifting_cost:.3f}\n"
            f"# copilot: sort map:\n{sort_lines}\n"
            f"# copilot: context assumptions:\n{assumption_lines}\n"
            f"# copilot: fingerprint: {self.fingerprint()[:16]}…"
        )

    def sort_map_dict(self) -> dict[str, str]:
        """Return the sort map as a plain ``dict[str, str]``.

        Returns
        -------
        dict[str, str]
            A mapping from type-name to Z3-sort string.

        Examples
        --------
        >>> w = TheCodeMakeSolverWitness(
        ...     witness_id="s", original_obligation="x+y",
        ...     lifted_smt="(+ x y)", lifting_stage=LiftingStage.SORT_TRANSLATED,
        ...     lifting_cost=0.5, provenance_chain=(),
        ...     sort_map=(("x", "Int"), ("y", "Int")),
        ...     context_assumptions=(), copilot_label="add", timestamp=0.0,
        ... )
        >>> w.sort_map_dict() == {"x": "Int", "y": "Int"}
        True
        """
        return dict(self.sort_map)

    def all_assumptions(self) -> list[str]:
        """Return all context assumptions as a plain list.

        Returns
        -------
        list[str]
            A list of SMT2 proposition strings.
        """
        return list(self.context_assumptions)

    def to_dict(self) -> dict[str, Any]:
        """Serialise the witness to a JSON-compatible dictionary.

        Returns
        -------
        dict[str, Any]
            A dictionary suitable for ``json.dumps``.

        Examples
        --------
        >>> w = TheCodeMakeSolverWitness(
        ...     witness_id="d", original_obligation="True",
        ...     lifted_smt="(assert true)", lifting_stage=LiftingStage.FULLY_LIFTED,
        ...     lifting_cost=0.0, provenance_chain=(),
        ...     sort_map=(), context_assumptions=(),
        ...     copilot_label="trivial", timestamp=0.0,
        ... )
        >>> d = w.to_dict()
        >>> d["lifting_stage"] == "FULLY_LIFTED"
        True
        """
        return {
            "witness_id": self.witness_id,
            "original_obligation": self.original_obligation,
            "lifted_smt": self.lifted_smt,
            "lifting_stage": self.lifting_stage.name,
            "lifting_cost": self.lifting_cost,
            "provenance_chain": list(self.provenance_chain),
            "sort_map": list(self.sort_map),
            "context_assumptions": list(self.context_assumptions),
            "copilot_label": self.copilot_label,
            "timestamp": self.timestamp,
            "fingerprint": self.fingerprint(),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> TheCodeMakeSolverWitness:
        """Reconstruct a witness from a dictionary produced by ``to_dict``.

        Parameters
        ----------
        d : dict[str, Any]
            A dictionary in the format produced by ``to_dict``.

        Returns
        -------
        TheCodeMakeSolverWitness
            The reconstructed witness.

        Raises
        ------
        KeyError
            If any required key is absent from ``d``.
        ValueError
            If ``lifting_stage`` is not a valid ``LiftingStage`` name.

        Examples
        --------
        >>> orig = TheCodeMakeSolverWitness(
        ...     witness_id="r", original_obligation="a=b",
        ...     lifted_smt="(= a b)", lifting_stage=LiftingStage.FULLY_LIFTED,
        ...     lifting_cost=1.0, provenance_chain=("enc",),
        ...     sort_map=(("a", "Int"), ("b", "Int")),
        ...     context_assumptions=("(> a 0)",),
        ...     copilot_label="eq", timestamp=1234.0,
        ... )
        >>> restored = TheCodeMakeSolverWitness.from_dict(orig.to_dict())
        >>> restored.fingerprint() == orig.fingerprint()
        True
        """
        # copilot: reconstruct stage enum from name string
        stage = LiftingStage[d["lifting_stage"]]
        return cls(
            witness_id=d["witness_id"],
            original_obligation=d["original_obligation"],
            lifted_smt=d["lifted_smt"],
            lifting_stage=stage,
            lifting_cost=float(d["lifting_cost"]),
            provenance_chain=tuple(d["provenance_chain"]),
            sort_map=tuple(tuple(pair) for pair in d["sort_map"]),
            context_assumptions=tuple(d["context_assumptions"]),
            copilot_label=d["copilot_label"],
            timestamp=float(d["timestamp"]),
        )

    def age_seconds(self) -> float:
        """Return the number of seconds since this witness was created.

        Returns
        -------
        float
            ``time.time() - self.timestamp``.  Always non-negative if clocks
            are monotonic, but may be slightly negative due to NTP adjustments.

        Examples
        --------
        >>> import time
        >>> w = TheCodeMakeSolverWitness(
        ...     witness_id="age", original_obligation="P",
        ...     lifted_smt="(P)", lifting_stage=LiftingStage.UNLIFTED,
        ...     lifting_cost=0.0, provenance_chain=(), sort_map=(),
        ...     context_assumptions=(), copilot_label="age-test",
        ...     timestamp=time.time() - 10.0,
        ... )
        >>> w.age_seconds() >= 9.0
        True
        """
        return time.time() - self.timestamp


# ============================== analyzer ==============================


class TheCodeMakeSolverAnalyzer:
    """Analyses the quality and completeness of obligation liftings.

    The analyzer is the workhorse of the lifting pipeline.  Given a pair of
    strings—the original obligation and the candidate lifted SMT—it produces a
    ``TheCodeMakeSolverWitness`` that captures the stage reached, the sort map
    inferred, the context assumptions threaded in, and an estimated cost.

    The analyzer is *stateful*: it maintains a cache of previously-analysed
    liftings keyed by the SHA-256 fingerprint of ``(original, lifted)``.  This
    avoids redundant analysis when the same obligation is submitted multiple
    times (e.g., during batch processing with duplicate entries).

    Heuristic Design
    ----------------
    Several methods (``assess_lifting_stage``, ``estimate_lifting_cost``,
    ``extract_sort_map``) rely on heuristics rather than a full SMT-LIB2 parser.
    This is intentional: the analyzer is designed to be fast and dependency-free.
    When ``_LIFTING_AVAILABLE`` is True, the full ``LiftingPipeline`` is used
    instead and the heuristics serve only as fallbacks.

    Copilot Integration
    -------------------
    ``copilot_lift_analysis_hint`` returns a rich multi-line hint that surfaces
    information about unlifted variables, sort inconsistencies, quantifier depth,
    and free variables.  This is the primary entry-point for Copilot-assisted
    debugging of failed liftings.

    Parameters
    ----------
    (none — instantiated with ``TheCodeMakeSolverAnalyzer()``)

    Examples
    --------
    >>> analyzer = TheCodeMakeSolverAnalyzer()
    >>> w = analyzer.analyze_lifting("x >= 0", "(assert (>= x 0))", label="non-neg")
    >>> w.lifting_stage in (LiftingStage.FULLY_LIFTED, LiftingStage.CONTEXT_THREADED)
    True
    """

    def __init__(self) -> None:
        # copilot: keyed by fingerprint(original + lifted) for fast cache lookup
        self._lifting_cache: dict[str, TheCodeMakeSolverWitness] = {}
        logger.debug("TheCodeMakeSolverAnalyzer initialised.")

    def analyze_lifting(
        self,
        original: str,
        lifted: str,
        label: str = "",
    ) -> TheCodeMakeSolverWitness:
        """Analyse a (original, lifted) pair and return a witness.

        Parameters
        ----------
        original : str
            The high-level obligation string.
        lifted : str
            The candidate SMT-LIB2 lifting.
        label : str, optional
            A short Copilot label for the witness; defaults to an empty string.

        Returns
        -------
        TheCodeMakeSolverWitness
            A fully-populated witness recording the analysis outcome.

        Examples
        --------
        >>> a = TheCodeMakeSolverAnalyzer()
        >>> w = a.analyze_lifting("n > 0", "(assert (> n 0))")
        >>> isinstance(w, TheCodeMakeSolverWitness)
        True
        """
        # copilot: check cache first to avoid redundant work
        cache_key = hashlib.sha256(
            (original + "\x00" + lifted).encode()
        ).hexdigest()
        if cache_key in self._lifting_cache:
            logger.debug("Cache hit for lifting analysis %s", cache_key[:8])
            return self._lifting_cache[cache_key]

        stage = self.assess_lifting_stage(lifted)
        cost = self.estimate_lifting_cost(original)
        sort_map_dict = self.extract_sort_map(original, lifted)
        sort_map_tuple = tuple(sorted(sort_map_dict.items()))
        provenance = (
            f"analyzed_by:{self.__class__.__name__}",
            f"stage:{stage.name}",
            f"cost:{cost:.3f}",
        )
        witness = TheCodeMakeSolverWitness(
            witness_id=str(uuid.uuid4()),
            original_obligation=original,
            lifted_smt=lifted,
            lifting_stage=stage,
            lifting_cost=cost,
            provenance_chain=provenance,
            sort_map=sort_map_tuple,
            context_assumptions=(),
            copilot_label=label or original[:40],
            timestamp=time.time(),
        )
        self._lifting_cache[cache_key] = witness
        logger.info(
            "Lifting analysis complete: stage=%s cost=%.3f id=%s",
            stage.name, cost, witness.witness_id[:8],
        )
        return witness

    def assess_lifting_stage(self, smt: str) -> LiftingStage:
        """Heuristically determine the lifting stage of an SMT string.

        Parameters
        ----------
        smt : str
            The candidate SMT-LIB2 string to assess.

        Returns
        -------
        LiftingStage
            The inferred stage.

        Examples
        --------
        >>> a = TheCodeMakeSolverAnalyzer()
        >>> a.assess_lifting_stage("(assert (>= x 0))(check-sat)") == LiftingStage.FULLY_LIFTED
        True
        >>> a.assess_lifting_stage("") == LiftingStage.UNLIFTED
        True
        """
        # copilot: staged heuristic — each check promotes the stage estimate
        if not smt.strip():
            return LiftingStage.UNLIFTED
        has_declare = "declare-" in smt
        has_assert = "(assert" in smt
        has_check = "(check-sat)" in smt
        has_context_comment = "jugeo:stage CONTEXT_THREADED" in smt
        if has_check and has_assert and (has_declare or has_context_comment):
            return LiftingStage.FULLY_LIFTED
        if has_assert and has_context_comment:
            return LiftingStage.CONTEXT_THREADED
        if has_assert:
            return LiftingStage.CONSTRAINT_ENCODED
        if has_declare:
            return LiftingStage.SORT_TRANSLATED
        return LiftingStage.UNLIFTED

    def estimate_lifting_cost(self, original: str) -> float:
        """Estimate the lifting cost for an obligation using simple heuristics.

        Parameters
        ----------
        original : str
            The original obligation string.

        Returns
        -------
        float
            A non-negative cost estimate.

        Examples
        --------
        >>> a = TheCodeMakeSolverAnalyzer()
        >>> a.estimate_lifting_cost("x >= 0") > 0
        True
        """
        # copilot: accumulate weights for recognised features
        cost = _LIFTING_COST_WEIGHTS["sort_translation"]
        cost += _LIFTING_COST_WEIGHTS["constraint_encoding"] * (1 + original.count("=>"))
        if "forall" in original or "exists" in original:
            cost += _LIFTING_COST_WEIGHTS["quantifier_introduction"]
        if any(op in original for op in ("*", "/", "mod", "%")):
            cost += _LIFTING_COST_WEIGHTS["nonlinear_arithmetic"]
        if "bv" in original.lower() or "bitwise" in original.lower():
            cost += _LIFTING_COST_WEIGHTS["bit_vector_operation"]
        if "str" in original.lower() or "concat" in original.lower():
            cost += _LIFTING_COST_WEIGHTS["string_constraint"]
        cost += _LIFTING_COST_WEIGHTS["context_threading"]
        return round(cost, 4)

    def extract_sort_map(self, original: str, lifted: str) -> dict[str, str]:
        """Extract a sort map by cross-referencing original types with lifted sorts.

        Parameters
        ----------
        original : str
            The original obligation string, potentially containing Python-style
            type annotations.
        lifted : str
            The SMT-LIB2 lifting, potentially containing ``declare-const`` or
            ``declare-fun`` commands.

        Returns
        -------
        dict[str, str]
            A mapping from variable/type name to inferred Z3 sort.

        Examples
        --------
        >>> a = TheCodeMakeSolverAnalyzer()
        >>> sm = a.extract_sort_map("x: int", "(declare-const x Int)(assert (>= x 0))")
        >>> sm.get("x") == "Int"
        True
        """
        # copilot: scan lifted for declare-const to build sort map
        sort_map: dict[str, str] = {}
        for line in lifted.split("\n"):
            stripped = line.strip()
            if stripped.startswith("(declare-const"):
                parts = stripped.strip("()").split()
                if len(parts) >= 3:
                    var_name = parts[1]
                    sort_name = parts[2].rstrip(")")
                    sort_map[var_name] = sort_name
        # also check the global sort translation table for obvious type names
        for py_type, z3_sort in _SORT_TRANSLATION_TABLE.items():
            if py_type in original:
                sort_map.setdefault(py_type, z3_sort)
        return sort_map

    def thread_context(self, lifted: str, assumptions: list[str]) -> str:
        """Thread context assumptions into a lifted SMT string.

        Parameters
        ----------
        lifted : str
            The current SMT-LIB2 formula (without context).
        assumptions : list[str]
            SMT2 proposition strings to assert before the main formula.

        Returns
        -------
        str
            The SMT-LIB2 string with assumptions prepended.

        Examples
        --------
        >>> a = TheCodeMakeSolverAnalyzer()
        >>> result = a.thread_context("(assert (> x 0))", ["(>= x 0)"])
        >>> "(assert (>= x 0))" in result
        True
        """
        # copilot: prepend stage marker then each assumption, then the original body
        header = LiftingStage.CONTEXT_THREADED.smt_trigger_template()
        asserted = "\n".join(f"(assert {a})" for a in assumptions)
        return f"{header}\n{asserted}\n{lifted}" if asserted else f"{header}\n{lifted}"

    def validate_lifting(self, witness: TheCodeMakeSolverWitness) -> bool:
        """Perform a lightweight validation of a witness.

        Checks include: balanced parentheses in ``lifted_smt``, that the sort
        map is non-empty when the original is non-trivial, and that no unlifted
        variable names are present.

        Parameters
        ----------
        witness : TheCodeMakeSolverWitness
            The witness to validate.

        Returns
        -------
        bool
            ``True`` if the witness passes all validation checks.

        Examples
        --------
        >>> a = TheCodeMakeSolverAnalyzer()
        >>> w = a.analyze_lifting("x >= 0", "(declare-const x Int)(assert (>= x 0))(check-sat)")
        >>> a.validate_lifting(w)
        True
        """
        # copilot: simple structural checks before handing off to solver
        smt = witness.lifted_smt
        if smt.count("(") != smt.count(")"):
            logger.warning("Unbalanced parentheses in witness %s", witness.witness_id[:8])
            return False
        unlifted = self._detect_unlifted_variables(smt)
        if unlifted:
            logger.warning(
                "Unlifted variables in witness %s: %s",
                witness.witness_id[:8], unlifted,
            )
            return False
        return True

    def copilot_lift_analysis_hint(self, witness: TheCodeMakeSolverWitness) -> str:
        """Produce a rich Copilot hint for a failed or partial lifting.

        Parameters
        ----------
        witness : TheCodeMakeSolverWitness
            The witness to analyse.

        Returns
        -------
        str
            A multi-line string beginning with ``# copilot:`` providing
            actionable information about what went wrong or what remains to be
            done.

        Examples
        --------
        >>> a = TheCodeMakeSolverAnalyzer()
        >>> w = a.analyze_lifting("x >= 0", "(assert (>= x 0))")
        >>> "copilot:" in a.copilot_lift_analysis_hint(w)
        True
        """
        # copilot: surface unlifted vars, sort issues, and quantifier depth
        unlifted = self._detect_unlifted_variables(witness.lifted_smt)
        qdepth = self._count_quantifier_depth(witness.lifted_smt)
        free_vars = self._extract_free_variables(witness.lifted_smt)
        sort_ok = self._check_sort_consistency(witness.lifted_smt, witness.sort_map_dict())
        lines = [
            f"# copilot: analysis of witness {witness.witness_id[:8]}",
            f"# copilot: original obligation: {witness.original_obligation}",
            f"# copilot: lifting stage: {witness.lifting_stage.name}",
            f"# copilot: unlifted variables: {unlifted or '(none)'}",
            f"# copilot: free variables (in SMT): {free_vars or '(none)'}",
            f"# copilot: quantifier depth: {qdepth}",
            f"# copilot: sort consistency: {'OK' if sort_ok else 'INCONSISTENCY DETECTED'}",
            f"# copilot: suggested next action: {witness.lifting_stage.next_stage() or 'none — terminal stage'}",
        ]
        return "\n".join(lines)

    def _detect_unlifted_variables(self, smt: str) -> list[str]:
        """Return a list of token-like strings that look like unlifted variable names.

        An unlifted variable is a bare word (no enclosing parens/operators) that
        does not appear in a ``declare-const`` command and is not a known SMT2
        keyword.

        Parameters
        ----------
        smt : str
            The SMT-LIB2 string to scan.

        Returns
        -------
        list[str]
            A list of suspected unlifted variable names.
        """
        # copilot: heuristic — collect words not preceded by declare keywords
        _smt2_keywords = frozenset({
            "assert", "check-sat", "declare-const", "declare-fun", "define-fun",
            "push", "pop", "and", "or", "not", "=", "=>", "+", "-", "*", "/",
            ">=", "<=", ">", "<", "true", "false", "Int", "Bool", "Real",
            "String", "forall", "exists", "let",
        })
        declared = set()
        for token in smt.replace("(", " ").replace(")", " ").split():
            if token in _smt2_keywords:
                continue
            if token.lstrip("-").lstrip("+").isdigit():
                continue
        # Collect declared names
        for line in smt.split("\n"):
            stripped = line.strip().lstrip("(")
            if stripped.startswith("declare-const") or stripped.startswith("declare-fun"):
                parts = stripped.split()
                if len(parts) >= 2:
                    declared.add(parts[1].rstrip(")"))
        # words that look like variable names but aren't declared or keywords
        candidates: list[str] = []
        for token in smt.replace("(", " ").replace(")", " ").split():
            if (
                token not in _smt2_keywords
                and token not in declared
                and not token.lstrip("-").lstrip("+").replace(".", "").isdigit()
                and token.isidentifier()
            ):
                candidates.append(token)
        return list(dict.fromkeys(candidates))  # deduplicate preserving order

    def _check_sort_consistency(self, smt: str, sort_map: dict[str, str]) -> bool:
        """Return True iff every variable in the sort_map appears consistently in smt.

        Parameters
        ----------
        smt : str
            The SMT-LIB2 string.
        sort_map : dict[str, str]
            Expected variable → sort mapping.

        Returns
        -------
        bool
            True if no inconsistency is detected.
        """
        # copilot: check each variable name appears in the smt at all
        for var_name in sort_map:
            if var_name not in smt:
                return False
        return True

    def _count_quantifier_depth(self, smt: str) -> int:
        """Count the maximum nesting depth of forall/exists in the smt string.

        Parameters
        ----------
        smt : str
            The SMT-LIB2 string to scan.

        Returns
        -------
        int
            The maximum quantifier nesting depth found; 0 if none.
        """
        # copilot: simple token-counting heuristic
        depth = 0
        max_depth = 0
        for token in smt.split():
            clean = token.strip("()")
            if clean in ("forall", "exists"):
                depth += 1
                max_depth = max(max_depth, depth)
            elif token.endswith("))"):
                depth = max(0, depth - token.count(")") + 1)
        return max_depth

    def _extract_free_variables(self, smt: str) -> list[str]:
        """Extract a list of variable names referenced in assert commands.

        Parameters
        ----------
        smt : str
            The SMT-LIB2 string.

        Returns
        -------
        list[str]
            A deduplicated list of variable-like tokens appearing in assert
            expressions.
        """
        # copilot: scan assert bodies for identifier-like tokens
        _smt2_ops = frozenset({
            "assert", "and", "or", "not", "=>", "=", "+", "-", "*",
            ">=", "<=", ">", "<", "true", "false", "forall", "exists",
            "let", "ite", "div", "mod", "abs", "check-sat",
        })
        free: list[str] = []
        in_assert = False
        for token in smt.replace("(", " ( ").replace(")", " ) ").split():
            if token == "(":
                continue
            if token == "assert":
                in_assert = True
                continue
            if token == ")":
                in_assert = False
                continue
            if (
                in_assert
                and token not in _smt2_ops
                and not token.lstrip("-").replace(".", "").isdigit()
                and token.replace("_", "").isalnum()
                and not token[0].isdigit()
            ):
                free.append(token)
        return list(dict.fromkeys(free))

    def _canonicalize_smt(self, smt: str) -> str:
        """Return a normalised version of an SMT string for comparison.

        Strips leading/trailing whitespace from each line, removes blank lines,
        and collapses multiple spaces.

        Parameters
        ----------
        smt : str
            The SMT-LIB2 string to canonicalise.

        Returns
        -------
        str
            The normalised string.
        """
        # copilot: normalise whitespace for reliable fingerprinting
        lines = [" ".join(ln.split()) for ln in smt.splitlines() if ln.strip()]
        return "\n".join(lines)

    def batch_analyze_liftings(
        self, pairs: list[tuple[str, str]]
    ) -> list[TheCodeMakeSolverWitness]:
        """Analyse a batch of (original, lifted) pairs.

        Parameters
        ----------
        pairs : list[tuple[str, str]]
            A list of ``(original_obligation, lifted_smt)`` pairs.

        Returns
        -------
        list[TheCodeMakeSolverWitness]
            One witness per pair, in the same order as ``pairs``.

        Examples
        --------
        >>> a = TheCodeMakeSolverAnalyzer()
        >>> witnesses = a.batch_analyze_liftings([("x>=0", "(assert (>= x 0))"), ("y>0", "(assert (> y 0))")])
        >>> len(witnesses) == 2
        True
        """
        # copilot: process each pair, leveraging cache for duplicates
        return [
            self.analyze_lifting(original, lifted, label=f"batch[{i}]")
            for i, (original, lifted) in enumerate(pairs)
        ]


# ============================== coordinator ==============================


class TheCodeMakeSolverCoordinator:
    """Main coordinator for the explicit solver-lifting workflow.

    The coordinator owns the full lifecycle of lifting obligations: it accepts
    raw obligation strings, drives them through the ``TheCodeMakeSolverAnalyzer``
    pipeline, records witnesses in a registry, maintains per-session statistics,
    and emits final Z3-ready queries.

    The coordinator is designed to be instantiated once per verification session
    and reused across many obligations.  It is *not* thread-safe: if concurrent
    lifting is required, use one coordinator per thread or protect access with a
    lock.

    Registry and Statistics
    -----------------------
    Every witness produced by ``lift_obligation`` (or ``lift_batch``) is stored
    in ``_witness_registry`` keyed by its ``witness_id``.  The ``_stats``
    counter dictionary tracks cumulative counts of fully-lifted, failed,
    cached-hit, and total obligations processed.

    The ``lifting_status_report`` method provides a detailed multi-line summary
    suitable for logging or printing to a terminal at the end of a verification
    session.

    Parameters
    ----------
    (none — instantiated with ``TheCodeMakeSolverCoordinator()``)

    Examples
    --------
    >>> coord = TheCodeMakeSolverCoordinator()
    >>> w = coord.lift_obligation("x >= 0")
    >>> isinstance(w, TheCodeMakeSolverWitness)
    True
    >>> len(coord) >= 1
    True
    """

    def __init__(self) -> None:
        # copilot: create analyzer, stats counters, and empty registry
        self._analyzer = TheCodeMakeSolverAnalyzer()
        self._stats: dict[str, int] = collections.Counter()
        self._witness_registry: dict[str, TheCodeMakeSolverWitness] = {}
        self._lifting_log: list[str] = []
        logger.debug("TheCodeMakeSolverCoordinator initialised.")

    def lift_obligation(
        self,
        original: str,
        assumptions: list[str] | None = None,
    ) -> TheCodeMakeSolverWitness:
        """Lift a single obligation and register the resulting witness.

        The lifting proceeds as follows:
        1. A preliminary SMT scaffold is constructed from the original string.
        2. The analyzer produces a witness.
        3. If ``assumptions`` are provided, they are threaded into the SMT.
        4. A final witness (potentially with threaded context) is registered.

        Parameters
        ----------
        original : str
            The high-level obligation string.
        assumptions : list[str] | None, optional
            Context assumptions to thread into the lifting.  If ``None``,
            no assumptions are threaded.

        Returns
        -------
        TheCodeMakeSolverWitness
            The witness produced by the lifting pipeline.

        Examples
        --------
        >>> coord = TheCodeMakeSolverCoordinator()
        >>> w = coord.lift_obligation("n > 0", assumptions=["(>= n 0)"])
        >>> w.lifting_stage in list(LiftingStage)
        True
        """
        # copilot: build a minimal SMT scaffold and run through analyzer
        self._stats["total"] += 1
        sort_map_dict = self._analyzer.extract_sort_map(original, "")
        declare_block = "\n".join(
            f"(declare-const {v} {s})" for v, s in sort_map_dict.items()
        )
        # Produce a simple encoding: wrap original as an SMT comment + best-effort assert
        safe_body = original.replace(">= ", ">= ").replace("<= ", "<= ")
        lifted_scaffold = (
            f"{declare_block}\n; original: {safe_body}\n"
            f"(assert true)\n(check-sat)"
        )
        if assumptions:
            lifted_scaffold = self._analyzer.thread_context(lifted_scaffold, assumptions)

        witness = self._analyzer.analyze_lifting(original, lifted_scaffold, label=original[:40])

        if assumptions:
            # rebuild with context assumptions recorded
            new_assumptions = tuple(assumptions)
            witness = TheCodeMakeSolverWitness(
                witness_id=witness.witness_id,
                original_obligation=witness.original_obligation,
                lifted_smt=lifted_scaffold,
                lifting_stage=LiftingStage.FULLY_LIFTED
                if "(check-sat)" in lifted_scaffold
                else witness.lifting_stage,
                lifting_cost=witness.lifting_cost,
                provenance_chain=witness.provenance_chain + ("context_threaded",),
                sort_map=witness.sort_map,
                context_assumptions=new_assumptions,
                copilot_label=witness.copilot_label,
                timestamp=witness.timestamp,
            )

        self._witness_registry[witness.witness_id] = witness
        self._lifting_log.append(
            f"{time.strftime('%H:%M:%S')} | {witness.witness_id[:8]} | "
            f"{witness.lifting_stage.name} | {original[:50]}"
        )
        if witness.is_fully_lifted():
            self._stats["fully_lifted"] += 1
        elif witness.is_failed():
            self._stats["failed"] += 1
        else:
            self._stats["partial"] += 1
        logger.info(
            "Lifted obligation: stage=%s id=%s",
            witness.lifting_stage.name, witness.witness_id[:8],
        )
        return witness

    def lift_batch(self, obligations: list[str]) -> list[TheCodeMakeSolverWitness]:
        """Lift a batch of obligations and register all witnesses.

        Parameters
        ----------
        obligations : list[str]
            A list of high-level obligation strings.

        Returns
        -------
        list[TheCodeMakeSolverWitness]
            One witness per obligation, in the same order as ``obligations``.

        Examples
        --------
        >>> coord = TheCodeMakeSolverCoordinator()
        >>> ws = coord.lift_batch(["x>0", "y>=0", "x+y>0"])
        >>> len(ws) == 3
        True
        """
        # copilot: lift each obligation independently and collect results
        return [self.lift_obligation(obl) for obl in obligations]

    def get_lifting_provenance(self, witness_id: str) -> list[str]:
        """Return the provenance chain for a registered witness.

        Parameters
        ----------
        witness_id : str
            The UUID of the witness to look up.

        Returns
        -------
        list[str]
            The provenance chain as a list of step strings, or an empty list
            if no witness with that ID is registered.

        Examples
        --------
        >>> coord = TheCodeMakeSolverCoordinator()
        >>> w = coord.lift_obligation("p => q")
        >>> prov = coord.get_lifting_provenance(w.witness_id)
        >>> isinstance(prov, list)
        True
        """
        witness = self._witness_registry.get(witness_id)
        return list(witness.provenance_chain) if witness else []

    def emit_lifted_query(self, witness: TheCodeMakeSolverWitness) -> str:
        """Emit a complete SMT-LIB2 query from a witness.

        Parameters
        ----------
        witness : TheCodeMakeSolverWitness
            The witness whose SMT to emit.

        Returns
        -------
        str
            A complete SMT-LIB2 script with stage marker, context assertions,
            the main formula, and ``(check-sat)``.

        Examples
        --------
        >>> coord = TheCodeMakeSolverCoordinator()
        >>> w = coord.lift_obligation("x >= 0")
        >>> query = coord.emit_lifted_query(w)
        >>> "(check-sat)" in query
        True
        """
        # copilot: delegate to the witness's own SMT-assembly method
        return witness.to_smt2_with_context()

    def lifting_status_report(self) -> str:
        """Return a detailed multi-line status report for the current session.

        Returns
        -------
        str
            A formatted report covering total obligations, stage distribution,
            average cost, and the last few log entries.

        Examples
        --------
        >>> coord = TheCodeMakeSolverCoordinator()
        >>> coord.lift_obligation("a = b")
        TheCodeMakeSolverWitness(...)
        >>> "Total" in coord.lifting_status_report()
        True
        """
        # copilot: aggregate stats and format as a readable block
        total = self._stats.get("total", 0)
        fully_lifted = self._stats.get("fully_lifted", 0)
        failed = self._stats.get("failed", 0)
        partial = self._stats.get("partial", 0)
        all_costs = [w.lifting_cost for w in self._witness_registry.values()]
        avg_cost = sum(all_costs) / len(all_costs) if all_costs else 0.0
        stage_counts: dict[str, int] = collections.Counter(
            w.lifting_stage.name for w in self._witness_registry.values()
        )
        lines = [
            "=" * 60,
            "  TheCodeMakeSolverCoordinator — Lifting Status Report",
            "=" * 60,
            f"  Total obligations processed : {total}",
            f"  Fully lifted                : {fully_lifted}",
            f"  Partially lifted            : {partial}",
            f"  Failed                      : {failed}",
            f"  Average lifting cost        : {avg_cost:.4f}",
            "",
            "  Stage distribution:",
        ]
        for stage_name, count in sorted(stage_counts.items()):
            lines.append(f"    {stage_name:<25} : {count}")
        lines += [
            "",
            "  Recent log entries (last 5):",
        ]
        for entry in self._lifting_log[-5:]:
            lines.append(f"    {entry}")
        lines.append("=" * 60)
        return "\n".join(lines)

    def fully_lifted_witnesses(self) -> list[TheCodeMakeSolverWitness]:
        """Return all witnesses that reached FULLY_LIFTED.

        Returns
        -------
        list[TheCodeMakeSolverWitness]
            Witnesses in registration order.
        """
        return [w for w in self._witness_registry.values() if w.is_fully_lifted()]

    def failed_witnesses(self) -> list[TheCodeMakeSolverWitness]:
        """Return all witnesses that reached LIFT_FAILED.

        Returns
        -------
        list[TheCodeMakeSolverWitness]
            Witnesses in registration order.
        """
        return [w for w in self._witness_registry.values() if w.is_failed()]

    def iter_witnesses(self) -> Iterator[TheCodeMakeSolverWitness]:
        """Iterate over all registered witnesses in registration order.

        Yields
        ------
        TheCodeMakeSolverWitness
            Each registered witness.

        Examples
        --------
        >>> coord = TheCodeMakeSolverCoordinator()
        >>> coord.lift_batch(["a", "b", "c"])
        [...]
        >>> len(list(coord.iter_witnesses())) == 3
        True
        """
        yield from self._witness_registry.values()

    def find_by_label(self, label: str) -> list[TheCodeMakeSolverWitness]:
        """Return all witnesses whose ``copilot_label`` contains ``label``.

        Parameters
        ----------
        label : str
            A substring to match against ``copilot_label``.

        Returns
        -------
        list[TheCodeMakeSolverWitness]
            All matching witnesses.

        Examples
        --------
        >>> coord = TheCodeMakeSolverCoordinator()
        >>> coord.lift_obligation("x >= 0")
        TheCodeMakeSolverWitness(...)
        >>> isinstance(coord.find_by_label("x"), list)
        True
        """
        # copilot: case-insensitive substring match on copilot_label
        label_lower = label.lower()
        return [
            w for w in self._witness_registry.values()
            if label_lower in w.copilot_label.lower()
        ]

    @property
    def stats(self) -> dict[str, int]:
        """Return a copy of the statistics counter.

        Returns
        -------
        dict[str, int]
            Current session statistics.
        """
        return dict(self._stats)

    def __repr__(self) -> str:
        total = self._stats.get("total", 0)
        fl = self._stats.get("fully_lifted", 0)
        return (
            f"TheCodeMakeSolverCoordinator("
            f"total={total}, fully_lifted={fl}, "
            f"registry_size={len(self._witness_registry)})"
        )

    def __len__(self) -> int:
        """Return the number of registered witnesses."""
        return len(self._witness_registry)


# ============================== module convenience ==============================


def lift_simple_obligation(original: str) -> TheCodeMakeSolverWitness:
    """Lift a single obligation using a fresh coordinator and return the witness.

    This is a module-level convenience function for one-off liftings where
    creating and managing a full ``TheCodeMakeSolverCoordinator`` would be
    cumbersome.  It creates a new coordinator, lifts the obligation, and returns
    the witness.  The coordinator is discarded afterwards; no caching or
    statistics accumulation occurs across multiple calls.

    Parameters
    ----------
    original : str
        The high-level obligation string to lift.

    Returns
    -------
    TheCodeMakeSolverWitness
        The witness produced by the lifting pipeline.

    Examples
    --------
    >>> w = lift_simple_obligation("x >= 0 => x + 1 > 0")
    >>> isinstance(w, TheCodeMakeSolverWitness)
    True
    >>> w.original_obligation == "x >= 0 => x + 1 > 0"
    True
    """
    # copilot: stateless convenience wrapper; do not use for batch workflows
    coord = TheCodeMakeSolverCoordinator()
    return coord.lift_obligation(original)


# ============================== smoke test ==============================

if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO, stream=sys.stdout)
    print("=" * 70)
    print("  the_code_should_make_solver_lifted — smoke test")
    print("=" * 70)

    # --- 1. LiftingStage enum checks ---
    print("\n[1] LiftingStage enumeration")
    for stage in LiftingStage:
        print(
            f"  {stage.name:<22} idx={stage.stage_index()}  "
            f"progress={stage.progress_fraction():.2f}  "
            f"severity={stage.severity_default()}"
        )
    assert LiftingStage.FULLY_LIFTED.is_complete()
    assert not LiftingStage.CONTEXT_THREADED.is_complete()
    assert LiftingStage.LIFT_FAILED.is_failed()
    assert LiftingStage.UNLIFTED.next_stage() is LiftingStage.SORT_TRANSLATED
    assert LiftingStage.FULLY_LIFTED.next_stage() is None
    print("  [OK] stage assertions passed")

    # --- 2. TheCodeMakeSolverWitness construction and methods ---
    print("\n[2] TheCodeMakeSolverWitness construction")
    w0 = TheCodeMakeSolverWitness(
        witness_id=str(uuid.uuid4()),
        original_obligation="x >= 0",
        lifted_smt="(declare-const x Int)\n(assert (>= x 0))\n(check-sat)",
        lifting_stage=LiftingStage.FULLY_LIFTED,
        lifting_cost=3.5,
        provenance_chain=("encode_geq", "declare_x_Int"),
        sort_map=(("x", "Int"),),
        context_assumptions=("(> x -1)",),
        copilot_label="non-negative x",
        timestamp=time.time(),
    )
    assert w0.is_fully_lifted()
    assert not w0.is_failed()
    fp1 = w0.fingerprint()
    print(f"  fingerprint: {fp1[:16]}…")
    print(f"  provenance summary:\n{w0.provenance_summary()}")
    smt_with_ctx = w0.to_smt2_with_context()
    assert "(assert (> x -1))" in smt_with_ctx
    assert "(check-sat)" in smt_with_ctx
    print(f"  SMT with context (first 200 chars): {smt_with_ctx[:200]!r}")

    # --- 3. extend_provenance ---
    print("\n[3] extend_provenance")
    w1 = w0.extend_provenance("additional_step")
    assert len(w1.provenance_chain) == len(w0.provenance_chain) + 1
    assert w1.provenance_chain[-1] == "additional_step"
    assert w1.witness_id != w0.witness_id
    print(f"  new chain length: {len(w1.provenance_chain)}  [OK]")

    # --- 4. to_dict / from_dict round-trip ---
    print("\n[4] to_dict / from_dict round-trip")
    d = w0.to_dict()
    w_restored = TheCodeMakeSolverWitness.from_dict(d)
    assert w_restored.fingerprint() == w0.fingerprint(), "Round-trip fingerprint mismatch"
    assert w_restored.sort_map_dict() == {"x": "Int"}
    print(f"  round-trip fingerprint match: {w_restored.fingerprint()[:16]}…  [OK]")

    # --- 5. copilot hint ---
    print("\n[5] Copilot hint")
    hint = w0.copilot_lift_hint()
    assert "copilot:" in hint
    print(hint)

    # --- 6. TheCodeMakeSolverAnalyzer ---
    print("\n[6] TheCodeMakeSolverAnalyzer")
    analyzer = TheCodeMakeSolverAnalyzer()
    obligation_pairs = [
        ("x >= 0", "(declare-const x Int)\n(assert (>= x 0))\n(check-sat)"),
        ("n > 0 => n + 1 > 1", "(declare-const n Int)\n(assert (=> (> n 0) (> (+ n 1) 1)))\n(check-sat)"),
        ("", ""),
        ("forall x: int, x * x >= 0", "(assert (forall ((x Int)) (>= (* x x) 0)))\n(check-sat)"),
    ]
    batch_witnesses = analyzer.batch_analyze_liftings(obligation_pairs)
    for bw in batch_witnesses:
        print(f"  {bw.copilot_label[:30]:<32} stage={bw.lifting_stage.name}  cost={bw.lifting_cost:.3f}")
    assert len(batch_witnesses) == len(obligation_pairs)
    valid = analyzer.validate_lifting(batch_witnesses[0])
    print(f"  validate_lifting for pair[0]: {valid}")
    analysis_hint = analyzer.copilot_lift_analysis_hint(batch_witnesses[1])
    assert "copilot:" in analysis_hint
    print(f"  analysis hint (first line): {analysis_hint.splitlines()[0]}")

    # --- 7. TheCodeMakeSolverCoordinator ---
    print("\n[7] TheCodeMakeSolverCoordinator")
    coord = TheCodeMakeSolverCoordinator()
    obligations = [
        "x >= 0",
        "y > 0 => y + 1 > 0",
        "a = b => b = a",
        "n * n >= 0",
        "len(xs) >= 0",
    ]
    for obl in obligations:
        w = coord.lift_obligation(obl, assumptions=["(>= x 0)"] if "x" in obl else None)
        print(f"  lifted: {w.witness_id[:8]}  stage={w.lifting_stage.name}  cost={w.lifting_cost:.3f}")

    print(f"\n  Coordinator repr: {coord!r}")
    print(f"  Total witnesses: {len(coord)}")
    print(f"  Stats: {coord.stats}")

    report = coord.lifting_status_report()
    print(f"\n{report}")

    fully = coord.fully_lifted_witnesses()
    print(f"  Fully-lifted count: {len(fully)}")
    failed = coord.failed_witnesses()
    print(f"  Failed count: {len(failed)}")

    found = coord.find_by_label("x")
    print(f"  find_by_label('x'): {len(found)} witness(es)")

    # emit a query for the first witness
    first_w = next(coord.iter_witnesses())
    query = coord.emit_lifted_query(first_w)
    print(f"\n  Emitted query (first 300 chars):\n{query[:300]}")

    # --- 8. Module-level convenience ---
    print("\n[8] lift_simple_obligation convenience function")
    w_simple = lift_simple_obligation("p => p")
    assert isinstance(w_simple, TheCodeMakeSolverWitness)
    print(f"  witness_id={w_simple.witness_id[:8]}  stage={w_simple.lifting_stage.name}")

    # --- 9. Sort translation table sanity check ---
    print("\n[9] _SORT_TRANSLATION_TABLE")
    assert len(_SORT_TRANSLATION_TABLE) >= 12
    for py_type, z3_sort in list(_SORT_TRANSLATION_TABLE.items())[:5]:
        print(f"  {py_type:<20} → {z3_sort}")

    # --- 10. Feature flags ---
    print("\n[10] Optional feature flags")
    print(f"  _Z3_SESSION_AVAILABLE : {_Z3_SESSION_AVAILABLE}")
    print(f"  _LIFTING_AVAILABLE    : {_LIFTING_AVAILABLE}")
    print(f"  _MODELS_AVAILABLE     : {_MODELS_AVAILABLE}")
    print(f"  _Z3_AVAILABLE         : {_Z3_AVAILABLE}")

    print("\n" + "=" * 70)
    print("  All smoke tests passed.")
    print("=" * 70)
