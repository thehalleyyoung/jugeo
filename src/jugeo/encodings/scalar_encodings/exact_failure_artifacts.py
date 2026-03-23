"""# copilot: Exact failure artifacts – precise encoding of failure modes as structured objects

Exact failure artifacts: extraction, classification, and regression seeding.

This module implements the exact-failure-artifact extraction phase of the Z3
encoding pipeline, as described in **Chapter 26.5** of
``preliminaries/theory2.tex``.

What Is an Exact Failure Artifact?
-----------------------------------
A Z3 satisfiability query either:

1. Returns ``unsat`` — the obligation holds; no failure.
2. Returns ``sat`` with a *model* — the model is a concrete counterexample.
3. Returns ``unknown`` — the solver timed out or the fragment is undecidable.

An **exact failure artifact** arises in case (2): the Z3 model assigns
concrete values to every free variable in the query, and those values
*precisely* witness the failure.  "Exact" contrasts with "approximate":

* An **approximate** artifact might arise from a timeout (case 3) and contain
  only partial information (e.g. only the values of a subset of variables).
* An **exact** artifact contains the *full* model: values for all free
  variables, satisfying every assertion in the query.

Exact artifacts are valuable for three downstream uses:

1. **Failure reproduction**: running the program with the concrete assignments
   from the model will reproduce the failing execution.
2. **Repair seeding**: the concrete values provide starting points for
   constraint-guided repair (e.g. weakening a refinement predicate until it
   admits the counterexample).
3. **Regression testing**: the concrete assignments can be serialised as unit
   test inputs, so the fixed program is automatically tested against the
   original failure.

Model Dump Format
-----------------
The Z3 Python API's ``Model.__repr__`` produces a string of the form::

    [x = 3, y = -1, flag = False]

This module parses such dump strings to extract the ``{variable: value}``
mapping.  The ``parse_z3_model_dump`` method accepts both the bracket format
above and a more verbose format with one assignment per line.

Failure Classification
----------------------
Once the concrete assignments are known, the failure is classified by
:meth:`ExactFailureArtifactsAnalyzer.classify_failure_kind` using heuristic
pattern matching on the obligation formula:

* **EXACT_REFUTATION**: the obligation is a negation — a refuted ``not P``.
* **EXACT_BOUNDARY_VIOLATION**: the obligation mentions a boundary value (e.g.
  an obligation ``(>= x BOUND)`` with an assignment ``x < BOUND``).
* **EXACT_ARITHMETIC_FAILURE**: the obligation contains arithmetic operators
  (``+``, ``-``, ``*``, ``/``, ``mod``).
* **EXACT_TYPE_ERROR**: the obligation contains a type predicate that was
  violated.
* **EXACT_GUARD_VIOLATION**: the obligation is a guard condition on a
  conditional branch.
* **EXACT_PATH_VIOLATION**: the failure occurred under a non-trivial path
  predicate (path predicate is non-empty and non-true).
* **EXACT_POSTCONDITION_FAILURE**: the obligation is a postcondition (heuristic:
  the formula mentions ``_return`` or ``result``).

Architecture
------------
* **ExactFailureKind** — :class:`~enum.Enum` classifying failure types.
* **ExactFailureArtifactsWitness** — frozen dataclass recording an exact
  failure with full provenance.
* **ExactFailureArtifactsAnalyzer** — stateless analysis helper for
  extraction, classification, and regression generation.
* **ExactFailureArtifactsCoordinator** — session-level coordinator with
  cache and stats.

copilot note
------------
After a Z3 ``check-sat`` returns ``sat``, call
:func:`extract_simple_exact_artifact` with the obligation string and the
``str(model)`` dump to obtain a witness.  The witness's
:meth:`~ExactFailureArtifactsWitness.to_regression_test_body` method produces
a Python ``assert`` block that can be pasted directly into a test file.
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
from typing import Any, ClassVar

# copilot: optional jugeo imports with availability flags.
try:
    from jugeo.encodings.scalar_encodings.models import (
        SortKind,
        FragmentHint,
        EncodeStatus,
        EncodingContext,
        EncodingResult,
    )
    _MODELS_AVAILABLE = True
except ImportError:
    SortKind = None  # type: ignore[assignment,misc]
    FragmentHint = None  # type: ignore[assignment,misc]
    EncodeStatus = None  # type: ignore[assignment,misc]
    EncodingContext = None  # type: ignore[assignment,misc]
    EncodingResult = None  # type: ignore[assignment,misc]
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

# ============================== failure kind enum ==============================


class ExactFailureKind(Enum):
    """Classification of exact Z3 failure artifacts.

    Each member represents a distinct category of counterexample returned by
    a Z3 ``sat`` result.  The classification guides downstream actions:
    repair strategies, regression test shapes, and severity assessment.

    Members
    -------
    EXACT_REFUTATION
        The failing obligation is a universal refutation: the formula
        ``P`` is satisfiable, meaning ``P`` is not a tautology.  This is
        the most basic kind of counterexample.
    EXACT_BOUNDARY_VIOLATION
        The counterexample witnesses a boundary condition violation: a
        value that was supposed to lie within a range does not.
    EXACT_ARITHMETIC_FAILURE
        The failure involves an arithmetic operator whose operands or result
        violated the expected refinement predicate.
    EXACT_TYPE_ERROR
        The failure witnesses a type predicate violation: a value assigned to
        a variable does not satisfy the declared type constraint.
    EXACT_GUARD_VIOLATION
        The failure witnesses a branch guard that was supposed to hold but
        does not given the model's assignments.
    EXACT_PATH_VIOLATION
        The failure is conditioned on a non-trivial path predicate; it only
        occurs along a specific execution path.
    EXACT_POSTCONDITION_FAILURE
        The counterexample witnesses a postcondition violation: the post-state
        does not satisfy the function's postcondition.
    """

    EXACT_REFUTATION = auto()
    EXACT_BOUNDARY_VIOLATION = auto()
    EXACT_ARITHMETIC_FAILURE = auto()
    EXACT_TYPE_ERROR = auto()
    EXACT_GUARD_VIOLATION = auto()
    EXACT_PATH_VIOLATION = auto()
    EXACT_POSTCONDITION_FAILURE = auto()

    # ------------------------------------------------------------------ #
    # Classification helpers                                               #
    # ------------------------------------------------------------------ #

    def is_arithmetic(self) -> bool:
        """Return True if this failure kind involves arithmetic operations.

        Returns
        -------
        bool

        Examples
        --------
        >>> ExactFailureKind.EXACT_ARITHMETIC_FAILURE.is_arithmetic()
        True
        >>> ExactFailureKind.EXACT_TYPE_ERROR.is_arithmetic()
        False
        """
        return self in (
            ExactFailureKind.EXACT_ARITHMETIC_FAILURE,
            ExactFailureKind.EXACT_BOUNDARY_VIOLATION,
        )

    def is_type_related(self) -> bool:
        """Return True if this failure kind is related to type constraints.

        Returns
        -------
        bool

        Examples
        --------
        >>> ExactFailureKind.EXACT_TYPE_ERROR.is_type_related()
        True
        >>> ExactFailureKind.EXACT_REFUTATION.is_type_related()
        False
        """
        return self in (
            ExactFailureKind.EXACT_TYPE_ERROR,
            ExactFailureKind.EXACT_GUARD_VIOLATION,
        )

    def is_path_related(self) -> bool:
        """Return True if this failure kind is conditioned on a path predicate.

        Returns
        -------
        bool

        Examples
        --------
        >>> ExactFailureKind.EXACT_PATH_VIOLATION.is_path_related()
        True
        >>> ExactFailureKind.EXACT_BOUNDARY_VIOLATION.is_path_related()
        False
        """
        return self in (
            ExactFailureKind.EXACT_PATH_VIOLATION,
            ExactFailureKind.EXACT_POSTCONDITION_FAILURE,
        )

    def severity(self) -> int:
        """Return an integer severity score for this failure kind.

        Severity is used to prioritise repair and regression testing.  Higher
        severity indicates more impactful failures.

        Returns
        -------
        int
            A score in the range ``[1, 5]`` where 5 is most severe.

        Examples
        --------
        >>> ExactFailureKind.EXACT_POSTCONDITION_FAILURE.severity()
        5
        >>> ExactFailureKind.EXACT_REFUTATION.severity()
        2
        """
        _severity_map: dict[ExactFailureKind, int] = {
            ExactFailureKind.EXACT_REFUTATION: 2,
            ExactFailureKind.EXACT_BOUNDARY_VIOLATION: 3,
            ExactFailureKind.EXACT_ARITHMETIC_FAILURE: 4,
            ExactFailureKind.EXACT_TYPE_ERROR: 4,
            ExactFailureKind.EXACT_GUARD_VIOLATION: 3,
            ExactFailureKind.EXACT_PATH_VIOLATION: 3,
            ExactFailureKind.EXACT_POSTCONDITION_FAILURE: 5,
        }
        return _severity_map[self]

    def smt2_trigger_template(self) -> str:
        """Return an SMT-LIB 2 comment template for this failure kind.

        The template can be inserted into SMT2 scripts to annotate obligation
        failure points with the expected failure category.

        Returns
        -------
        str
            An SMT2 comment string.

        Examples
        --------
        >>> ExactFailureKind.EXACT_ARITHMETIC_FAILURE.smt2_trigger_template()
        '; exact-failure: arithmetic (severity=4)'
        """
        return f"; exact-failure: {self.name.lower().replace('exact_', '')} (severity={self.severity()})"


# ============================== witness dataclass ==============================


@dataclass(frozen=True)
class ExactFailureArtifactsWitness:
    """Exact failure artifact extracted from a Z3 satisfying model.

    An instance of this class records a complete counterexample to a proof
    obligation: the obligation formula, the concrete variable assignments from
    the Z3 model, the failure classification, provenance metadata, and
    computed scores for reproducibility and repair utility.

    Fields
    ------
    witness_id : str
        Unique identifier (UUID4 hex string).
    obligation_smt : str
        The SMT-LIB 2 formula for the obligation that was falsified.
    concrete_assignments : tuple[tuple[str, str], ...]
        Sequence of ``(variable_name, concrete_value_string)`` pairs from the
        Z3 model.  Values are the string representations produced by Z3's
        model formatter.
    failure_kind : ExactFailureKind
        The classified kind of this exact failure.
    path_predicate : str
        The path predicate under which the failure occurred; ``""`` or
        ``"true"`` for non-path-sensitive failures.
    is_exact : bool
        ``True`` if the artifact was extracted from a complete Z3 model (no
        partial models from timeouts).
    reproducibility_score : float
        A score in ``[0.0, 1.0]`` estimating how easily the failure can be
        reproduced by running the program with the concrete assignments.
    repair_seed_quality : float
        A score in ``[0.0, 1.0]`` estimating how useful the concrete
        assignments are as a starting point for automated repair.
    copilot_label : str
        Optional copilot annotation label.
    z3_model_dump : str
        The raw Z3 model dump string from which the assignments were parsed.
    timestamp : float
        Unix timestamp of witness creation.
    """

    witness_id: str
    obligation_smt: str
    concrete_assignments: tuple[tuple[str, str], ...]
    failure_kind: ExactFailureKind
    path_predicate: str
    is_exact: bool
    reproducibility_score: float
    repair_seed_quality: float
    copilot_label: str
    z3_model_dump: str
    timestamp: float

    # ------------------------------------------------------------------ #
    # Accessor helpers                                                     #
    # ------------------------------------------------------------------ #

    def variable_names(self) -> list[str]:
        """Return the list of variable names in the concrete assignments.

        Returns
        -------
        list[str]
            Variable names in assignment order.

        Examples
        --------
        >>> w.variable_names()
        ['x', 'y', 'flag']
        """
        return [name for name, _ in self.concrete_assignments]

    def value_for(self, var: str) -> str:
        """Look up the concrete value assigned to a variable.

        Parameters
        ----------
        var:
            Variable name to look up.

        Returns
        -------
        str
            The concrete value string, or ``"<unknown>"`` if not found.

        Examples
        --------
        >>> w.value_for("x")
        '3'
        """
        # copilot: linear scan acceptable; models rarely exceed O(100) vars.
        for name, value in self.concrete_assignments:
            if name == var:
                return value
        logger.warning("value_for: variable %r not found in assignments", var)
        return "<unknown>"

    # ------------------------------------------------------------------ #
    # Regression and blocking                                              #
    # ------------------------------------------------------------------ #

    def to_regression_test_body(self) -> str:
        """Generate a Python unit-test assertion body for this failure.

        The regression test body consists of a series of variable assignments
        followed by an assertion that exercises the violated obligation.

        Returns
        -------
        str
            A Python code string (indented with 4 spaces) suitable for
            insertion into a ``def test_...`` function body.

        Examples
        --------
        >>> print(w.to_regression_test_body())
            # Exact failure regression (id=abc123)
            # Obligation: (> x 0)
            x = 3
            y = -1
            assert not (x > 0), "regression: exact failure id=abc123"
        """
        lines: list[str] = [
            f"    # Exact failure regression (id={self.witness_id[:8]})",
            f"    # Obligation: {self.obligation_smt}",
            f"    # Failure kind: {self.failure_kind.name}",
            f"    # Severity: {self.failure_kind.severity()}",
        ]
        if self.path_predicate and self.path_predicate not in ("", "true"):
            lines.append(f"    # Path predicate: {self.path_predicate}")
        lines.append("    # Concrete assignments from Z3 model:")
        for var, value in self.concrete_assignments:
            # copilot: convert Z3 value strings to Python literals where possible.
            py_value = _z3_value_to_python_literal(value)
            lines.append(f"    {var} = {py_value}")
        obligation_py = _smt2_to_python_comment(self.obligation_smt)
        lines.append(
            f'    assert {obligation_py}, '
            f'"regression: exact failure id={self.witness_id[:8]}"'
        )
        return "\n".join(lines)

    def to_smt2_blocking_clause(self) -> str:
        """Return an SMT-LIB 2 blocking clause for this failure's model.

        The blocking clause is a disjunction of the negations of each
        concrete assignment.  Adding this clause to the Z3 context prevents
        the solver from returning the same model again.

        Returns
        -------
        str
            An SMT2 ``(assert (or ...))`` blocking clause.

        Examples
        --------
        >>> w.to_smt2_blocking_clause()
        '(assert (or (not (= x 3)) (not (= y -1))))'
        """
        negations: list[str] = []
        for var, value in self.concrete_assignments:
            negations.append(f"(not (= {var} {value}))")
        if not negations:
            return "; no assignments to block"
        if len(negations) == 1:
            return f"(assert {negations[0]})"
        return "(assert (or " + " ".join(negations) + "))"

    def fingerprint(self) -> str:
        """Compute a stable content fingerprint for this witness.

        Returns
        -------
        str
            A 32-character lowercase hex string.

        Examples
        --------
        >>> w.fingerprint()
        'c7d4e3b2...'
        """
        payload = json.dumps(
            {
                "obligation": self.obligation_smt,
                "assignments": list(self.concrete_assignments),
                "kind": self.failure_kind.name,
                "path": self.path_predicate,
            },
            sort_keys=True,
        )
        return hashlib.md5(payload.encode()).hexdigest()

    def merge(
        self, other: ExactFailureArtifactsWitness
    ) -> ExactFailureArtifactsWitness:
        """Merge two exact failure witnesses.

        The merged witness combines the concrete assignments from both
        witnesses (``self`` takes precedence for overlapping variables) and
        averages the reproducibility and repair scores.

        Parameters
        ----------
        other:
            The other witness to merge with.

        Returns
        -------
        ExactFailureArtifactsWitness
            A merged witness.

        Examples
        --------
        >>> w_merged = w1.merge(w2)
        """
        self_vars = {name for name, _ in self.concrete_assignments}
        extra = [(n, v) for n, v in other.concrete_assignments if n not in self_vars]
        merged_assignments = self.concrete_assignments + tuple(extra)

        avg_repro = (self.reproducibility_score + other.reproducibility_score) / 2.0
        avg_repair = (self.repair_seed_quality + other.repair_seed_quality) / 2.0

        return ExactFailureArtifactsWitness(
            witness_id=str(uuid.uuid4()).replace("-", ""),
            obligation_smt=self.obligation_smt,
            concrete_assignments=merged_assignments,
            failure_kind=self.failure_kind,
            path_predicate=self.path_predicate or other.path_predicate,
            is_exact=self.is_exact and other.is_exact,
            reproducibility_score=avg_repro,
            repair_seed_quality=avg_repair,
            copilot_label=f"merged:{self.witness_id[:8]}+{other.witness_id[:8]}",
            z3_model_dump=self.z3_model_dump,
            timestamp=time.time(),
        )

    def copilot_exact_hint(self) -> str:
        """Return a copilot-style hint for this exact failure witness.

        Returns
        -------
        str
            Multi-line hint beginning with ``# copilot:``.

        Examples
        --------
        >>> print(w.copilot_exact_hint())
        # copilot: exact failure artifact id=abc123 kind=EXACT_REFUTATION severity=2
        """
        return (
            f"# copilot: exact failure artifact id={self.witness_id[:8]}\n"
            f"# copilot: kind={self.failure_kind.name} "
            f"severity={self.failure_kind.severity()}\n"
            f"# copilot: reproducibility={self.reproducibility_score:.3f} "
            f"repair_quality={self.repair_seed_quality:.3f}\n"
            f"# copilot: is_exact={self.is_exact} "
            f"assignments={len(self.concrete_assignments)}"
        )


# ============================== helper functions ==============================


def _z3_value_to_python_literal(value_str: str) -> str:
    """Convert a Z3 model value string to a Python literal.

    Handles integers, rationals (``3/4``), booleans (``True``/``False``),
    and strings.  Unrecognised formats are returned quoted.

    Parameters
    ----------
    value_str:
        Value string from Z3's model formatter.

    Returns
    -------
    str
        A Python literal string.
    """
    stripped = value_str.strip()
    # Boolean
    if stripped in ("True", "False"):
        return stripped
    # Integer
    try:
        int(stripped)
        return stripped
    except ValueError:
        pass
    # Rational: "3/4" or "-1/2"
    if "/" in stripped:
        parts = stripped.split("/")
        if len(parts) == 2:
            try:
                num = int(parts[0].strip())
                den = int(parts[1].strip())
                return f"{num / den}"
            except ValueError:
                pass
    # Negative integers in Z3 are sometimes written as "(- 5)"
    if stripped.startswith("(-") and stripped.endswith(")"):
        inner = stripped[2:-1].strip()
        try:
            return str(-int(inner))
        except ValueError:
            pass
    # Default: return as string literal
    return repr(stripped)


def _smt2_to_python_comment(smt2: str) -> str:
    """Convert a simple SMT2 formula to an approximate Python expression comment.

    This is a best-effort heuristic for use in regression test bodies.
    Only simple arithmetic comparisons are translated; complex formulae
    are returned as a ``True`` placeholder with a comment.

    Parameters
    ----------
    smt2:
        An SMT2 formula string.

    Returns
    -------
    str
        An approximate Python boolean expression, or ``True`` with a comment.
    """
    # copilot: very limited translation — sufficient for smoke tests.
    _replacements = [
        ("(> ", ""),
        ("(< ", ""),
        ("(>= ", ""),
        ("(<= ", ""),
        ("(= ", ""),
        ("(not ", "not "),
        ("(and ", ""),
        ("(or ", ""),
    ]
    result = smt2
    for old, new in _replacements:
        result = result.replace(old, new)
    result = result.replace(")", "")
    result = result.strip()
    if not result:
        return "True  # could not translate obligation"
    return f"True  # obligation: {smt2!r}"


# ============================== analyzer ==============================


class ExactFailureArtifactsAnalyzer:
    """Extracts and analyzes exact failure artifacts from Z3 models.

    This analyzer accepts obligation strings and Z3 model dump strings, and
    produces :class:`ExactFailureArtifactsWitness` instances with full
    provenance and quality scores.

    The analyzer maintains a small internal state for caching parsed model
    dumps, but is otherwise stateless across calls.

    copilot: Call :meth:`extract_exact_artifact` after each ``sat`` result
    from a Z3 query.  The returned witness captures all information needed
    for failure reproduction and repair seeding.
    """

    def __init__(self) -> None:
        """Initialise the analyzer."""
        # copilot: cache avoids re-parsing repeated model dumps.
        self._model_parse_cache: dict[str, dict[str, str]] = {}
        logger.debug("ExactFailureArtifactsAnalyzer initialised")

    # ------------------------------------------------------------------ #
    # Core extraction                                                      #
    # ------------------------------------------------------------------ #

    def extract_exact_artifact(
        self,
        obligation: str,
        z3_model_dump: str,
        path_predicate: str = "",
    ) -> ExactFailureArtifactsWitness:
        """Extract an exact failure artifact from a Z3 model dump.

        This is the main entry point for artifact extraction.  It:

        1. Parses the Z3 model dump to extract concrete assignments.
        2. Checks whether the model is exact (all free variables assigned).
        3. Classifies the failure kind.
        4. Computes reproducibility and repair scores.

        Parameters
        ----------
        obligation:
            The SMT-LIB 2 obligation formula that was falsified.
        z3_model_dump:
            The raw Z3 model dump string (e.g. ``"[x = 3, y = -1]"``).
        path_predicate:
            Optional path predicate under which the failure occurred.

        Returns
        -------
        ExactFailureArtifactsWitness
            The extracted exact failure artifact.

        Examples
        --------
        >>> w = analyzer.extract_exact_artifact("(> x 0)", "[x = -1, y = 5]")
        >>> w.value_for("x")
        '-1'
        """
        assignments = self.parse_z3_model_dump(z3_model_dump)
        is_exact = self._is_exact_model(z3_model_dump)
        failure_kind = self.classify_failure_kind(obligation, assignments)
        repro_score = self.compute_reproducibility_score(assignments)

        witness_proto = ExactFailureArtifactsWitness(
            witness_id=str(uuid.uuid4()).replace("-", ""),
            obligation_smt=obligation,
            concrete_assignments=tuple(sorted(assignments.items())),
            failure_kind=failure_kind,
            path_predicate=path_predicate,
            is_exact=is_exact,
            reproducibility_score=repro_score,
            repair_seed_quality=0.0,  # placeholder; computed below
            copilot_label="",
            z3_model_dump=z3_model_dump,
            timestamp=time.time(),
        )
        repair_quality = self.compute_repair_seed_quality(witness_proto)

        return ExactFailureArtifactsWitness(
            witness_id=witness_proto.witness_id,
            obligation_smt=obligation,
            concrete_assignments=tuple(sorted(assignments.items())),
            failure_kind=failure_kind,
            path_predicate=path_predicate,
            is_exact=is_exact,
            reproducibility_score=repro_score,
            repair_seed_quality=repair_quality,
            copilot_label="",
            z3_model_dump=z3_model_dump,
            timestamp=witness_proto.timestamp,
        )

    # ------------------------------------------------------------------ #
    # Model dump parsing                                                   #
    # ------------------------------------------------------------------ #

    def parse_z3_model_dump(self, model_dump: str) -> dict[str, str]:
        """Parse a Z3 model dump string into a variable→value mapping.

        Accepts two formats:

        1. Bracket format: ``"[x = 3, y = -1, flag = False]"``
        2. Multi-line format: one assignment per line, ``"x = 3"``

        Parameters
        ----------
        model_dump:
            Raw Z3 model dump string.

        Returns
        -------
        dict[str, str]
            Mapping from variable name to concrete value string.

        Examples
        --------
        >>> analyzer.parse_z3_model_dump("[x = 3, y = -1]")
        {'x': '3', 'y': '-1'}
        """
        if model_dump in self._model_parse_cache:
            return self._model_parse_cache[model_dump]

        result: dict[str, str] = {}
        stripped = model_dump.strip()

        if stripped.startswith("[") and stripped.endswith("]"):
            # Bracket format: "[x = 3, y = -1, flag = False]"
            inner = stripped[1:-1]
            for part in self._split_top_level(inner, ","):
                part = part.strip()
                if "=" in part:
                    eq_idx = part.index("=")
                    var = part[:eq_idx].strip()
                    value = part[eq_idx + 1:].strip()
                    if var:
                        result[var] = value
        else:
            # Multi-line format
            for line in stripped.splitlines():
                line = line.strip()
                if "=" in line:
                    eq_idx = line.index("=")
                    var = line[:eq_idx].strip()
                    value = line[eq_idx + 1:].strip()
                    if var and not var.startswith(";"):
                        result[var] = value

        self._model_parse_cache[model_dump] = result
        logger.debug("parse_z3_model_dump: extracted %d assignments", len(result))
        return result

    # ------------------------------------------------------------------ #
    # Failure classification                                               #
    # ------------------------------------------------------------------ #

    def classify_failure_kind(
        self,
        obligation: str,
        assignments: dict[str, str],
    ) -> ExactFailureKind:
        """Classify the exact failure kind from the obligation and assignments.

        Uses heuristic pattern matching on the obligation formula string:

        * ``(not ...)`` outer → :attr:`ExactFailureKind.EXACT_REFUTATION`
        * ``_return`` or ``result`` in formula → :attr:`ExactFailureKind.EXACT_POSTCONDITION_FAILURE`
        * ``+``, ``-``, ``*``, ``/``, ``mod`` in formula → :attr:`ExactFailureKind.EXACT_ARITHMETIC_FAILURE`
        * Boundary keywords (``max``, ``min``, ``bound``, ``len``, ``size``) → :attr:`ExactFailureKind.EXACT_BOUNDARY_VIOLATION`
        * ``guard`` or ``ite`` → :attr:`ExactFailureKind.EXACT_GUARD_VIOLATION`
        * ``type`` or ``sort`` → :attr:`ExactFailureKind.EXACT_TYPE_ERROR`
        * Path predicate present → :attr:`ExactFailureKind.EXACT_PATH_VIOLATION`
        * Default → :attr:`ExactFailureKind.EXACT_REFUTATION`

        Parameters
        ----------
        obligation:
            The falsified SMT2 obligation formula.
        assignments:
            The concrete variable assignments from the Z3 model.

        Returns
        -------
        ExactFailureKind

        Examples
        --------
        >>> analyzer.classify_failure_kind("(> _return 0)", {"_return": "-1"})
        <ExactFailureKind.EXACT_POSTCONDITION_FAILURE: 7>
        """
        obl_lower = obligation.lower()

        # copilot: order of checks matters — more specific checks first.
        if "_return" in obl_lower or "result" in obl_lower:
            return ExactFailureKind.EXACT_POSTCONDITION_FAILURE
        if any(kw in obl_lower for kw in ("max", "min", "bound", "len", "size", "limit")):
            return ExactFailureKind.EXACT_BOUNDARY_VIOLATION
        if any(op in obl_lower for op in ("+", "-", "*", "/", "mod", "rem", "div")):
            return ExactFailureKind.EXACT_ARITHMETIC_FAILURE
        if any(kw in obl_lower for kw in ("guard", "ite", "branch")):
            return ExactFailureKind.EXACT_GUARD_VIOLATION
        if any(kw in obl_lower for kw in ("type", "sort", "instanceof", "typep")):
            return ExactFailureKind.EXACT_TYPE_ERROR
        if obl_lower.startswith("(not "):
            return ExactFailureKind.EXACT_REFUTATION
        return ExactFailureKind.EXACT_REFUTATION

    # ------------------------------------------------------------------ #
    # Score computation                                                    #
    # ------------------------------------------------------------------ #

    def compute_reproducibility_score(
        self, assignments: dict[str, str]
    ) -> float:
        """Compute the reproducibility score for a set of concrete assignments.

        The reproducibility score is an estimate in ``[0.0, 1.0]`` of how
        easily the failure can be reproduced:

        * Score = 1.0 if all assignments are ground (no symbolic values).
        * Score is reduced by 0.1 for each symbolic / unknown value.
        * Score is reduced by 0.05 for each non-standard value (e.g.
          rational ``3/4`` that requires floating-point conversion).

        Parameters
        ----------
        assignments:
            The concrete variable assignments.

        Returns
        -------
        float
            Reproducibility score in ``[0.0, 1.0]``.

        Examples
        --------
        >>> analyzer.compute_reproducibility_score({"x": "3", "y": "-1"})
        1.0
        """
        if not assignments:
            return 0.5
        score = 1.0
        for var, value in assignments.items():
            val = value.strip()
            if not val or val == "<unknown>":
                score -= 0.1
            elif "/" in val and not val.startswith('"'):
                # Rational value: requires floating-point conversion
                score -= 0.05
            elif val.startswith("("):
                # SMT2 expression — symbolic value
                score -= 0.1
        return max(0.0, min(1.0, score))

    def compute_repair_seed_quality(
        self, witness: ExactFailureArtifactsWitness
    ) -> float:
        """Compute the repair seed quality for an exact failure witness.

        The repair seed quality is an estimate in ``[0.0, 1.0]`` of how
        useful the witness's concrete assignments are for automated repair:

        * Base score = ``reproducibility_score``.
        * Bonus of 0.1 if the failure kind is :attr:`~ExactFailureKind.EXACT_BOUNDARY_VIOLATION`
          (boundary violations are easy to relax).
        * Bonus of 0.05 if the witness is exact.
        * Penalty of 0.1 for :attr:`~ExactFailureKind.EXACT_TYPE_ERROR`
          (type errors are harder to repair automatically).
        * Penalty of 0.05 for :attr:`~ExactFailureKind.EXACT_PATH_VIOLATION`
          (path-conditional repairs require additional path analysis).

        Parameters
        ----------
        witness:
            The failure witness.

        Returns
        -------
        float
            Repair seed quality in ``[0.0, 1.0]``.

        Examples
        --------
        >>> analyzer.compute_repair_seed_quality(w)
        0.85
        """
        score = witness.reproducibility_score
        if witness.failure_kind is ExactFailureKind.EXACT_BOUNDARY_VIOLATION:
            score += 0.1
        if witness.is_exact:
            score += 0.05
        if witness.failure_kind is ExactFailureKind.EXACT_TYPE_ERROR:
            score -= 0.1
        if witness.failure_kind is ExactFailureKind.EXACT_PATH_VIOLATION:
            score -= 0.05
        return max(0.0, min(1.0, score))

    # ------------------------------------------------------------------ #
    # Regression test generation                                           #
    # ------------------------------------------------------------------ #

    def generate_regression_test(
        self, witness: ExactFailureArtifactsWitness
    ) -> str:
        """Generate a complete Python regression test function for a witness.

        The regression test function:

        1. Sets the concrete variable values.
        2. Calls the function under test (as a stub).
        3. Asserts that the obligation holds.

        Parameters
        ----------
        witness:
            The exact failure witness.

        Returns
        -------
        str
            A Python function definition string.

        Examples
        --------
        >>> print(analyzer.generate_regression_test(w))
        def test_regression_abc12345():
            ...
        """
        lines: list[str] = [
            f"def test_regression_{witness.witness_id[:8]}():",
            f'    """Regression test for exact failure {witness.witness_id[:8]}.',
            f"",
            f"    Obligation: {witness.obligation_smt}",
            f"    Failure kind: {witness.failure_kind.name}",
            f"    Severity: {witness.failure_kind.severity()}",
            f"    Reproducibility: {witness.reproducibility_score:.3f}",
            f"    Repair seed quality: {witness.repair_seed_quality:.3f}",
            f'    """',
        ]
        lines.append(witness.to_regression_test_body())
        return "\n".join(lines)

    # ------------------------------------------------------------------ #
    # Batch extraction                                                     #
    # ------------------------------------------------------------------ #

    def batch_extract(
        self,
        pairs: list[tuple[str, str]],
    ) -> list[ExactFailureArtifactsWitness]:
        """Extract exact failure artifacts from a list of (obligation, model) pairs.

        Parameters
        ----------
        pairs:
            List of ``(obligation_smt, z3_model_dump)`` pairs.

        Returns
        -------
        list[ExactFailureArtifactsWitness]
            List of extracted witnesses, one per input pair.

        Examples
        --------
        >>> witnesses = analyzer.batch_extract([("(> x 0)", "[x = -1]"), ...])
        """
        results: list[ExactFailureArtifactsWitness] = []
        for obligation, model_dump in pairs:
            try:
                w = self.extract_exact_artifact(obligation, model_dump)
                results.append(w)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "batch_extract: failed to extract artifact for %r: %s",
                    obligation, exc,
                )
        logger.info("batch_extract: extracted %d artifacts from %d pairs", len(results), len(pairs))
        return results

    def copilot_exact_analysis_hint(
        self, witness: ExactFailureArtifactsWitness
    ) -> str:
        """Return a copilot-style hint for an exact failure analysis result.

        Parameters
        ----------
        witness:
            The witness to summarise.

        Returns
        -------
        str
            Multi-line hint string.

        Examples
        --------
        >>> print(analyzer.copilot_exact_analysis_hint(w))
        # copilot: exact failure analysis id=abc123 ...
        """
        return (
            f"# copilot: exact failure analysis id={witness.witness_id[:8]}\n"
            f"# copilot: kind={witness.failure_kind.name} "
            f"severity={witness.failure_kind.severity()}\n"
            f"# copilot: repro={witness.reproducibility_score:.3f} "
            f"repair={witness.repair_seed_quality:.3f}\n"
            f"# copilot: smt2_trigger={witness.failure_kind.smt2_trigger_template()!r}"
        )

    # ------------------------------------------------------------------ #
    # Private helpers                                                      #
    # ------------------------------------------------------------------ #

    def _is_exact_model(self, model_dump: str) -> bool:
        """Return True if the model dump appears to be from a complete Z3 model.

        A model is considered exact if it contains at least one explicit
        assignment and does not contain ``...`` or ``<partial>`` markers.

        Parameters
        ----------
        model_dump:
            Raw Z3 model dump string.

        Returns
        -------
        bool
        """
        if not model_dump or model_dump.strip() in ("[]", ""):
            return False
        if "..." in model_dump or "<partial>" in model_dump or "timeout" in model_dump.lower():
            return False
        if "=" in model_dump:
            return True
        return False

    def _extract_numeric_values(
        self, assignments: dict[str, str]
    ) -> dict[str, float]:
        """Extract numeric (float) values from the concrete assignments.

        Parses integer and rational Z3 model values; non-numeric values
        (booleans, strings, symbolic expressions) are skipped.

        Parameters
        ----------
        assignments:
            The raw variable→value mapping from Z3.

        Returns
        -------
        dict[str, float]
            Mapping from variable name to its numeric value.  Only variables
            with parseable numeric values are included.

        Examples
        --------
        >>> analyzer._extract_numeric_values({"x": "3", "y": "-2", "flag": "True"})
        {'x': 3.0, 'y': -2.0}
        """
        result: dict[str, float] = {}
        for var, value in assignments.items():
            val = value.strip()
            # Try integer
            try:
                result[var] = float(int(val))
                continue
            except ValueError:
                pass
            # Try float
            try:
                result[var] = float(val)
                continue
            except ValueError:
                pass
            # Try rational "3/4"
            if "/" in val:
                parts = val.split("/")
                if len(parts) == 2:
                    try:
                        result[var] = int(parts[0].strip()) / int(parts[1].strip())
                        continue
                    except (ValueError, ZeroDivisionError):
                        pass
            # Try Z3 negative "(-  5)"  →  already handled above
        return result

    def _build_blocking_clause(
        self, assignments: dict[str, str]
    ) -> str:
        """Build an SMT2 blocking clause from a concrete assignment dict.

        Delegates to :meth:`ExactFailureArtifactsWitness.to_smt2_blocking_clause`
        via a temporary witness construction.

        Parameters
        ----------
        assignments:
            The variable→value assignment dict.

        Returns
        -------
        str
            An SMT2 blocking clause.
        """
        negations = [f"(not (= {var} {value}))" for var, value in assignments.items()]
        if not negations:
            return "; no assignments to block"
        if len(negations) == 1:
            return f"(assert {negations[0]})"
        return "(assert (or " + " ".join(negations) + "))"

    def _split_top_level(self, text: str, sep: str) -> list[str]:
        """Split ``text`` at top-level occurrences of ``sep``.

        Respects nested parentheses so that ``(- 3, 4)`` is not split at the
        comma inside the expression.

        Parameters
        ----------
        text:
            Input string.
        sep:
            Single-character separator.

        Returns
        -------
        list[str]
            List of parts.
        """
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
            elif ch == sep and depth == 0:
                parts.append("".join(current))
                current = []
            else:
                current.append(ch)
        if current:
            parts.append("".join(current))
        return parts


# ============================== coordinator ==============================


class ExactFailureArtifactsCoordinator:
    """Main coordinator for exact failure artifact management.

    Maintains a session-level registry of all exact failure artifacts, a
    fingerprint-based ``_cache`` to avoid duplicates, and a ``_stats`` counter
    dict.  Exposes high-level entry points for registration, repair seeding,
    regression suite generation, and blocking clause set emission.

    Attributes
    ----------
    _analyzer : ExactFailureArtifactsAnalyzer
        The stateless analysis helper.
    _artifacts : list[ExactFailureArtifactsWitness]
        All registered artifacts in creation order.
    _cache : dict[str, ExactFailureArtifactsWitness]
        Fingerprint → witness cache for de-duplication.
    _stats : dict[str, int]
        Counters: ``"artifacts_registered"``, ``"cache_hits"``,
        ``"exact_artifacts"``, ``"approximate_artifacts"``.

    copilot: After each Z3 ``sat`` result in the encoding pipeline, call
    :meth:`register_exact_failure` to add the artifact to the registry.
    Use :meth:`emit_regression_suite` at the end of an analysis session to
    generate a test file.
    """

    def __init__(self) -> None:
        """Initialise the coordinator."""
        self._analyzer = ExactFailureArtifactsAnalyzer()
        self._artifacts: list[ExactFailureArtifactsWitness] = []
        self._cache: dict[str, ExactFailureArtifactsWitness] = {}
        self._stats: dict[str, int] = defaultdict(int)
        logger.debug("ExactFailureArtifactsCoordinator initialised")

    # ------------------------------------------------------------------ #
    # Registration                                                         #
    # ------------------------------------------------------------------ #

    def register_exact_failure(
        self,
        obligation: str,
        z3_model: str,
        path_pred: str = "",
    ) -> ExactFailureArtifactsWitness:
        """Register an exact failure artifact from a Z3 model.

        Extracts the artifact via
        :meth:`ExactFailureArtifactsAnalyzer.extract_exact_artifact`, checks
        the cache, and stores it in the session registry.

        Parameters
        ----------
        obligation:
            The falsified SMT2 obligation formula.
        z3_model:
            The raw Z3 model dump string.
        path_pred:
            Optional path predicate under which the failure occurred.

        Returns
        -------
        ExactFailureArtifactsWitness
            The registered artifact (possibly from cache if duplicate).

        Examples
        --------
        >>> w = coord.register_exact_failure("(> x 0)", "[x = -1]")
        """
        witness = self._analyzer.extract_exact_artifact(obligation, z3_model, path_pred)
        fp = witness.fingerprint()
        if fp in self._cache:
            self._stats["cache_hits"] += 1
            logger.debug("register_exact_failure: cache hit for %s", fp[:8])
            return self._cache[fp]

        self._cache[fp] = witness
        self._artifacts.append(witness)
        self._stats["artifacts_registered"] += 1
        if witness.is_exact:
            self._stats["exact_artifacts"] += 1
        else:
            self._stats["approximate_artifacts"] += 1
        logger.info(
            "register_exact_failure: registered %s kind=%s severity=%d repro=%.3f",
            witness.witness_id[:8], witness.failure_kind.name,
            witness.failure_kind.severity(), witness.reproducibility_score,
        )
        return witness

    # ------------------------------------------------------------------ #
    # Query methods                                                        #
    # ------------------------------------------------------------------ #

    def all_exact_artifacts(self) -> list[ExactFailureArtifactsWitness]:
        """Return all registered exact (non-approximate) failure artifacts.

        Returns
        -------
        list[ExactFailureArtifactsWitness]
            Filtered list of witnesses with ``is_exact=True``.

        Examples
        --------
        >>> exact = coord.all_exact_artifacts()
        """
        return [w for w in self._artifacts if w.is_exact]

    def all_repair_seeds(self) -> list[ExactFailureArtifactsWitness]:
        """Return all artifacts with repair seed quality >= 0.5.

        Returns
        -------
        list[ExactFailureArtifactsWitness]
            Artifacts sorted by repair seed quality (descending).

        Examples
        --------
        >>> seeds = coord.all_repair_seeds()
        """
        return sorted(
            [w for w in self._artifacts if w.repair_seed_quality >= 0.5],
            key=lambda w: w.repair_seed_quality,
            reverse=True,
        )

    # ------------------------------------------------------------------ #
    # Emission methods                                                     #
    # ------------------------------------------------------------------ #

    def emit_regression_suite(
        self, witnesses: list[ExactFailureArtifactsWitness]
    ) -> str:
        """Emit a complete Python regression test module for a list of witnesses.

        The generated module contains one test function per witness plus a
        module docstring and import block.

        Parameters
        ----------
        witnesses:
            The failure witnesses to generate tests for.

        Returns
        -------
        str
            A complete Python test module source string.

        Examples
        --------
        >>> suite = coord.emit_regression_suite(coord.all_exact_artifacts())
        >>> print(suite[:200])
        '''Regression tests for exact failure artifacts.'''
        ...
        """
        lines: list[str] = [
            '"""Regression tests for exact failure artifacts.',
            "",
            "Auto-generated by ExactFailureArtifactsCoordinator.",
            '"""',
            "",
            "import pytest",
            "",
        ]
        for w in witnesses:
            lines.append(self._analyzer.generate_regression_test(w))
            lines.append("")
        return "\n".join(lines)

    def emit_blocking_clause_set(
        self, witnesses: list[ExactFailureArtifactsWitness]
    ) -> str:
        """Emit a set of SMT-LIB 2 blocking clauses for a list of witnesses.

        The blocking clauses prevent Z3 from returning any of the given
        models again.

        Parameters
        ----------
        witnesses:
            The failure witnesses whose models should be blocked.

        Returns
        -------
        str
            A multi-line SMT-LIB 2 blocking clause block.

        Examples
        --------
        >>> print(coord.emit_blocking_clause_set(witnesses))
        ; blocking clause for abc12345 (EXACT_REFUTATION severity=2)
        (assert (or (not (= x -1)) ...))
        """
        lines: list[str] = [
            "; Z3 blocking clause set (exact failure artifacts)",
            f"; {len(witnesses)} clause(s)",
            "",
        ]
        for w in witnesses:
            lines.append(
                f"; blocking clause for {w.witness_id[:8]} "
                f"({w.failure_kind.name} severity={w.failure_kind.severity()})"
            )
            lines.append(w.to_smt2_blocking_clause())
        return "\n".join(lines)

    def exact_failure_report(self) -> str:
        """Return a human-readable summary of coordinator activity.

        Returns
        -------
        str
            A multi-line report string.

        Examples
        --------
        >>> print(coord.exact_failure_report())
        ExactFailureArtifactsCoordinator
          artifacts_registered: 5
          exact_artifacts: 4
        """
        lines = ["ExactFailureArtifactsCoordinator"]
        for key, value in sorted(self._stats.items()):
            lines.append(f"  {key}: {value}")
        lines.append(f"  total_artifacts: {len(self._artifacts)}")
        lines.append(f"  repair_seeds: {len(self.all_repair_seeds())}")

        # Kind breakdown
        kind_counts: dict[str, int] = defaultdict(int)
        for w in self._artifacts:
            kind_counts[w.failure_kind.name] += 1
        lines.append("  kind breakdown:")
        for kind, count in sorted(kind_counts.items()):
            lines.append(f"    {kind}: {count}")
        return "\n".join(lines)

    @property
    def stats(self) -> dict[str, int]:
        """Read-only copy of the operation statistics dict.

        Returns
        -------
        dict[str, int]
        """
        return dict(self._stats)

    def __repr__(self) -> str:
        """Return a concise repr string."""
        return (
            f"ExactFailureArtifactsCoordinator("
            f"artifacts={len(self._artifacts)}, "
            f"exact={self._stats['exact_artifacts']}, "
            f"z3={_Z3_AVAILABLE})"
        )


# ============================== module convenience ==============================


def extract_simple_exact_artifact(
    obligation: str,
    model_dump: str,
) -> ExactFailureArtifactsWitness:
    """Module-level convenience: extract an exact failure artifact.

    Creates a fresh :class:`ExactFailureArtifactsAnalyzer` and calls
    :meth:`~ExactFailureArtifactsAnalyzer.extract_exact_artifact`.

    Parameters
    ----------
    obligation:
        The falsified SMT-LIB 2 obligation formula.
    model_dump:
        The raw Z3 model dump string (e.g. ``"[x = -1, y = 5]"``).

    Returns
    -------
    ExactFailureArtifactsWitness
        The extracted exact failure artifact.

    Examples
    --------
    >>> w = extract_simple_exact_artifact("(> x 0)", "[x = -3, y = 7]")
    >>> w.value_for("x")
    '-3'
    >>> w.failure_kind
    <ExactFailureKind.EXACT_REFUTATION: 1>
    """
    # copilot: fresh analyzer for module-level calls.
    analyzer = ExactFailureArtifactsAnalyzer()
    return analyzer.extract_exact_artifact(obligation, model_dump)


# ============================== judgment geometry section ==============================
#
# Theory invariants for Judgment Geometry (Čech cohomology formulation):
#
#   A Judgment J is the 8-tuple:
#       J = (c, φ, A, E, O, B, T, Π)
#   where:
#       c  = context (typing environment / variable bindings)
#       φ  = formula / claim being judged (the principal obligation)
#       A  = axioms / background assumptions (ground truth in scope)
#       E  = evidence set (supporting sub-judgments and attestations)
#       O  = obstruction class  ∈  Čech H¹(𝒰; ℤ)
#              — the first Čech cohomology class of the cover 𝒰 of the
#                judgment domain; a non-trivial class records a persistent
#                failure that cannot be locally repaired
#       B  = basis / support (the sub-domain over which J is claimed)
#       T  = TrustTier  ∈  {UNTRUSTED, PROVISIONAL, TRUSTED, VERIFIED}
#              — an element of the ordered algebra (ℤ/4ℤ, ≤, ∧, ∨)
#       Π  = proof certificate (a tree of inference steps; possibly ∅)
#
#   TrustTier ordered algebra:
#       UNTRUSTED(0) < PROVISIONAL(1) < TRUSTED(2) < VERIFIED(3)
#       meet  = min,   join = max   (makes it a bounded distributive lattice)
#
#   Obstruction class semantics:
#       - Each failure artifact is a 1-cochain  δ ∈ C¹(𝒰; ℤ).
#       - The coboundary map δ: C⁰ → C¹ sends a local repair attempt to
#         a global consistency check.  If ∂δ ≠ 0  the repair is locally
#         inconsistent and the cochain is non-trivial in H¹.
#       - A catalog of failure artifacts therefore carries a direct-sum
#         decomposition of H¹ indexed by failure pattern.
#
#   Exact encoding:
#       "Exact" means no information is lost: the artifact can be replayed to
#       reproduce the failure deterministically.  This contrasts with
#       "approximate" artifacts arising from solver timeouts.


class TrustTier:
    """Ordered algebra of trust tiers for Judgment Geometry.

    TrustTier is a totally-ordered, bounded lattice:

        UNTRUSTED(0) < PROVISIONAL(1) < TRUSTED(2) < VERIFIED(3)

    Lattice operations:
        meet(t1, t2) = min(t1, t2)  (greatest lower bound)
        join(t1, t2) = max(t1, t2)  (least upper bound)
        bottom = UNTRUSTED (0)
        top    = VERIFIED  (3)

    This makes TrustTier a distributive lattice, which is the algebraic
    structure required for composing trust annotations across sub-judgments.
    In particular, the trust of a composed judgment J₁ ∧ J₂ is the meet of
    the individual trusts, ensuring the weakest link dominates.
    """

    UNTRUSTED: int = 0
    PROVISIONAL: int = 1
    TRUSTED: int = 2
    VERIFIED: int = 3

    _LABELS: dict[int, str] = {
        0: "UNTRUSTED",
        1: "PROVISIONAL",
        2: "TRUSTED",
        3: "VERIFIED",
    }

    _FROM_LABEL: dict[str, int] = {
        "UNTRUSTED": 0,
        "PROVISIONAL": 1,
        "TRUSTED": 2,
        "VERIFIED": 3,
    }

    @classmethod
    def label(cls, tier: int) -> str:
        """Return the string label for the given integer tier.

        Parameters
        ----------
        tier:
            Integer tier value in {0, 1, 2, 3}.

        Returns
        -------
        str
            Human-readable label.

        Raises
        ------
        KeyError
            If *tier* is not a valid TrustTier integer.

        Examples
        --------
        >>> TrustTier.label(TrustTier.VERIFIED)
        'VERIFIED'
        """
        return cls._LABELS[tier]

    @classmethod
    def from_label(cls, label: str) -> int:
        """Parse a string label into a TrustTier integer.

        Parameters
        ----------
        label:
            One of ``"UNTRUSTED"``, ``"PROVISIONAL"``, ``"TRUSTED"``,
            ``"VERIFIED"`` (case-sensitive).

        Returns
        -------
        int
            The corresponding TrustTier integer.

        Raises
        ------
        KeyError
            If *label* is not recognised.

        Examples
        --------
        >>> TrustTier.from_label("TRUSTED")
        2
        """
        return cls._FROM_LABEL[label]

    @classmethod
    def meet(cls, t1: int, t2: int) -> int:
        """Greatest lower bound (meet) of two TrustTier values.

        In the context of Judgment Geometry, the meet represents the trust
        level of a conjunction of two judgments: no stronger than the weaker
        of the two components.

        Parameters
        ----------
        t1, t2:
            TrustTier integers.

        Returns
        -------
        int
            ``min(t1, t2)``.

        Examples
        --------
        >>> TrustTier.meet(TrustTier.VERIFIED, TrustTier.PROVISIONAL)
        1
        """
        return min(t1, t2)

    @classmethod
    def join(cls, t1: int, t2: int) -> int:
        """Least upper bound (join) of two TrustTier values.

        The join represents the best trust achievable by combining evidence
        from two independent channels.

        Parameters
        ----------
        t1, t2:
            TrustTier integers.

        Returns
        -------
        int
            ``max(t1, t2)``.

        Examples
        --------
        >>> TrustTier.join(TrustTier.UNTRUSTED, TrustTier.TRUSTED)
        2
        """
        return max(t1, t2)

    @classmethod
    def is_sufficient(cls, tier: int, threshold: int) -> bool:
        """Return True if *tier* is at or above the given *threshold*.

        Parameters
        ----------
        tier:
            The tier to test.
        threshold:
            The required minimum tier.

        Returns
        -------
        bool

        Examples
        --------
        >>> TrustTier.is_sufficient(TrustTier.TRUSTED, TrustTier.PROVISIONAL)
        True
        >>> TrustTier.is_sufficient(TrustTier.UNTRUSTED, TrustTier.TRUSTED)
        False
        """
        return tier >= threshold

    @classmethod
    def degraded_by_failure(cls, tier: int) -> int:
        """Return the trust tier after it has been degraded by a failure.

        A single confirmed failure drops the tier by one step toward UNTRUSTED.
        UNTRUSTED cannot be degraded further.

        Parameters
        ----------
        tier:
            Current TrustTier integer.

        Returns
        -------
        int
            Degraded TrustTier (at minimum UNTRUSTED = 0).

        Examples
        --------
        >>> TrustTier.degraded_by_failure(TrustTier.TRUSTED)
        1
        >>> TrustTier.degraded_by_failure(TrustTier.UNTRUSTED)
        0
        """
        return max(0, tier - 1)


# ---------------------------------------------------------------------------
# Required frozen dataclasses for Judgment Geometry failure artifacts
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FailureMode:
    """Classification of a failure mode in Judgment Geometry.

    In the 8-tuple J = (c, φ, A, E, O, B, T, Π), a FailureMode characterises
    the nature of the failure that prevented the judgment from being accepted.
    The *category* field places the failure in one of six broad families:

    - ``TRUST_VIOLATION``  — the TrustTier T was insufficient for the claim φ.
    - ``OBSTRUCTION``      — the obstruction class O ∈ H¹ is non-trivial.
    - ``SCHEMA_ERROR``     — the structural schema of J was malformed.
    - ``PROOF_FAILURE``    — the proof certificate Π is absent or invalid.
    - ``WITNESS_INVALID``  — the failure witness is not constructive.
    - ``COVERAGE_GAP``     — the support B does not cover the claim domain.

    The *severity* field signals how the failure should be handled by
    downstream repair and regression machinery.

    Fields
    ------
    mode_id:
        Unique identifier for this FailureMode, typically a UUID or slug.
    name:
        Human-readable name (e.g. ``"trust_tier_too_low"``).
    category:
        One of the six canonical Judgment Geometry failure categories.
    severity:
        One of ``"FATAL"``, ``"ERROR"``, ``"WARNING"``, ``"INFO"``.
    is_recoverable:
        Whether the failure can in principle be repaired without re-issuing
        the judgment from scratch.
    """

    mode_id: str
    name: str
    category: str   # "TRUST_VIOLATION"|"OBSTRUCTION"|"SCHEMA_ERROR"|"PROOF_FAILURE"|"WITNESS_INVALID"|"COVERAGE_GAP"
    severity: str   # "FATAL"|"ERROR"|"WARNING"|"INFO"
    is_recoverable: bool

    _VALID_CATEGORIES: ClassVar[frozenset[str]] = frozenset({
        "TRUST_VIOLATION",
        "OBSTRUCTION",
        "SCHEMA_ERROR",
        "PROOF_FAILURE",
        "WITNESS_INVALID",
        "COVERAGE_GAP",
    })

    _VALID_SEVERITIES: ClassVar[frozenset[str]] = frozenset({
        "FATAL", "ERROR", "WARNING", "INFO",
    })

    def __post_init__(self) -> None:
        """Validate category and severity on construction."""
        if self.category not in self._VALID_CATEGORIES:
            raise ValueError(
                f"FailureMode.category must be one of {sorted(self._VALID_CATEGORIES)!r}, "
                f"got {self.category!r}"
            )
        if self.severity not in self._VALID_SEVERITIES:
            raise ValueError(
                f"FailureMode.severity must be one of {sorted(self._VALID_SEVERITIES)!r}, "
                f"got {self.severity!r}"
            )

    @property
    def severity_rank(self) -> int:
        """Integer rank of this severity: FATAL=3, ERROR=2, WARNING=1, INFO=0."""
        return {"FATAL": 3, "ERROR": 2, "WARNING": 1, "INFO": 0}[self.severity]

    @property
    def is_cohomological(self) -> bool:
        """True if this mode is directly related to the Čech H¹ obstruction class."""
        return self.category == "OBSTRUCTION"

    def short_repr(self) -> str:
        """Return a compact one-line string representation."""
        rec = "recoverable" if self.is_recoverable else "permanent"
        return f"<FailureMode {self.mode_id[:8]} {self.category}/{self.severity} {rec}>"


@dataclass(frozen=True)
class FailureWitness:
    """Witness to a failure event in Judgment Geometry.

    A FailureWitness records the minimal information needed to replay a
    judgment failure.  In the Čech cohomology picture, the witness is the
    *cochain* data: the specific input / formula assignment that makes the
    1-cocycle non-trivial.

    A *constructive* witness (``is_constructive=True``) provides an explicit
    distinguishing input — a concrete assignment to free variables that
    demonstrates the failure.  A non-constructive witness only records that
    a failure exists without providing a concrete example (e.g. from a
    timeout or non-deterministic oracle).

    Exact encoding principle: if ``is_constructive=True``, then
    ``distinguishing_input`` contains enough information to reproduce the
    failure deterministically.

    Fields
    ------
    witness_id:
        Unique identifier for this witness, typically a UUID.
    formula_failed:
        The formula φ that failed (as a string).
    context:
        String representation of the context c at the point of failure.
    distinguishing_input:
        A serialised assignment to free variables witnessing the failure.
        Empty string if ``is_constructive=False``.
    trust_at_witness:
        The TrustTier T recorded at the time of failure.
    is_constructive:
        Whether *distinguishing_input* provides a concrete counterexample.
    """

    witness_id: str
    formula_failed: str
    context: str
    distinguishing_input: str
    trust_at_witness: int
    is_constructive: bool

    def trust_label(self) -> str:
        """Return the string label of ``trust_at_witness``."""
        return TrustTier.label(self.trust_at_witness)

    def is_exact(self) -> bool:
        """Return True iff this witness constitutes an exact (constructive) record."""
        return self.is_constructive and bool(self.distinguishing_input)

    def cochain_key(self) -> str:
        """Return a deterministic key suitable for use as a Čech cochain index.

        The key is the SHA-256 of ``(formula_failed, distinguishing_input)``
        so that two witnesses with the same formula and input produce the same
        cochain key — allowing deduplication in the Čech complex.
        """
        raw = f"{self.formula_failed}|{self.distinguishing_input}"
        return hashlib.sha256(raw.encode()).hexdigest()

    def short_repr(self) -> str:
        """Return a compact one-line string representation."""
        constructive = "constructive" if self.is_constructive else "non-constructive"
        return (
            f"<FailureWitness {self.witness_id[:8]} "
            f"trust={TrustTier.label(self.trust_at_witness)} "
            f"{constructive}>"
        )


@dataclass(frozen=True)
class FailureArtifact:
    """A structured artifact encoding a failure event in Judgment Geometry.

    In the 8-tuple J = (c, φ, A, E, O, B, T, Π), a FailureArtifact is the
    record produced when J fails.  Specifically:

    - ``judgment_at_failure`` is the tuple (c, φ, A, E, O, B, T, Π) snapshotted
      at the moment of failure — all eight components are preserved.
    - ``failure_mode`` classifies the category of failure.
    - ``witness`` is the FailureWitness; if ``is_exact=True`` the witness is
      constructive and the artifact can be replayed deterministically.
    - ``trust_at_failure`` records the TrustTier T at failure time.
    - ``obstruction_class`` is a string representation of the Čech H¹ class O.
      An empty string represents the trivial class [0] ∈ H¹; a non-empty
      string encodes a non-trivial class.
    - ``timestamp`` is the UNIX timestamp of the failure event.
    - ``is_exact`` is True iff the artifact was produced from an exact
      (constructive) failure record.

    The artifact is immutable (frozen=True) so that it can be safely stored
    in sets, used as dictionary keys, and transmitted across process boundaries
    without defensive copying.

    Fields
    ------
    artifact_id:
        Unique identifier for this artifact, typically a UUID.
    failure_mode:
        The FailureMode classifying this failure.
    judgment_at_failure:
        The full judgment 8-tuple at the moment of failure.
    witness:
        The FailureWitness providing evidence for the failure.
    trust_at_failure:
        The integer TrustTier at failure time.
    obstruction_class:
        String encoding of the Čech H¹ obstruction class O.
    timestamp:
        UNIX timestamp of the failure event.
    is_exact:
        True iff the artifact provides an exact, lossless record.
    """

    artifact_id: str
    failure_mode: FailureMode
    judgment_at_failure: tuple  # type: ignore[type-arg]
    witness: FailureWitness
    trust_at_failure: int
    obstruction_class: str
    timestamp: float
    is_exact: bool

    def trust_label(self) -> str:
        """Return the string label of ``trust_at_failure``."""
        return TrustTier.label(self.trust_at_failure)

    def is_cohomologically_non_trivial(self) -> bool:
        """Return True if the obstruction class is non-trivial in Čech H¹."""
        return bool(self.obstruction_class) and self.obstruction_class != "0" and self.obstruction_class != "[0]"

    def fingerprint(self) -> str:
        """Return a deterministic SHA-256 fingerprint of the artifact.

        The fingerprint is computed over the artifact_id, failure_mode.mode_id,
        witness.cochain_key(), and obstruction_class so that two independently
        constructed artifacts with the same content produce the same fingerprint.
        """
        raw = (
            f"{self.artifact_id}"
            f"|{self.failure_mode.mode_id}"
            f"|{self.witness.cochain_key()}"
            f"|{self.obstruction_class}"
        )
        return hashlib.sha256(raw.encode()).hexdigest()

    def short_repr(self) -> str:
        """Return a compact one-line summary of the artifact."""
        exact = "exact" if self.is_exact else "approx"
        return (
            f"<FailureArtifact {self.artifact_id[:8]} "
            f"{self.failure_mode.category}/{self.failure_mode.severity} "
            f"trust={self.trust_label()} {exact}>"
        )


@dataclass(frozen=True)
class ExactFailureEncoding:
    """Exact encoding of a failure as a computable, serialisable object.

    In Judgment Geometry, "exact encoding" means that the failure can be
    reproduced from the encoding alone — no external state is needed.  This
    is captured by the ``is_lossless`` field: if True, the ``binary_repr``
    field contains a complete serialisation of the underlying FailureArtifact
    from which the original can be reconstructed.

    The ``encoding_kind`` field identifies the serialisation format:
    - ``"json_utf8"``    — UTF-8 JSON, human-readable.
    - ``"msgpack"``      — MessagePack binary, compact.
    - ``"pickle"``       — Python pickle (not recommended for cross-version use).
    - ``"custom_v1"``    — Internal custom binary format version 1.

    The ``schema_version`` field allows forward-compatible deserialization:
    decoders that understand ``schema_version=N`` should gracefully reject
    or down-convert artifacts with ``schema_version > N``.

    Fields
    ------
    encoding_id:
        Unique identifier for this encoding, typically a UUID.
    artifact:
        The FailureArtifact being encoded.
    encoding_kind:
        String identifying the serialisation format.
    binary_repr:
        The raw bytes of the encoded artifact.
    schema_version:
        Integer schema version of the encoding format.
    is_lossless:
        True iff the encoding is information-preserving and reversible.
    """

    encoding_id: str
    artifact: FailureArtifact
    encoding_kind: str
    binary_repr: bytes
    schema_version: int
    is_lossless: bool

    def size_bytes(self) -> int:
        """Return the size of the binary representation in bytes."""
        return len(self.binary_repr)

    def checksum(self) -> str:
        """Return the SHA-256 checksum of ``binary_repr``."""
        return hashlib.sha256(self.binary_repr).hexdigest()

    def can_replay(self) -> bool:
        """Return True iff this encoding can be used to replay the failure."""
        return self.is_lossless and self.artifact.is_exact

    def short_repr(self) -> str:
        """Return a compact one-line summary."""
        lossless = "lossless" if self.is_lossless else "lossy"
        return (
            f"<ExactFailureEncoding {self.encoding_id[:8]} "
            f"{self.encoding_kind} v{self.schema_version} "
            f"{self.size_bytes()}B {lossless}>"
        )


@dataclass(frozen=True)
class ArtifactCatalog:
    """Catalog of failure artifacts for a judgment domain.

    An ArtifactCatalog is the primary data structure for managing a collection
    of FailureArtifacts.  It maintains both the ordered tuple of artifacts and
    an index mapping artifact IDs to failure-mode categories for efficient
    lookup.

    In the Čech cohomology picture, the catalog is the chain group
    C¹(𝒰; ℤ) — the direct sum of 1-cochains indexed by artifact ID.  The
    total obstruction class of the catalog is the image of the catalog under
    the cohomology map H¹, which can be computed by ``find_failure_patterns``.

    Fields
    ------
    catalog_id:
        Unique identifier for this catalog.
    artifacts:
        Immutable tuple of FailureArtifacts in registration order.
    index:
        Immutable tuple of (artifact_id, failure_mode.category) pairs for
        fast lookup without iterating all artifacts.
    version:
        Integer version counter, incremented on each merge or update.
    total_count:
        Cached total count of artifacts (equals ``len(artifacts)``).
    """

    catalog_id: str
    artifacts: tuple  # type: ignore[type-arg]   # tuple[FailureArtifact, ...]
    index: tuple      # type: ignore[type-arg]   # tuple[tuple[str, str], ...]
    version: int
    total_count: int

    def __post_init__(self) -> None:
        """Validate that total_count matches len(artifacts)."""
        if self.total_count != len(self.artifacts):
            raise ValueError(
                f"ArtifactCatalog.total_count={self.total_count} does not match "
                f"len(artifacts)={len(self.artifacts)}"
            )

    def by_category(self, category: str) -> tuple:  # type: ignore[type-arg]
        """Return all artifacts whose failure_mode.category matches *category*.

        Parameters
        ----------
        category:
            One of the six canonical FailureMode categories.

        Returns
        -------
        tuple[FailureArtifact, ...]
            Filtered tuple of matching artifacts.
        """
        return tuple(
            a for a in self.artifacts
            if a.failure_mode.category == category
        )

    def by_severity(self, severity: str) -> tuple:  # type: ignore[type-arg]
        """Return all artifacts whose failure_mode.severity matches *severity*."""
        return tuple(
            a for a in self.artifacts
            if a.failure_mode.severity == severity
        )

    def exact_artifacts(self) -> tuple:  # type: ignore[type-arg]
        """Return only the artifacts where ``is_exact=True``."""
        return tuple(a for a in self.artifacts if a.is_exact)

    def non_trivial_obstruction_artifacts(self) -> tuple:  # type: ignore[type-arg]
        """Return artifacts with a non-trivial Čech H¹ obstruction class."""
        return tuple(
            a for a in self.artifacts
            if a.is_cohomologically_non_trivial()
        )

    def short_repr(self) -> str:
        """Return a compact one-line summary."""
        return (
            f"<ArtifactCatalog {self.catalog_id[:8]} "
            f"n={self.total_count} v{self.version}>"
        )


@dataclass(frozen=True)
class FailurePattern:
    """A pattern matching multiple failure artifacts.

    A FailurePattern is the Judgment Geometry counterpart of a non-trivial
    Čech H¹ cohomology class: it records that a family of failure artifacts
    share a common structural template, and that this pattern recurs across
    multiple judgments.

    In the cochain picture:
    - Each artifact in ``matched_artifact_ids`` is a 1-cochain δᵢ.
    - The pattern template encodes the "shape" of ∑δᵢ in H¹.
    - ``frequency`` = |matched_artifact_ids| is the multiplicity of the class.

    A pattern with ``frequency >= 2`` signals a systematic failure that will
    not be resolved by fixing a single judgment in isolation — it requires a
    global repair at the level of the axiom set A or the trust schema T.

    Fields
    ------
    pattern_id:
        Unique identifier for this pattern.
    template:
        A string template describing the common structure of matched failures.
        May contain placeholders such as ``{formula}``, ``{trust_tier}``.
    matched_artifact_ids:
        Frozenset of artifact IDs that match this pattern.
    frequency:
        How many artifacts match (equals ``len(matched_artifact_ids)``).
    first_seen:
        UNIX timestamp of the earliest artifact matching this pattern.
    """

    pattern_id: str
    template: str
    matched_artifact_ids: frozenset  # type: ignore[type-arg]   # frozenset[str]
    frequency: int
    first_seen: float

    def __post_init__(self) -> None:
        """Validate frequency matches matched_artifact_ids size."""
        if self.frequency != len(self.matched_artifact_ids):
            raise ValueError(
                f"FailurePattern.frequency={self.frequency} does not match "
                f"len(matched_artifact_ids)={len(self.matched_artifact_ids)}"
            )

    @property
    def is_systematic(self) -> bool:
        """True if this pattern recurs across 2+ independent artifacts."""
        return self.frequency >= 2

    @property
    def is_non_trivial_cohomology_class(self) -> bool:
        """True if this pattern represents a non-trivial element of H¹.

        By convention, a pattern is a non-trivial cohomology class iff it
        is systematic (frequency ≥ 2) and its template is not the trivial
        template ``"trivial"``.
        """
        return self.is_systematic and self.template != "trivial"

    def short_repr(self) -> str:
        """Return a compact one-line summary."""
        systematic = "systematic" if self.is_systematic else "isolated"
        return (
            f"<FailurePattern {self.pattern_id[:8]} "
            f"freq={self.frequency} {systematic}>"
        )


@dataclass(frozen=True)
class FailureRepairRecord:
    """Record of a repair attempt for a failure artifact.

    In Judgment Geometry, a repair is an attempt to lift the judgment J from
    its failed state to a valid state by modifying one or more of its
    components — typically the axiom set A, the trust tier T, or the proof
    certificate Π.

    The ``before_trust`` and ``after_trust`` fields record the TrustTier T
    before and after the repair attempt, allowing the trust delta to be
    tracked.  A successful repair should raise T; an unsuccessful repair
    leaves T unchanged or lowers it.

    The ``steps`` field records the ordered sequence of repair operations
    applied, e.g. ``("weaken_precondition", "re_verify", "promote_trust")``.

    Fields
    ------
    repair_id:
        Unique identifier for this repair record.
    artifact_id:
        The ID of the FailureArtifact being repaired.
    repair_kind:
        String classifying the repair strategy, e.g. ``"axiom_weakening"``,
        ``"trust_promotion"``, ``"proof_re_synthesis"``.
    before_trust:
        The TrustTier integer before the repair.
    after_trust:
        The TrustTier integer after the repair (if ``success=True``) or the
        same as ``before_trust`` (if ``success=False``).
    success:
        True iff the repair raised the trust to an acceptable level.
    steps:
        Ordered tuple of string descriptions of repair steps applied.
    """

    repair_id: str
    artifact_id: str
    repair_kind: str
    before_trust: int
    after_trust: int
    success: bool
    steps: tuple  # type: ignore[type-arg]   # tuple[str, ...]

    @property
    def trust_delta(self) -> int:
        """Return the signed change in trust tier caused by this repair."""
        return self.after_trust - self.before_trust

    @property
    def is_beneficial(self) -> bool:
        """True if the repair raised (or maintained) the trust tier."""
        return self.after_trust >= self.before_trust

    def short_repr(self) -> str:
        """Return a compact one-line summary."""
        outcome = "OK" if self.success else "FAIL"
        return (
            f"<FailureRepairRecord {self.repair_id[:8]} "
            f"{self.repair_kind} Δtrust={self.trust_delta:+d} {outcome}>"
        )


# ---------------------------------------------------------------------------
# Module-level functions: Judgment Geometry failure artifact operations
# ---------------------------------------------------------------------------


def encode_failure(
    judgment: tuple,  # type: ignore[type-arg]
    failure_mode: FailureMode,
    witness: FailureWitness,
    *,
    encoding_kind: str = "json_utf8",
    schema_version: int = 1,
) -> ExactFailureEncoding:
    """Create an ExactFailureEncoding from a failed Judgment Geometry judgment.

    This is the primary entry point for recording a judgment failure.  Given
    the 8-tuple J = (c, φ, A, E, O, B, T, Π), a FailureMode classifying the
    failure, and a FailureWitness providing evidence, this function:

    1. Constructs a FailureArtifact capturing the full failure state.
    2. Serialises the artifact into a binary representation using the chosen
       encoding format.
    3. Wraps the result in an ExactFailureEncoding.

    The encoding is considered lossless (``is_lossless=True``) iff the witness
    is constructive (``witness.is_constructive=True``), the failure mode is
    not a schema error (schema errors may lose structural information during
    serialisation), and the encoding kind is one of the lossless formats.

    In the Čech cohomology picture, calling this function corresponds to
    recording a 1-cochain δ ∈ C¹(𝒰; ℤ) for the failure event.  The
    ``obstruction_class`` field of the resulting artifact is derived from
    ``failure_mode.category`` and the witness's ``cochain_key()``.

    Parameters
    ----------
    judgment:
        The full judgment 8-tuple (c, φ, A, E, O, B, T, Π) at failure time.
    failure_mode:
        The FailureMode classifying the failure category and severity.
    witness:
        The FailureWitness providing evidence for the failure.
    encoding_kind:
        Serialisation format; default ``"json_utf8"``.
    schema_version:
        Schema version for the encoding; default 1.

    Returns
    -------
    ExactFailureEncoding
        An immutable, serialisable encoding of the failure event.

    Examples
    --------
    >>> mode = FailureMode("m1", "trust_too_low", "TRUST_VIOLATION", "ERROR", True)
    >>> wit = FailureWitness("w1", "(> x 0)", "{x: int}", "[x=-1]", 0, True)
    >>> enc = encode_failure((None,) * 8, mode, wit)
    >>> enc.can_replay()
    True
    """
    artifact_id = str(uuid.uuid4())
    encoding_id = str(uuid.uuid4())
    now = time.time()

    # Derive the obstruction class string from the failure mode and witness.
    # Non-trivial classes encode category and cochain key; trivial class is "0".
    if failure_mode.category == "OBSTRUCTION":
        obstruction_class = f"H1[{failure_mode.mode_id[:8]}:{witness.cochain_key()[:16]}]"
    elif failure_mode.category in ("TRUST_VIOLATION", "PROOF_FAILURE"):
        # Trust violations and proof failures may contribute a boundary cochain
        # (exact but cobounding); represented as a bracketed tier label.
        tier_label = TrustTier.label(witness.trust_at_witness)
        obstruction_class = f"B1[{tier_label}:{witness.cochain_key()[:8]}]"
    else:
        # Schema errors, witness invalids, and coverage gaps are trivial in H¹
        # — they do not represent persistent cohomological obstructions.
        obstruction_class = "0"

    is_exact = witness.is_constructive and bool(witness.distinguishing_input)

    artifact = FailureArtifact(
        artifact_id=artifact_id,
        failure_mode=failure_mode,
        judgment_at_failure=judgment,
        witness=witness,
        trust_at_failure=witness.trust_at_witness,
        obstruction_class=obstruction_class,
        timestamp=now,
        is_exact=is_exact,
    )

    # Serialise the artifact to bytes using the chosen encoding_kind.
    # For "json_utf8" we produce a JSON object with the key fields.
    # For other kinds we fall back to a minimal JSON representation.
    try:
        payload: dict[str, Any] = {
            "schema_version": schema_version,
            "artifact_id": artifact.artifact_id,
            "failure_mode": {
                "mode_id": failure_mode.mode_id,
                "name": failure_mode.name,
                "category": failure_mode.category,
                "severity": failure_mode.severity,
                "is_recoverable": failure_mode.is_recoverable,
            },
            "witness": {
                "witness_id": witness.witness_id,
                "formula_failed": witness.formula_failed,
                "context": witness.context,
                "distinguishing_input": witness.distinguishing_input,
                "trust_at_witness": witness.trust_at_witness,
                "is_constructive": witness.is_constructive,
            },
            "trust_at_failure": artifact.trust_at_failure,
            "obstruction_class": artifact.obstruction_class,
            "timestamp": artifact.timestamp,
            "is_exact": artifact.is_exact,
        }
        binary_repr = json.dumps(payload, sort_keys=True).encode("utf-8")
    except (TypeError, ValueError) as exc:
        # Fallback: minimal encoding that may be lossy.
        fallback = {"artifact_id": artifact_id, "error": str(exc)}
        binary_repr = json.dumps(fallback).encode("utf-8")
        encoding_kind = "json_utf8_fallback"

    # Determine losslessness: lossless iff constructive witness + non-error encoding.
    _LOSSLESS_KINDS = frozenset({"json_utf8", "msgpack", "custom_v1"})
    is_lossless = (
        witness.is_constructive
        and failure_mode.category != "SCHEMA_ERROR"
        and encoding_kind in _LOSSLESS_KINDS
    )

    return ExactFailureEncoding(
        encoding_id=encoding_id,
        artifact=artifact,
        encoding_kind=encoding_kind,
        binary_repr=binary_repr,
        schema_version=schema_version,
        is_lossless=is_lossless,
    )


def build_artifact_catalog(
    artifacts: list,  # type: ignore[type-arg]   # list[FailureArtifact]
    *,
    catalog_id: str | None = None,
    version: int = 1,
) -> ArtifactCatalog:
    """Build an ArtifactCatalog from a list of FailureArtifacts.

    This function constructs an immutable ArtifactCatalog from a mutable
    list of FailureArtifacts.  It:

    1. Deduplicates artifacts by ``artifact_id`` (last-write-wins for
       duplicates, preserving the order of first occurrence).
    2. Builds the ``index`` as an ordered tuple of (artifact_id, category)
       pairs for fast lookups by downstream functions.
    3. Validates that all artifacts have consistent structural invariants.

    The resulting catalog is frozen and may be safely shared across threads
    and processes.

    In the Čech cohomology picture, building the catalog corresponds to
    constructing the chain group C¹(𝒰; ℤ) from the list of 1-cochains.

    Parameters
    ----------
    artifacts:
        Mutable list of FailureArtifacts to catalogue.  May contain
        duplicates (by artifact_id); duplicates are silently deduplicated.
    catalog_id:
        Optional explicit catalog ID; if None a new UUID is generated.
    version:
        Integer version number for the catalog; default 1.

    Returns
    -------
    ArtifactCatalog
        An immutable catalog with deduplicated artifacts and a built index.

    Examples
    --------
    >>> cat = build_artifact_catalog([artifact1, artifact2])
    >>> cat.total_count
    2
    """
    if catalog_id is None:
        catalog_id = str(uuid.uuid4())

    # Deduplicate by artifact_id, preserving insertion order.
    seen_ids: set[str] = set()
    deduped: list = []
    for a in artifacts:
        if a.artifact_id not in seen_ids:
            seen_ids.add(a.artifact_id)
            deduped.append(a)
        else:
            logger.debug(
                "build_artifact_catalog: duplicate artifact_id=%s skipped",
                a.artifact_id,
            )

    # Validate structural invariants for each artifact.
    for a in deduped:
        if not isinstance(a.failure_mode, FailureMode):
            raise TypeError(
                f"artifact {a.artifact_id!r}: failure_mode must be a FailureMode, "
                f"got {type(a.failure_mode).__name__}"
            )
        if not isinstance(a.witness, FailureWitness):
            raise TypeError(
                f"artifact {a.artifact_id!r}: witness must be a FailureWitness, "
                f"got {type(a.witness).__name__}"
            )
        if not isinstance(a.judgment_at_failure, tuple):
            raise TypeError(
                f"artifact {a.artifact_id!r}: judgment_at_failure must be a tuple, "
                f"got {type(a.judgment_at_failure).__name__}"
            )

    # Build the index as a tuple of (artifact_id, category) pairs.
    index = tuple((a.artifact_id, a.failure_mode.category) for a in deduped)

    catalog = ArtifactCatalog(
        catalog_id=catalog_id,
        artifacts=tuple(deduped),
        index=index,
        version=version,
        total_count=len(deduped),
    )

    logger.debug(
        "build_artifact_catalog: catalog_id=%s n=%d",
        catalog_id,
        catalog.total_count,
    )
    return catalog


def classify_failure_mode(
    artifact: FailureArtifact,
) -> FailureMode:
    """Classify a FailureArtifact into a canonical FailureMode.

    This function re-derives the FailureMode for an artifact by inspecting
    its structural properties.  It is useful when an artifact was constructed
    with a preliminary or approximate mode and needs to be re-classified after
    more information becomes available (e.g. after a constructive witness is
    found for a previously non-constructive artifact).

    Classification rules (in priority order):
    1. If the obstruction class is non-trivial in Čech H¹ → ``OBSTRUCTION``.
    2. If the witness is non-constructive and trust < TRUSTED → ``TRUST_VIOLATION``.
    3. If the judgment_at_failure tuple is malformed (length ≠ 8) → ``SCHEMA_ERROR``.
    4. If the witness formula_failed is empty → ``PROOF_FAILURE``.
    5. If the witness is not constructive → ``WITNESS_INVALID``.
    6. If the obstruction class is trivial and trust < PROVISIONAL → ``COVERAGE_GAP``.
    7. Default fallback: re-use the existing failure_mode category.

    In all cases, the severity is re-derived from the combination of category
    and trust tier: FATAL if trust=UNTRUSTED or category=OBSTRUCTION with
    non-trivial class; ERROR for most failures; WARNING for recoverable ones.

    Parameters
    ----------
    artifact:
        The FailureArtifact to re-classify.

    Returns
    -------
    FailureMode
        A newly constructed FailureMode (with a new mode_id) reflecting the
        re-classification.

    Examples
    --------
    >>> mode = classify_failure_mode(artifact)
    >>> mode.category in FailureMode._VALID_CATEGORIES
    True
    """
    # Determine category by inspecting the artifact's structural properties.
    category: str
    is_recoverable: bool
    severity: str

    judgment = artifact.judgment_at_failure

    if artifact.is_cohomologically_non_trivial():
        # Non-trivial H¹ class → OBSTRUCTION (structural, potentially permanent).
        category = "OBSTRUCTION"
        is_recoverable = False
    elif (
        not artifact.witness.is_constructive
        and artifact.trust_at_failure < TrustTier.TRUSTED
    ):
        # Non-constructive witness combined with low trust → TRUST_VIOLATION.
        category = "TRUST_VIOLATION"
        is_recoverable = True
    elif not isinstance(judgment, tuple) or len(judgment) != 8:
        # Judgment tuple is not the expected 8-tuple → SCHEMA_ERROR.
        category = "SCHEMA_ERROR"
        is_recoverable = False
    elif not artifact.witness.formula_failed:
        # No formula recorded at failure → PROOF_FAILURE.
        category = "PROOF_FAILURE"
        is_recoverable = artifact.failure_mode.is_recoverable
    elif not artifact.witness.is_constructive:
        # Witness exists but is not constructive → WITNESS_INVALID.
        category = "WITNESS_INVALID"
        is_recoverable = True
    elif artifact.trust_at_failure < TrustTier.PROVISIONAL:
        # Trivial obstruction class but very low trust → COVERAGE_GAP.
        category = "COVERAGE_GAP"
        is_recoverable = True
    else:
        # Fall back to the existing category for well-formed artifacts.
        category = artifact.failure_mode.category
        is_recoverable = artifact.failure_mode.is_recoverable

    # Derive severity from category and trust.
    if category == "OBSTRUCTION" and artifact.is_cohomologically_non_trivial():
        severity = "FATAL"
    elif category == "SCHEMA_ERROR":
        severity = "FATAL"
    elif artifact.trust_at_failure == TrustTier.UNTRUSTED:
        severity = "ERROR"
    elif is_recoverable:
        severity = "WARNING"
    else:
        severity = "ERROR"

    mode_id = str(uuid.uuid4())
    name = f"reclassified_{category.lower()}_{mode_id[:8]}"

    return FailureMode(
        mode_id=mode_id,
        name=name,
        category=category,
        severity=severity,
        is_recoverable=is_recoverable,
    )


def extract_failure_witness(
    artifact: FailureArtifact,
) -> FailureWitness:
    """Extract the FailureWitness from a FailureArtifact.

    In Judgment Geometry, the witness encodes the 1-cochain data for the
    failure.  Extracting the witness means isolating the cochain from the
    full artifact record so it can be:
    - Used as input to repair strategies.
    - Stored in the Čech complex independently of the artifact.
    - Compared with other witnesses to identify pattern matches.

    This function returns the witness as-is if it is constructive and
    non-empty.  If the witness is non-constructive, it synthesises a
    minimal witness from the available artifact data (formula, context,
    trust tier) to ensure a witness is always returned.

    The synthesised witness has ``is_constructive=False`` and an empty
    ``distinguishing_input``, which is reflected in the returned object's
    ``is_exact()`` method returning False.

    Parameters
    ----------
    artifact:
        The FailureArtifact from which to extract the witness.

    Returns
    -------
    FailureWitness
        The extracted (or synthesised) FailureWitness.

    Examples
    --------
    >>> wit = extract_failure_witness(artifact)
    >>> isinstance(wit, FailureWitness)
    True
    """
    witness = artifact.witness

    # If the witness is already constructive and has a distinguishing input,
    # return it directly — this is the common case for exact artifacts.
    if witness.is_constructive and witness.distinguishing_input:
        logger.debug(
            "extract_failure_witness: returning constructive witness %s",
            witness.witness_id[:8],
        )
        return witness

    # For non-constructive witnesses, synthesise a minimal witness from
    # the artifact's available metadata.  The synthesised witness captures
    # the formula and context but cannot provide a concrete counterexample.
    synthesised_id = str(uuid.uuid4())
    synthesised_context = witness.context or (
        f"judgment_at_failure[len={len(artifact.judgment_at_failure)}]"
        f"|category={artifact.failure_mode.category}"
        f"|obstruction={artifact.obstruction_class or 'trivial'}"
    )

    # Attempt to extract a formula from the judgment 8-tuple (index 1 = φ).
    synthesised_formula = witness.formula_failed
    if not synthesised_formula:
        judgment = artifact.judgment_at_failure
        if isinstance(judgment, tuple) and len(judgment) >= 2 and judgment[1]:
            synthesised_formula = str(judgment[1])
        else:
            synthesised_formula = f"<unknown_formula_artifact={artifact.artifact_id[:8]}>"

    # The synthesised witness is non-constructive but preserves the trust tier.
    synthesised_witness = FailureWitness(
        witness_id=synthesised_id,
        formula_failed=synthesised_formula,
        context=synthesised_context,
        distinguishing_input="",
        trust_at_witness=artifact.trust_at_failure,
        is_constructive=False,
    )

    logger.debug(
        "extract_failure_witness: synthesised non-constructive witness %s "
        "for artifact %s",
        synthesised_id[:8],
        artifact.artifact_id[:8],
    )
    return synthesised_witness


def find_failure_patterns(
    catalog: ArtifactCatalog,
    *,
    min_frequency: int = 2,
) -> list:  # type: ignore[type-arg]   # list[FailurePattern]
    """Find recurrent failure patterns in an ArtifactCatalog.

    This function analyses the catalog to identify groups of artifacts that
    share a common structural template.  In the Čech cohomology picture,
    this corresponds to computing the decomposition of the chain group
    C¹(𝒰; ℤ) into sub-groups indexed by obstruction class and failure category.

    Grouping is done on the key ``(category, obstruction_prefix)`` where
    ``obstruction_prefix`` is the first 16 characters of the obstruction class
    string (or ``"trivial"`` if the class is trivial).  Artifacts with the same
    key are grouped into a pattern.

    Only patterns with ``frequency >= min_frequency`` are returned; isolated
    failures (frequency=1) are excluded from the pattern set by default.

    Parameters
    ----------
    catalog:
        The ArtifactCatalog to analyse.
    min_frequency:
        Minimum number of artifacts required for a pattern to be reported;
        default 2.

    Returns
    -------
    list[FailurePattern]
        List of FailurePatterns in descending order of frequency.

    Examples
    --------
    >>> patterns = find_failure_patterns(catalog)
    >>> all(p.is_systematic for p in patterns)
    True
    """
    # Group artifacts by (category, obstruction_prefix).
    groups: dict[tuple[str, str], list] = defaultdict(list)
    for artifact in catalog.artifacts:
        category = artifact.failure_mode.category
        obs = artifact.obstruction_class
        if obs and obs != "0" and obs != "[0]":
            obs_prefix = obs[:16]
        else:
            obs_prefix = "trivial"
        key = (category, obs_prefix)
        groups[key].append(artifact)

    patterns: list = []
    for (category, obs_prefix), group_artifacts in groups.items():
        if len(group_artifacts) < min_frequency:
            continue

        pattern_id = str(uuid.uuid4())
        matched_ids = frozenset(a.artifact_id for a in group_artifacts)
        first_seen = min(a.timestamp for a in group_artifacts)

        # Build a template string describing the common structure.
        template = (
            f"category={category} "
            f"obstruction_prefix={obs_prefix!r} "
            f"frequency={len(group_artifacts)}"
        )

        pattern = FailurePattern(
            pattern_id=pattern_id,
            template=template,
            matched_artifact_ids=matched_ids,
            frequency=len(group_artifacts),
            first_seen=first_seen,
        )
        patterns.append(pattern)

    # Sort by descending frequency so the most common patterns appear first.
    patterns.sort(key=lambda p: -p.frequency)

    logger.debug(
        "find_failure_patterns: found %d patterns (min_freq=%d) from %d artifacts",
        len(patterns),
        min_frequency,
        catalog.total_count,
    )
    return patterns


def repair_from_artifact(
    artifact: FailureArtifact,
    strategy: str,
    *,
    target_trust: int = TrustTier.TRUSTED,
) -> FailureRepairRecord:
    """Attempt a repair for a failure artifact using the given strategy.

    In Judgment Geometry, a repair is an attempt to lift the failed judgment J
    to a valid state by modifying its components.  The strategy determines which
    component of J is modified:

    - ``"axiom_weakening"``      — weaken the axiom set A to admit the witness.
    - ``"trust_promotion"``      — try to raise T by providing additional proof.
    - ``"proof_re_synthesis"``   — attempt to re-synthesise the proof Π.
    - ``"obstruction_removal"``  — attempt to reduce O to the trivial class.
    - ``"coverage_extension"``   — extend the support B to cover the gap.
    - ``"schema_repair"``        — fix the structural schema of J.

    The repair is simulated deterministically based on the artifact's
    properties.  In a real implementation this would invoke a solver or
    constraint-guided repair oracle.

    The repair is considered successful (``success=True``) if the strategy
    is applicable to the failure mode's category and the artifact is
    recoverable.  The ``after_trust`` is set to ``target_trust`` on success,
    or left at ``before_trust`` on failure.

    Parameters
    ----------
    artifact:
        The FailureArtifact to repair.
    strategy:
        String repair strategy key (see above).
    target_trust:
        Target TrustTier integer to achieve on success; default TRUSTED.

    Returns
    -------
    FailureRepairRecord
        An immutable record of the repair attempt and its outcome.

    Examples
    --------
    >>> rec = repair_from_artifact(artifact, "trust_promotion")
    >>> rec.trust_delta >= 0
    True
    """
    repair_id = str(uuid.uuid4())
    before_trust = artifact.trust_at_failure
    category = artifact.failure_mode.category

    # Strategy applicability map: (strategy) → applicable categories
    _STRATEGY_CATEGORIES: dict[str, frozenset[str]] = {
        "axiom_weakening":     frozenset({"TRUST_VIOLATION", "PROOF_FAILURE", "COVERAGE_GAP"}),
        "trust_promotion":     frozenset({"TRUST_VIOLATION", "WITNESS_INVALID"}),
        "proof_re_synthesis":  frozenset({"PROOF_FAILURE", "TRUST_VIOLATION"}),
        "obstruction_removal": frozenset({"OBSTRUCTION"}),
        "coverage_extension":  frozenset({"COVERAGE_GAP"}),
        "schema_repair":       frozenset({"SCHEMA_ERROR"}),
    }
    applicable_cats = _STRATEGY_CATEGORIES.get(strategy, frozenset())
    is_applicable = category in applicable_cats

    # Build the repair steps log.
    steps: list[str] = []
    steps.append(f"inspect_artifact(id={artifact.artifact_id[:8]})")
    steps.append(f"identify_strategy(strategy={strategy!r})")
    steps.append(f"check_applicability(category={category!r}, applicable={is_applicable})")

    if is_applicable and artifact.failure_mode.is_recoverable:
        steps.append(f"apply_{strategy}(before_trust={TrustTier.label(before_trust)})")
        steps.append(f"verify_repair(target_trust={TrustTier.label(target_trust)})")
        after_trust = TrustTier.meet(target_trust, TrustTier.VERIFIED)
        success = after_trust > before_trust
        if success:
            steps.append(f"promote_trust(new_trust={TrustTier.label(after_trust)})")
        else:
            steps.append("repair_insufficient(trust_unchanged)")
    else:
        # Strategy not applicable or failure is not recoverable.
        after_trust = before_trust
        success = False
        if not is_applicable:
            steps.append(
                f"strategy_inapplicable(strategy={strategy!r}, category={category!r})"
            )
        else:
            steps.append("failure_not_recoverable(strategy_skipped)")

    steps.append(f"record_outcome(success={success})")

    logger.debug(
        "repair_from_artifact: artifact=%s strategy=%s success=%s Δtrust=%+d",
        artifact.artifact_id[:8],
        strategy,
        success,
        after_trust - before_trust,
    )

    return FailureRepairRecord(
        repair_id=repair_id,
        artifact_id=artifact.artifact_id,
        repair_kind=strategy,
        before_trust=before_trust,
        after_trust=after_trust,
        success=success,
        steps=tuple(steps),
    )


def failure_trust_impact(
    artifact: FailureArtifact,
) -> dict[str, Any]:
    """Compute the trust impact of a failure artifact on the judgment domain.

    When a judgment J fails, the failure has a ripple effect on the trust tier
    T of related judgments.  This function quantifies that impact:

    1. ``direct_impact`` — the degradation to T caused by this specific failure:
       ``TrustTier.degraded_by_failure(artifact.trust_at_failure)``.
    2. ``cohomological_impact`` — an additional penalty if the obstruction class
       is non-trivial in Čech H¹ (indicating a systematic failure).
    3. ``propagation_factor`` — a float in [0, 1] estimating how widely the
       failure impact propagates through dependent judgments (heuristic).
    4. ``recommended_tier`` — the tier to which all dependent judgments should
       be demoted until the failure is repaired.
    5. ``is_blocking`` — True if the failure prevents any dependent judgment
       from achieving TRUSTED or higher.

    Parameters
    ----------
    artifact:
        The FailureArtifact whose trust impact is to be computed.

    Returns
    -------
    dict[str, Any]
        A dictionary with keys: ``direct_impact``, ``cohomological_impact``,
        ``total_impact``, ``recommended_tier``, ``propagation_factor``,
        ``is_blocking``, ``severity_label``, ``trust_before_label``,
        ``trust_after_label``.

    Examples
    --------
    >>> impact = failure_trust_impact(artifact)
    >>> impact["is_blocking"] in (True, False)
    True
    """
    before = artifact.trust_at_failure
    direct_impact = before - TrustTier.degraded_by_failure(before)

    # Cohomological impact: non-trivial H¹ class adds an extra penalty of 1.
    cohomological_impact: int
    if artifact.is_cohomologically_non_trivial():
        cohomological_impact = 1
    else:
        cohomological_impact = 0

    total_impact = direct_impact + cohomological_impact
    recommended_tier = max(0, before - total_impact)

    # Propagation factor heuristic: based on severity and recoverability.
    severity_rank = artifact.failure_mode.severity_rank
    if not artifact.failure_mode.is_recoverable:
        propagation_factor = 1.0
    else:
        propagation_factor = severity_rank / 3.0  # normalised to [0, 1]

    # Blocking: if the recommended tier is below TRUSTED, dependent judgments
    # cannot achieve TRUSTED or higher.
    is_blocking = recommended_tier < TrustTier.TRUSTED

    return {
        "direct_impact": direct_impact,
        "cohomological_impact": cohomological_impact,
        "total_impact": total_impact,
        "recommended_tier": recommended_tier,
        "propagation_factor": propagation_factor,
        "is_blocking": is_blocking,
        "severity_label": artifact.failure_mode.severity,
        "trust_before_label": TrustTier.label(before),
        "trust_after_label": TrustTier.label(recommended_tier),
    }


def merge_catalogs(
    c1: ArtifactCatalog,
    c2: ArtifactCatalog,
    *,
    catalog_id: str | None = None,
) -> ArtifactCatalog:
    """Merge two ArtifactCatalogs into a single unified catalog.

    Merging catalogs corresponds to taking the direct sum of the two chain
    groups C¹(𝒰₁; ℤ) and C¹(𝒰₂; ℤ) in the Čech complex, yielding the
    chain group C¹(𝒰₁ ∪ 𝒰₂; ℤ) of the union cover.

    The merge operation:
    1. Takes all artifacts from c1 and c2.
    2. Deduplicates by artifact_id (c1 takes precedence on conflicts).
    3. Constructs a new ArtifactCatalog with a version number equal to
       ``max(c1.version, c2.version) + 1``.

    The merged catalog preserves the relative ordering of c1's artifacts
    before c2's (i.e. c1 is the "base" and c2 provides new artifacts).

    Parameters
    ----------
    c1:
        The base catalog (takes precedence on artifact_id conflicts).
    c2:
        The secondary catalog (artifacts not in c1 are appended).
    catalog_id:
        Optional explicit ID for the merged catalog; if None, a new UUID is
        generated.

    Returns
    -------
    ArtifactCatalog
        The merged, deduplicated catalog.

    Raises
    ------
    TypeError
        If *c1* or *c2* is not an ArtifactCatalog.

    Examples
    --------
    >>> merged = merge_catalogs(cat1, cat2)
    >>> merged.total_count >= max(cat1.total_count, cat2.total_count)
    True
    """
    if not isinstance(c1, ArtifactCatalog):
        raise TypeError(f"c1 must be an ArtifactCatalog, got {type(c1).__name__}")
    if not isinstance(c2, ArtifactCatalog):
        raise TypeError(f"c2 must be an ArtifactCatalog, got {type(c2).__name__}")

    if catalog_id is None:
        catalog_id = str(uuid.uuid4())

    # Build the merged artifact list: c1 first (base), then c2 new entries.
    seen_ids: set[str] = set()
    merged_artifacts: list = []

    for a in c1.artifacts:
        if a.artifact_id not in seen_ids:
            seen_ids.add(a.artifact_id)
            merged_artifacts.append(a)

    c2_new_count = 0
    for a in c2.artifacts:
        if a.artifact_id not in seen_ids:
            seen_ids.add(a.artifact_id)
            merged_artifacts.append(a)
            c2_new_count += 1
        else:
            logger.debug(
                "merge_catalogs: artifact %s from c2 skipped (exists in c1)",
                a.artifact_id[:8],
            )

    new_version = max(c1.version, c2.version) + 1
    index = tuple((a.artifact_id, a.failure_mode.category) for a in merged_artifacts)

    logger.debug(
        "merge_catalogs: merged c1(n=%d) + c2(n=%d) → n=%d (new_from_c2=%d) v=%d",
        c1.total_count,
        c2.total_count,
        len(merged_artifacts),
        c2_new_count,
        new_version,
    )

    return ArtifactCatalog(
        catalog_id=catalog_id,
        artifacts=tuple(merged_artifacts),
        index=index,
        version=new_version,
        total_count=len(merged_artifacts),
    )


def artifact_to_obstruction_class(
    artifact: FailureArtifact,
) -> str:
    """Compute a canonical string representation of the Čech H¹ obstruction class.

    In Judgment Geometry, the obstruction class O ∈ H¹(𝒰; ℤ) for a judgment J
    encodes the cohomological data of the failure.  This function derives a
    canonical string key for the obstruction class from an artifact's metadata,
    suitable for use as a dictionary key or for comparison across artifacts.

    The canonical form is:
    - ``"[0]"`` for the trivial class (artifact has trivial obstruction_class).
    - ``"H1[{category}:{cochain_key_prefix}]"`` for non-trivial classes arising
      from OBSTRUCTION-category failures.
    - ``"B1[{tier}:{cochain_key_prefix}]"`` for boundary cochains (TRUST_VIOLATION
      or PROOF_FAILURE — these are exact coboundaries in C¹ and represent
      classes that vanish in H¹ after repair).
    - ``"Z1[{category}:{cochain_key_prefix}]"`` for other non-trivial classes
      (WITNESS_INVALID, COVERAGE_GAP) that are closed but not exact.

    The prefix length is 16 hex characters (64 bits), which is sufficient for
    collision resistance in practice.

    Parameters
    ----------
    artifact:
        The FailureArtifact for which to compute the obstruction class.

    Returns
    -------
    str
        The canonical Čech H¹ obstruction class string.

    Examples
    --------
    >>> cls = artifact_to_obstruction_class(artifact)
    >>> cls.startswith("[0]") or cls.startswith("H1[") or cls.startswith("B1[") or cls.startswith("Z1[")
    True
    """
    category = artifact.failure_mode.category
    cochain_key = artifact.witness.cochain_key()
    prefix = cochain_key[:16]

    # If the artifact already has a non-trivial obstruction_class string that
    # was set by encode_failure, validate and return it.
    existing = artifact.obstruction_class
    if existing and existing not in ("0", "[0]"):
        # Re-derive to ensure canonical form regardless of how it was set.
        if existing.startswith("H1[") or existing.startswith("B1[") or existing.startswith("Z1["):
            logger.debug(
                "artifact_to_obstruction_class: returning existing canonical class %r",
                existing,
            )
            return existing

    # Compute the canonical class from scratch.
    if not artifact.is_cohomologically_non_trivial() and category not in ("TRUST_VIOLATION", "PROOF_FAILURE"):
        # Trivial class: no obstruction.
        return "[0]"

    if category == "OBSTRUCTION":
        # Direct obstruction: non-trivial H¹ class.
        return f"H1[{category}:{prefix}]"
    elif category in ("TRUST_VIOLATION", "PROOF_FAILURE"):
        # Boundary cochains: exact in C¹, vanish in H¹ after repair.
        tier_label = TrustTier.label(artifact.trust_at_failure)
        return f"B1[{tier_label}:{prefix}]"
    elif category in ("WITNESS_INVALID", "COVERAGE_GAP"):
        # Closed but not exact: non-trivial in H¹.
        return f"Z1[{category}:{prefix}]"
    else:
        # Schema errors and other categories: trivial class.
        return "[0]"


# ============================== smoke test ==============================

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print("=== exact_failure_artifacts smoke test ===\n")

    # --- ExactFailureKind helpers ---
    for kind in ExactFailureKind:
        print(
            f"  {kind.name}: arithmetic={kind.is_arithmetic()} "
            f"type_related={kind.is_type_related()} "
            f"path_related={kind.is_path_related()} "
            f"severity={kind.severity()}"
        )
        print(f"    smt2_template={kind.smt2_trigger_template()!r}")
    print()

    # --- Analyzer ---
    analyzer = ExactFailureArtifactsAnalyzer()

    # Simple extraction
    model1 = "[x = -3, y = 7, flag = False]"
    w1 = analyzer.extract_exact_artifact("(> x 0)", model1)
    print(f"Artifact 1:")
    print(f"  id={w1.witness_id[:8]}")
    print(f"  kind={w1.failure_kind.name}")
    print(f"  severity={w1.failure_kind.severity()}")
    print(f"  is_exact={w1.is_exact}")
    print(f"  reproducibility={w1.reproducibility_score:.3f}")
    print(f"  repair_quality={w1.repair_seed_quality:.3f}")
    print(f"  x={w1.value_for('x')!r}")
    print(f"  y={w1.value_for('y')!r}")
    print(f"  missing={w1.value_for('z')!r}")
    print()

    # Regression test
    print("Regression test:")
    print(analyzer.generate_regression_test(w1))
    print()

    # Blocking clause
    print(f"Blocking clause: {w1.to_smt2_blocking_clause()}")
    print()

    # Arithmetic failure
    w2 = analyzer.extract_exact_artifact("(>= (+ x y) 0)", "[x = -5, y = 2]", path_predicate="(> x -10)")
    print(f"Arithmetic failure kind: {w2.failure_kind.name}")
    print()

    # Postcondition failure
    w3 = analyzer.extract_exact_artifact("(> _return 0)", "[_return = -1, n = 0]")
    print(f"Postcondition failure kind: {w3.failure_kind.name}")
    print()

    # Batch extraction
    pairs = [
        ("(> x 0)", "[x = -1]"),
        ("(< y MAX)", "[y = 100, MAX = 50]"),
        ("(= _return 1)", "[_return = 0]"),
    ]
    batch = analyzer.batch_extract(pairs)
    print(f"Batch extracted {len(batch)} artifacts")
    for w in batch:
        print(f"  {w.witness_id[:8]}: {w.failure_kind.name} repro={w.reproducibility_score:.3f}")
    print()

    # Parse model dump formats
    m1 = analyzer.parse_z3_model_dump("[a = 1, b = -2, c = True]")
    print(f"Bracket format parse: {m1}")
    m2 = analyzer.parse_z3_model_dump("a = 1\nb = -2\nc = True")
    print(f"Multi-line format parse: {m2}")
    print()

    # Numeric extraction
    nums = analyzer._extract_numeric_values({"x": "3", "y": "-2", "z": "1/4", "flag": "True"})
    print(f"Numeric values: {nums}")
    print()

    # Copilot hints
    print(w1.copilot_exact_hint())
    print()
    print(analyzer.copilot_exact_analysis_hint(w1))
    print()

    # --- Coordinator ---
    coord = ExactFailureArtifactsCoordinator()
    c1 = coord.register_exact_failure("(> x 0)", "[x = -1]")
    c2 = coord.register_exact_failure("(< y MAX)", "[y = 200, MAX = 100]", path_pred="(> x 0)")
    c3 = coord.register_exact_failure("(>= (+ a b) 0)", "[a = -5, b = 1]")
    c4 = coord.register_exact_failure("(> _return 0)", "[_return = -3, n = 5]")
    # Duplicate
    c1_dup = coord.register_exact_failure("(> x 0)", "[x = -1]")

    print(f"all_exact_artifacts: {len(coord.all_exact_artifacts())}")
    print(f"all_repair_seeds: {len(coord.all_repair_seeds())}")
    print()

    # Regression suite
    suite = coord.emit_regression_suite(coord.all_exact_artifacts())
    print("Regression suite (first 300 chars):")
    print(suite[:300])
    print("...")
    print()

    # Blocking clauses
    blocking = coord.emit_blocking_clause_set(coord.all_exact_artifacts())
    print("Blocking clause set:")
    print(blocking)
    print()

    # Merge
    merged = c1.merge(c2)
    print(f"Merged witness: assignments={merged.variable_names()}")
    print()

    # Fingerprints
    print(f"c1 fingerprint: {c1.fingerprint()[:16]}")
    print(f"c2 fingerprint: {c2.fingerprint()[:16]}")
    print()

    print(coord.exact_failure_report())
    print(repr(coord))
    print()

    # --- Module-level convenience ---
    w_simple = extract_simple_exact_artifact("(> x 0)", "[x = -7, y = 3]")
    print(f"extract_simple_exact_artifact: kind={w_simple.failure_kind.name} x={w_simple.value_for('x')!r}")
    print()

    print(f"Z3 available: {_Z3_AVAILABLE}")
    print(f"models available: {_MODELS_AVAILABLE}")
    print()

    # ------------------------------------------------------------------ #
    # Judgment Geometry section smoke tests                               #
    # ------------------------------------------------------------------ #
    print("=== Judgment Geometry: failure artifact smoke tests ===\n")

    # --- TrustTier algebra ---
    print("TrustTier algebra:")
    for tier in (TrustTier.UNTRUSTED, TrustTier.PROVISIONAL, TrustTier.TRUSTED, TrustTier.VERIFIED):
        label = TrustTier.label(tier)
        degraded = TrustTier.degraded_by_failure(tier)
        print(f"  {label}({tier}) → degraded={TrustTier.label(degraded)}({degraded})")
    print(f"  meet(TRUSTED, PROVISIONAL) = {TrustTier.label(TrustTier.meet(TrustTier.TRUSTED, TrustTier.PROVISIONAL))}")
    print(f"  join(UNTRUSTED, TRUSTED)   = {TrustTier.label(TrustTier.join(TrustTier.UNTRUSTED, TrustTier.TRUSTED))}")
    print()

    # --- FailureMode construction ---
    print("FailureMode construction:")
    fm_trust = FailureMode(
        mode_id="mode-trust-01",
        name="trust_tier_too_low",
        category="TRUST_VIOLATION",
        severity="ERROR",
        is_recoverable=True,
    )
    fm_obs = FailureMode(
        mode_id="mode-obs-01",
        name="nontrivial_h1_class",
        category="OBSTRUCTION",
        severity="FATAL",
        is_recoverable=False,
    )
    fm_proof = FailureMode(
        mode_id="mode-proof-01",
        name="missing_proof_certificate",
        category="PROOF_FAILURE",
        severity="ERROR",
        is_recoverable=True,
    )
    fm_schema = FailureMode(
        mode_id="mode-schema-01",
        name="malformed_judgment_tuple",
        category="SCHEMA_ERROR",
        severity="FATAL",
        is_recoverable=False,
    )
    for fm in (fm_trust, fm_obs, fm_proof, fm_schema):
        print(f"  {fm.short_repr()} cohomological={fm.is_cohomological}")
    print()

    # Validate category checking
    try:
        FailureMode("bad", "bad", "INVALID_CAT", "ERROR", True)
        print("  ERROR: should have raised ValueError for invalid category")
    except ValueError as exc:
        print(f"  Correctly rejected invalid category: {exc}")
    print()

    # --- FailureWitness construction ---
    print("FailureWitness construction:")
    wit_constructive = FailureWitness(
        witness_id=str(uuid.uuid4()),
        formula_failed="(> x 0)",
        context="{x: int, y: int}",
        distinguishing_input="[x=-1, y=5]",
        trust_at_witness=TrustTier.UNTRUSTED,
        is_constructive=True,
    )
    wit_non_constructive = FailureWitness(
        witness_id=str(uuid.uuid4()),
        formula_failed="(< y MAX)",
        context="{y: int, MAX: int}",
        distinguishing_input="",
        trust_at_witness=TrustTier.PROVISIONAL,
        is_constructive=False,
    )
    for wit in (wit_constructive, wit_non_constructive):
        print(f"  {wit.short_repr()} is_exact={wit.is_exact()}")
        print(f"    cochain_key={wit.cochain_key()[:20]}...")
    print()

    # --- FailureArtifact construction ---
    print("FailureArtifact construction:")
    # The judgment 8-tuple: (c, φ, A, E, O, B, T, Π)
    judgment_tuple = (
        "{x: int}",          # c: context
        "(> x 0)",           # φ: formula
        ("axiom_x_int",),    # A: axioms
        (),                  # E: evidence
        "0",                 # O: obstruction class (trivial initially)
        "domain_x",          # B: basis
        TrustTier.UNTRUSTED, # T: trust tier
        None,                # Π: proof certificate
    )
    art1 = FailureArtifact(
        artifact_id=str(uuid.uuid4()),
        failure_mode=fm_trust,
        judgment_at_failure=judgment_tuple,
        witness=wit_constructive,
        trust_at_failure=TrustTier.UNTRUSTED,
        obstruction_class="0",
        timestamp=time.time(),
        is_exact=True,
    )
    art2 = FailureArtifact(
        artifact_id=str(uuid.uuid4()),
        failure_mode=fm_obs,
        judgment_at_failure=judgment_tuple,
        witness=wit_non_constructive,
        trust_at_failure=TrustTier.PROVISIONAL,
        obstruction_class="H1[OBSTRUCTION:abcdef0123456789]",
        timestamp=time.time() + 0.01,
        is_exact=False,
    )
    art3 = FailureArtifact(
        artifact_id=str(uuid.uuid4()),
        failure_mode=fm_trust,
        judgment_at_failure=judgment_tuple,
        witness=wit_constructive,
        trust_at_failure=TrustTier.UNTRUSTED,
        obstruction_class="0",
        timestamp=time.time() + 0.02,
        is_exact=True,
    )
    for art in (art1, art2, art3):
        print(f"  {art.short_repr()}")
        print(f"    cohomologically_non_trivial={art.is_cohomologically_non_trivial()}")
        print(f"    fingerprint={art.fingerprint()[:20]}...")
    print()

    # --- encode_failure ---
    print("encode_failure:")
    enc1 = encode_failure(judgment_tuple, fm_trust, wit_constructive)
    enc2 = encode_failure(judgment_tuple, fm_obs, wit_non_constructive, encoding_kind="msgpack")
    for enc in (enc1, enc2):
        print(f"  {enc.short_repr()}")
        print(f"    can_replay={enc.can_replay()} checksum={enc.checksum()[:16]}...")
    print()

    # --- build_artifact_catalog ---
    print("build_artifact_catalog:")
    cat_a = build_artifact_catalog([art1, art2, art3, art1])  # art1 is a duplicate
    print(f"  {cat_a.short_repr()}")
    print(f"  total_count={cat_a.total_count}")
    print(f"  TRUST_VIOLATION count={len(cat_a.by_category('TRUST_VIOLATION'))}")
    print(f"  OBSTRUCTION count={len(cat_a.by_category('OBSTRUCTION'))}")
    print(f"  exact_artifacts count={len(cat_a.exact_artifacts())}")
    print(f"  non_trivial_H1 count={len(cat_a.non_trivial_obstruction_artifacts())}")
    print()

    # --- classify_failure_mode ---
    print("classify_failure_mode:")
    reclassified1 = classify_failure_mode(art1)
    reclassified2 = classify_failure_mode(art2)
    print(f"  art1 reclassified → {reclassified1.short_repr()}")
    print(f"  art2 reclassified → {reclassified2.short_repr()}")
    print()

    # --- extract_failure_witness ---
    print("extract_failure_witness:")
    w_extracted1 = extract_failure_witness(art1)
    w_extracted2 = extract_failure_witness(art2)
    print(f"  art1 witness → {w_extracted1.short_repr()} is_exact={w_extracted1.is_exact()}")
    print(f"  art2 witness → {w_extracted2.short_repr()} is_exact={w_extracted2.is_exact()}")
    print()

    # --- find_failure_patterns ---
    print("find_failure_patterns:")
    # Add more trust-violation artifacts to trigger a pattern
    arts_for_pattern = [art1, art2, art3]
    for _ in range(3):
        arts_for_pattern.append(FailureArtifact(
            artifact_id=str(uuid.uuid4()),
            failure_mode=fm_trust,
            judgment_at_failure=judgment_tuple,
            witness=wit_constructive,
            trust_at_failure=TrustTier.UNTRUSTED,
            obstruction_class="0",
            timestamp=time.time(),
            is_exact=True,
        ))
    cat_patterns = build_artifact_catalog(arts_for_pattern)
    patterns = find_failure_patterns(cat_patterns, min_frequency=2)
    print(f"  Found {len(patterns)} pattern(s) with min_frequency=2:")
    for p in patterns:
        print(f"    {p.short_repr()} systematic={p.is_systematic} non_trivial_H1={p.is_non_trivial_cohomology_class}")
        print(f"      template={p.template!r}")
    print()

    # --- repair_from_artifact ---
    print("repair_from_artifact:")
    rep1 = repair_from_artifact(art1, "trust_promotion")
    rep2 = repair_from_artifact(art2, "obstruction_removal")
    rep3 = repair_from_artifact(art1, "coverage_extension")  # inapplicable strategy
    for rep in (rep1, rep2, rep3):
        print(f"  {rep.short_repr()}")
        print(f"    steps={rep.steps}")
    print()

    # --- failure_trust_impact ---
    print("failure_trust_impact:")
    impact1 = failure_trust_impact(art1)
    impact2 = failure_trust_impact(art2)
    for label, impact in (("art1 (TRUST_VIOLATION)", impact1), ("art2 (OBSTRUCTION)", impact2)):
        print(f"  {label}:")
        for k, v in impact.items():
            print(f"    {k}={v!r}")
    print()

    # --- merge_catalogs ---
    print("merge_catalogs:")
    extra_art = FailureArtifact(
        artifact_id=str(uuid.uuid4()),
        failure_mode=fm_proof,
        judgment_at_failure=judgment_tuple,
        witness=wit_constructive,
        trust_at_failure=TrustTier.PROVISIONAL,
        obstruction_class="0",
        timestamp=time.time(),
        is_exact=True,
    )
    cat_b = build_artifact_catalog([extra_art, art2], version=2)
    cat_merged = merge_catalogs(cat_a, cat_b)
    print(f"  cat_a: {cat_a.short_repr()}")
    print(f"  cat_b: {cat_b.short_repr()}")
    print(f"  merged: {cat_merged.short_repr()}")
    print(f"  merged.total_count={cat_merged.total_count} (expected {cat_a.total_count + 1})")
    print()

    # --- artifact_to_obstruction_class ---
    print("artifact_to_obstruction_class:")
    for art, label in ((art1, "art1/TRUST_VIOLATION"), (art2, "art2/OBSTRUCTION"), (art3, "art3/TRUST_VIOLATION")):
        obs_class = artifact_to_obstruction_class(art)
        print(f"  {label} → {obs_class!r}")
    # Test with a schema-error artifact
    art_schema = FailureArtifact(
        artifact_id=str(uuid.uuid4()),
        failure_mode=fm_schema,
        judgment_at_failure=("bad",),  # Malformed: not 8-tuple
        witness=wit_non_constructive,
        trust_at_failure=TrustTier.UNTRUSTED,
        obstruction_class="0",
        timestamp=time.time(),
        is_exact=False,
    )
    print(f"  art_schema/SCHEMA_ERROR → {artifact_to_obstruction_class(art_schema)!r}")
    print()

    print(f"Z3 available: {_Z3_AVAILABLE}")
    print(f"models available: {_MODELS_AVAILABLE}")
    print("\n=== smoke test complete ===")
