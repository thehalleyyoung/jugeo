"""FailureArtifactEncoder — encodes failure conditions as Z3 formulas.

This module provides machinery for representing, encoding, and reasoning about
failure artifacts — conditions under which a program or logical model violates
some expected property.  A *failure artifact* captures the SMT2 trigger formula
that witnesses the failure together with contextual assumptions and metadata
(kind, severity, support region, copilot label).

The central class :class:`FailureArtifactEncoder` converts high-level failure
descriptions into :class:`FailureArtifact` value objects and optionally wraps
them in ready-to-submit SMT2 queries.  Supporting infrastructure includes:

* :class:`FailureKind` — enumeration of the distinct failure categories, each
  carrying default severity, classification helpers, and an SMT2 trigger
  template string.
* :class:`FailureArtifact` — frozen dataclass representing a single, immutable
  failure record with helpers for negation, merge, fingerprinting, and SMT2
  query emission.
* :class:`FailurePreconditionExtractor` — implements lightweight weakest-
  precondition (WP) and strongest-postcondition (SP) calculus rules over
  SMT2 string representations, suitable for driving iterative refinement
  loops inside the copilot encoding pipeline.

Usage example::

    encoder = FailureArtifactEncoder()
    artifact = encoder.encode_failure(
        FailureKind.DIVISION_BY_ZERO,
        trigger="(= denom 0)",
        context=["(> x 0)", "(< y 100)"],
    )
    print(artifact.to_smt2_query())

Copilot integration notes:
    The copilot label on each artifact (``copilot:<kind>``) is consumed by
    downstream repair pipelines to prioritise which failures to attempt to
    patch automatically.  Severity levels follow the 1-5 scale defined in the
    copilot scalar-encoding specification.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any

from jugeo.geometry.supports import SupportRegion, SupportSet
from jugeo.solver.countermodels import (
    Countermodel,
    CountermodelExtractor,
    ObstructionConverter,
    FailureClass,
    RepairType,
)
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
)
from jugeo.encodings.scalar_encodings.models import (
    SortKind,
    FragmentHint,
    RefinementEncoding,
    GuardFormula,
    ArithmeticObligation,
    EncodingContext,
    EncodingResult,
    make_encoding_id,
)

# ============================== Module logger ================================

logger = logging.getLogger(__name__)

# ============================== Failure kinds ================================


class FailureKind(Enum):
    """Enumeration of well-known program-logic failure categories.

    Each member represents a distinct class of runtime or logical failure.
    Instances expose helpers for severity estimation, classification, and
    template-based SMT2 trigger generation.

    Members are ordered roughly by decreasing tractability: precondition and
    postcondition violations are typically the easiest to repair automatically,
    while null-dereferences are the most critical.
    """

    PRECONDITION_VIOLATION = auto()
    """A caller failed to satisfy a function's declared precondition."""

    POSTCONDITION_VIOLATION = auto()
    """A function failed to establish its declared postcondition."""

    ASSERTION_FAILURE = auto()
    """An inline assertion (assert statement) evaluated to false."""

    DIVISION_BY_ZERO = auto()
    """A division or modulo operation with a zero denominator."""

    INDEX_OUT_OF_BOUNDS = auto()
    """An array or sequence access with an out-of-range index."""

    NULL_DEREF = auto()
    """A dereference of a null or uninitialised pointer."""

    TYPE_MISMATCH = auto()
    """A value of unexpected sort or type was supplied to an operation."""

    ARITHMETIC_OVERFLOW = auto()
    """An arithmetic expression exceeded representable numeric bounds."""

    # ------------------------------------------------------------------
    # Classification helpers
    # ------------------------------------------------------------------

    def severity_default(self) -> int:
        """Return the default severity on a 1-5 scale for this failure kind.

        The scale is:
            1 – informational
            2 – low
            3 – medium
            4 – high
            5 – critical

        Returns:
            int: Default severity level.
        """
        _map: dict[FailureKind, int] = {
            FailureKind.PRECONDITION_VIOLATION: 3,
            FailureKind.POSTCONDITION_VIOLATION: 3,
            FailureKind.ASSERTION_FAILURE: 3,
            FailureKind.DIVISION_BY_ZERO: 4,
            FailureKind.INDEX_OUT_OF_BOUNDS: 4,
            FailureKind.NULL_DEREF: 5,
            FailureKind.TYPE_MISMATCH: 2,
            FailureKind.ARITHMETIC_OVERFLOW: 4,
        }
        return _map[self]

    def is_arithmetic(self) -> bool:
        """Return True iff this failure kind is arithmetic in nature.

        Arithmetic failures are those caused by numeric operations going out of
        range or hitting undefined numeric behaviour.

        Returns:
            bool: True for DIVISION_BY_ZERO and ARITHMETIC_OVERFLOW.
        """
        return self in (FailureKind.DIVISION_BY_ZERO, FailureKind.ARITHMETIC_OVERFLOW)

    def is_memory_safety(self) -> bool:
        """Return True iff this failure kind represents a memory-safety violation.

        Memory-safety failures involve illegal accesses to program memory and
        are typically harder to repair automatically than logical failures.

        Returns:
            bool: True for NULL_DEREF and INDEX_OUT_OF_BOUNDS.
        """
        return self in (FailureKind.NULL_DEREF, FailureKind.INDEX_OUT_OF_BOUNDS)

    def smt_trigger_template(self) -> str:
        """Return a parameterised SMT2 formula template for this failure kind.

        The returned string contains ``{placeholder}`` slots that the caller
        should fill in with concrete subterms before using the formula in a
        query.  Templates are written in SMT-LIB2 syntax.

        Returns:
            str: SMT2 formula template with named placeholders.

        Examples:
            >>> FailureKind.DIVISION_BY_ZERO.smt_trigger_template()
            '(= {divisor} 0)'
            >>> FailureKind.NULL_DEREF.smt_trigger_template()
            '(= {ptr} null)'
        """
        _templates: dict[FailureKind, str] = {
            FailureKind.DIVISION_BY_ZERO: "(= {divisor} 0)",
            FailureKind.INDEX_OUT_OF_BOUNDS: "(or (< {index} 0) (>= {index} {length}))",
            FailureKind.NULL_DEREF: "(= {ptr} null)",
            FailureKind.ARITHMETIC_OVERFLOW: (
                "(or (> {expr} {max_val}) (< {expr} {min_val}))"
            ),
            FailureKind.PRECONDITION_VIOLATION: "(not {condition})",
            FailureKind.POSTCONDITION_VIOLATION: "(not {condition})",
            FailureKind.ASSERTION_FAILURE: "(not {condition})",
            FailureKind.TYPE_MISMATCH: "(not {condition})",
        }
        return _templates[self]


# ============================ Value objects ==================================


@dataclass(frozen=True)
class FailureArtifact:
    """Immutable record representing a single encoded failure condition.

    A ``FailureArtifact`` captures everything needed to:
    * understand *what* failed and *why* (kind, trigger_smt, context_assumptions)
    * assess impact (severity, support region)
    * integrate with copilot tooling (copilot_label)
    * submit a reachability check to Z3 (to_smt2_query)

    Because this is a frozen dataclass, all mutations produce new instances.
    Use :meth:`merge` to combine two compatible artifacts.

    Attributes:
        artifact_id: Unique identifier for this artifact instance.
        kind: The :class:`FailureKind` this artifact represents.
        trigger_smt: SMT2 formula whose satisfiability witnesses the failure.
        context_assumptions: Additional assumptions in scope when checking
            reachability.  Stored as a tuple to preserve immutability.
        support: Geometric support region associated with this failure.
        severity: Numeric severity on the 1-5 scale.
        copilot_label: Opaque label string consumed by the copilot repair
            pipeline.
    """

    artifact_id: str
    kind: FailureKind
    trigger_smt: str
    context_assumptions: tuple[str, ...]
    support: SupportRegion
    severity: int
    copilot_label: str

    # ------------------------------------------------------------------
    # Query generation
    # ------------------------------------------------------------------

    def negation_formula(self) -> str:
        """Return the SMT2 formula that *prevents* this failure from occurring.

        The negation formula is the logical dual of the trigger: it asserts that
        the condition that would cause the failure does *not* hold.  This is the
        formula injected as an additional obligation during repair.

        Returns:
            str: SMT2 negation of the trigger formula.
        """
        return f"(not {self.trigger_smt})"

    def is_avoidable(self) -> bool:
        """Heuristic estimate of whether this failure is statically avoidable.

        A failure is considered avoidable when automated repair strategies have
        a reasonable chance of eliminating it.  Memory-safety violations (null
        dereferences, index out-of-bounds) and critical-severity failures are
        deemed unavoidable by this heuristic because they typically require
        non-trivial invariant inference.

        Returns:
            bool: True when the failure is likely avoidable through static
                analysis or automated repair.
        """
        if self.kind.is_memory_safety():
            return False
        if self.severity >= 5:
            return False
        return True

    def to_smt2_query(self) -> str:
        """Emit a complete SMT-LIB2 query checking reachability of this failure.

        The query asserts all context assumptions and then the trigger formula,
        then issues a ``(check-sat)`` command.  A satisfiable result means the
        failure is reachable under the given assumptions.

        Returns:
            str: Multi-line SMT2 string ready to be passed to a Z3 session.
        """
        lines: list[str] = [
            f"; Failure artifact: {self.artifact_id}",
            f"; Kind: {self.kind.name}",
            f"; Severity: {self.severity}",
        ]
        for assumption in self.context_assumptions:
            lines.append(f"(assert {assumption})")
        lines.append(f"(assert {self.trigger_smt})")
        lines.append("(check-sat)")
        return "\n".join(lines)

    def severity_label(self) -> str:
        """Translate numeric severity to a human-readable label.

        The mapping is:
            5   → 'critical'
            4   → 'high'
            3   → 'medium'
            <3  → 'low'

        Returns:
            str: One of 'critical', 'high', 'medium', or 'low'.
        """
        if self.severity >= 5:
            return "critical"
        if self.severity >= 4:
            return "high"
        if self.severity >= 3:
            return "medium"
        return "low"

    def merge(self, other: FailureArtifact) -> FailureArtifact:
        """Merge two failure artifacts of the same kind into one.

        The merged artifact has:
        * a fresh artifact_id derived from both parents
        * the combined, deduplicated context_assumptions from both
        * a conjunction of both trigger formulas as the new trigger
        * the maximum severity of the two
        * the copilot_label and support of *self* (the receiver)

        Args:
            other: Another :class:`FailureArtifact` to merge with.  Must have
                the same :attr:`kind` as this artifact.

        Returns:
            FailureArtifact: New merged artifact.

        Raises:
            ValueError: If ``other.kind`` differs from ``self.kind``.
        """
        if other.kind != self.kind:
            raise ValueError(
                f"Cannot merge artifacts of different kinds: "
                f"{self.kind.name} vs {other.kind.name}"
            )
        seen: set[str] = set()
        combined_assumptions: list[str] = []
        for asm in (*self.context_assumptions, *other.context_assumptions):
            if asm not in seen:
                seen.add(asm)
                combined_assumptions.append(asm)

        merged_trigger = f"(and {self.trigger_smt} {other.trigger_smt})"
        merged_severity = max(self.severity, other.severity)
        merged_id = f"fail_merge_{hashlib.md5((self.artifact_id + other.artifact_id).encode()).hexdigest()[:8]}"

        return FailureArtifact(
            artifact_id=merged_id,
            kind=self.kind,
            trigger_smt=merged_trigger,
            context_assumptions=tuple(combined_assumptions),
            support=self.support,
            severity=merged_severity,
            copilot_label=self.copilot_label,
        )

    def fingerprint(self) -> str:
        """Compute a short MD5 fingerprint for content-based deduplication.

        The fingerprint is derived from the artifact_id, kind name, and trigger
        formula.  It can be used as a cache key or for equality checks when the
        full object comparison is not desired.

        Returns:
            str: 32-character hex MD5 digest.
        """
        raw = self.artifact_id + self.kind.name + self.trigger_smt
        return hashlib.md5(raw.encode()).hexdigest()

    def assumption_count(self) -> int:
        """Return the number of context assumptions attached to this artifact.

        Returns:
            int: Length of :attr:`context_assumptions`.
        """
        return len(self.context_assumptions)


# =================== Weakest-precondition extractor =========================


class FailurePreconditionExtractor:
    """Computes weakest preconditions and strongest postconditions for failure avoidance.

    This class implements a lightweight symbolic calculus over SMT2 string
    representations.  Rather than operating on parsed ASTs, it works with
    structured string patterns that mirror the SMT-LIB2 s-expression grammar.
    This makes it fast and dependency-free while still providing useful
    approximations for the copilot repair pipeline.

    The WP rules implemented are:
    * Assignment: ``WP((assign x e), Q) = Q[x := e]``
    * Sequencing: ``WP((seq S1 S2), Q) = WP(S1, WP(S2, Q))``
    * Conditional: ``WP((if b S1 S2), Q) = (and (=> b WP(S1,Q)) (=> (not b) WP(S2,Q)))``
    * Default:     ``WP(S, Q) = (=> S Q)``

    Attributes:
        _computation_log: Running log of WP/SP computations performed.
    """

    def __init__(self) -> None:
        """Initialise the extractor with an empty computation log."""
        self._computation_log: list[str] = []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def extract_wp(self, failure: FailureArtifact, body_smt: str) -> str:
        """Compute the weakest precondition guaranteeing failure avoidance.

        Returns the weakest formula over pre-state variables that, when it
        holds before executing ``body_smt``, ensures the failure captured in
        ``failure`` cannot be triggered.

        The computation uses the implication form:
            ``(=> body_smt failure.negation_formula())``

        This is a sound approximation: if the body leads to the failure-
        negation being true, the precondition is satisfied.

        Args:
            failure: The :class:`FailureArtifact` to avoid.
            body_smt: SMT2 representation of the program body.

        Returns:
            str: SMT2 formula for the weakest precondition.
        """
        negation = failure.negation_formula()
        wp = f"(=> {body_smt} {negation})"
        entry = f"extract_wp({failure.artifact_id}, body={body_smt[:40]!r}) -> {wp[:60]!r}"
        self._computation_log.append(entry)
        logger.debug("WP extraction: %s", entry)
        return wp

    def extract_sp(self, precondition: str, body_smt: str) -> str:
        """Compute the strongest postcondition given a precondition and body.

        Returns the strongest formula over post-state variables that is
        guaranteed to hold after executing ``body_smt`` when ``precondition``
        holds in the pre-state.

        The approximation used is conjunction:
            ``(and precondition body_smt)``

        This is exact for deterministic, side-effect-free bodies and a sound
        over-approximation otherwise.

        Args:
            precondition: SMT2 formula holding in the pre-state.
            body_smt: SMT2 representation of the program body.

        Returns:
            str: SMT2 formula for the strongest postcondition.
        """
        sp = f"(and {precondition} {body_smt})"
        self._computation_log.append(f"extract_sp -> {sp[:60]!r}")
        return sp

    def weakest_precondition(self, stmt: str, post: str) -> str:
        """General weakest-precondition computation over structured statements.

        Recognises the following SMT2-flavoured statement forms:
        * ``(assign x e)``  — single-variable assignment
        * ``(seq S1 S2)``   — sequential composition (right-to-left)
        * ``(if b S1 S2)``  — conditional branching
        * Anything else    — treated as an opaque guard via implication

        The assignment rule performs a textual substitution of variable ``x``
        with expression ``e`` inside ``post``.  This is sound only when ``x``
        is a simple identifier (no capture), which is the common case in the
        scalar encoding pipeline.

        Args:
            stmt: Structured SMT2 statement string.
            post: Postcondition formula whose WP is sought.

        Returns:
            str: SMT2 weakest-precondition formula.
        """
        stmt_stripped = stmt.strip()

        if stmt_stripped.startswith("(assign "):
            # Parse "(assign x e)" — extract x and e
            inner = stmt_stripped[len("(assign "):-1].strip()
            space_idx = inner.index(" ")
            var = inner[:space_idx].strip()
            expr = inner[space_idx:].strip()
            # Substitute var -> expr in post (textual, left-to-right)
            substituted = self._substitute(post, var, expr)
            result = substituted
            self._computation_log.append(
                f"WP(assign {var}:={expr}, {post[:30]!r}) = {result[:60]!r}"
            )
            return result

        if stmt_stripped.startswith("(seq "):
            # Parse "(seq S1 S2)" — two sub-statements
            inner = stmt_stripped[len("(seq "):-1].strip()
            s1, s2 = self._split_two_sexprs(inner)
            wp_s2 = self.weakest_precondition(s2, post)
            result = self.weakest_precondition(s1, wp_s2)
            self._computation_log.append(
                f"WP(seq, post={post[:20]!r}) = {result[:60]!r}"
            )
            return result

        if stmt_stripped.startswith("(if "):
            # Parse "(if b S1 S2)"
            inner = stmt_stripped[len("(if "):-1].strip()
            guard, s1, s2 = self._split_three_sexprs(inner)
            wp_s1 = self.weakest_precondition(s1, post)
            wp_s2 = self.weakest_precondition(s2, post)
            result = f"(and (=> {guard} {wp_s1}) (=> (not {guard}) {wp_s2}))"
            self._computation_log.append(
                f"WP(if {guard[:20]!r}, post={post[:20]!r}) = {result[:60]!r}"
            )
            return result

        # Default: treat stmt as guard and post as consequence
        result = f"(=> {stmt_stripped} {post})"
        self._computation_log.append(f"WP(default, stmt={stmt_stripped[:30]!r}) = {result[:60]!r}")
        return result

    def strongest_postcondition(self, pre: str, stmt: str) -> str:
        """Compute the strongest postcondition of executing stmt from pre.

        For assignment statements, the exact SP introduces an existential to
        capture the pre-state value.  For all other statements, the conjunction
        ``(and pre stmt)`` is returned as a sound approximation.

        The existential form for assignment ``(assign x e)`` is:
            ``(exists ((x_old Sort)) (and pre[x -> x_old] (= x e[x -> x_old])))``

        Because sort information is not available here, we use the approximation
        and log a note about the simplification.

        Args:
            pre: Precondition formula holding in the pre-state.
            stmt: SMT2 statement to execute.

        Returns:
            str: Strongest-postcondition formula approximation.
        """
        stmt_stripped = stmt.strip()
        if stmt_stripped.startswith("(assign "):
            inner = stmt_stripped[len("(assign "):-1].strip()
            space_idx = inner.index(" ")
            var = inner[:space_idx].strip()
            expr = inner[space_idx:].strip()
            # Approximate with existential over x_old
            pre_renamed = self._substitute(pre, var, f"{var}_old")
            expr_renamed = self._substitute(expr, var, f"{var}_old")
            sp = (
                f"(exists (({var}_old Int)) "
                f"(and {pre_renamed} (= {var} {expr_renamed})))"
            )
            self._computation_log.append(f"SP(assign {var}, pre={pre[:20]!r}) [existential]")
            return sp
        # Approximation for all other forms
        sp = f"(and {pre} {stmt_stripped})"
        self._computation_log.append(f"SP(default, pre={pre[:20]!r}) = {sp[:60]!r}")
        return sp

    def check_wp_feasibility(self, wp_smt: str) -> bool:
        """Heuristic check for whether a WP formula is satisfiable.

        This method uses lightweight syntactic tests to quickly classify
        formulas as obviously satisfiable or obviously unsatisfiable without
        invoking a full SMT solver.  When neither extreme is detected the
        formula is treated optimistically as satisfiable.

        The heuristics applied are:
        1. If the formula contains the literal ``true`` → satisfiable.
        2. If the formula contains the literal ``false`` → unsatisfiable.
        3. If the formula is shorter than 10 characters and contains no
           logical operators → likely trivially satisfiable.
        4. Otherwise → optimistically satisfiable.

        Args:
            wp_smt: SMT2 formula string to evaluate.

        Returns:
            bool: Heuristic satisfiability estimate.
        """
        stripped = wp_smt.strip()
        if "true" in stripped.lower():
            return True
        if "false" in stripped.lower():
            return False
        # Short formula with no operators — likely a variable reference
        if len(stripped) < 10 and not any(op in stripped for op in ("=>", "and", "or", "not")):
            return True
        # Optimistic default
        return True

    def copilot_wp_hint(self, failure: FailureArtifact) -> str:
        """Return copilot-targeted advice for computing WP of a specific failure.

        The hint is kind-specific and suggests invariants, lemmas, or
        annotations that a copilot repair agent should target when constructing
        the weakest precondition for the given failure.

        Args:
            failure: The :class:`FailureArtifact` for which to generate hints.

        Returns:
            str: Multi-line hint string for the copilot pipeline.
        """
        hints: dict[FailureKind, str] = {
            FailureKind.DIVISION_BY_ZERO: (
                "copilot: insert a non-zero guard on the denominator before the division. "
                "WP candidate: (not (= denominator 0)). "
                "Consider adding a requires-clause or runtime assertion."
            ),
            FailureKind.INDEX_OUT_OF_BOUNDS: (
                "copilot: add a bounds-check loop invariant: "
                "(and (>= index 0) (< index (length array))). "
                "WP candidate propagates backwards through the loop to the call site."
            ),
            FailureKind.NULL_DEREF: (
                "copilot: introduce a non-null precondition for the pointer. "
                "WP candidate: (not (= ptr null)). "
                "Null checks should appear immediately before dereference, not far upstream."
            ),
            FailureKind.ARITHMETIC_OVERFLOW: (
                "copilot: add range checks before the arithmetic operation. "
                "WP candidate: (and (>= expr min_val) (<= expr max_val)). "
                "Consider using saturating arithmetic or widening to larger sort."
            ),
            FailureKind.PRECONDITION_VIOLATION: (
                "copilot: propagate the precondition to all call sites. "
                "Synthesise a call-site guard that implies the callee precondition."
            ),
            FailureKind.POSTCONDITION_VIOLATION: (
                "copilot: strengthen the function body or weaken the postcondition. "
                "Verify that all return paths establish the postcondition."
            ),
            FailureKind.ASSERTION_FAILURE: (
                "copilot: inspect the assertion and its context. "
                "Compute WP backwards through the preceding statements to find "
                "the minimal pre-state condition that guarantees assertion holds."
            ),
            FailureKind.TYPE_MISMATCH: (
                "copilot: insert a type-coercion or sort-cast before the operation. "
                "WP candidate: ensure the argument has the correct sort/type."
            ),
        }
        return hints.get(failure.kind, "copilot: no specific hint available for this failure kind.")

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _substitute(formula: str, var: str, replacement: str) -> str:
        """Textual substitution of ``var`` with ``replacement`` in ``formula``.

        Performs a whole-word replacement, targeting occurrences of ``var``
        that are delimited by whitespace or s-expression delimiters.  This is
        not capture-avoiding but is sufficient for the simple SMT2 fragments
        handled in the scalar encoding pipeline.

        Args:
            formula: Source formula string.
            var: Variable name to replace.
            replacement: Expression string to substitute in.

        Returns:
            str: Formula with all occurrences of ``var`` replaced.
        """
        import re
        pattern = r'(?<![A-Za-z0-9_])' + re.escape(var) + r'(?![A-Za-z0-9_])'
        return re.sub(pattern, replacement, formula)

    @staticmethod
    def _split_two_sexprs(text: str) -> tuple[str, str]:
        """Split a string containing two top-level s-expressions.

        Walks the character stream, tracking parenthesis depth.  Returns the
        first two balanced s-expressions found.

        Args:
            text: Input string with two consecutive s-expressions.

        Returns:
            tuple[str, str]: The two s-expression strings.

        Raises:
            ValueError: If fewer than two balanced s-expressions are found.
        """
        parts: list[str] = []
        depth = 0
        start = 0
        i = 0
        while i < len(text) and len(parts) < 2:
            ch = text[i]
            if ch == '(':
                if depth == 0:
                    start = i
                depth += 1
            elif ch == ')':
                depth -= 1
                if depth == 0:
                    parts.append(text[start:i + 1])
                    start = i + 1
            elif depth == 0 and ch not in (' ', '\n', '\t'):
                # Atom (non-parenthesised term)
                atom_end = i
                while atom_end < len(text) and text[atom_end] not in (' ', '\n', '\t', '(', ')'):
                    atom_end += 1
                parts.append(text[i:atom_end])
                i = atom_end
                continue
            i += 1
        if len(parts) < 2:
            raise ValueError(f"Could not split two s-expressions from: {text!r}")
        return parts[0], parts[1]

    @staticmethod
    def _split_three_sexprs(text: str) -> tuple[str, str, str]:
        """Split a string containing three top-level s-expressions.

        Extends :meth:`_split_two_sexprs` to extract three balanced s-
        expressions from the input string.

        Args:
            text: Input string with three consecutive s-expressions.

        Returns:
            tuple[str, str, str]: The three s-expression strings.

        Raises:
            ValueError: If fewer than three balanced s-expressions are found.
        """
        parts: list[str] = []
        depth = 0
        start = 0
        i = 0
        while i < len(text) and len(parts) < 3:
            ch = text[i]
            if ch == '(':
                if depth == 0:
                    start = i
                depth += 1
            elif ch == ')':
                depth -= 1
                if depth == 0:
                    parts.append(text[start:i + 1])
                    start = i + 1
            elif depth == 0 and ch not in (' ', '\n', '\t'):
                atom_end = i
                while atom_end < len(text) and text[atom_end] not in (' ', '\n', '\t', '(', ')'):
                    atom_end += 1
                parts.append(text[i:atom_end])
                i = atom_end
                continue
            i += 1
        if len(parts) < 3:
            raise ValueError(f"Could not split three s-expressions from: {text!r}")
        return parts[0], parts[1], parts[2]


# ========================= Main encoder class ================================


class FailureArtifactEncoder:
    """Encodes failure conditions as Z3-ready :class:`FailureArtifact` objects.

    ``FailureArtifactEncoder`` is the primary entry point for the scalar
    encoding of failure conditions in the jugeo copilot pipeline.  It provides:

    * Direct construction of :class:`FailureArtifact` from raw SMT2 and kind
      metadata (``encode_failure``).
    * Extraction of failure artifacts from :class:`Countermodel` instances
      produced by Z3 sessions (``encode_from_countermodel``).
    * Convenience wrappers for common failure patterns: assertion failures
      and arithmetic overflows.
    * Batch encoding with error resilience.
    * Assumption minimisation to reduce query complexity.
    * Human-readable copilot failure reports.

    All produced artifacts are cached by fingerprint to avoid duplicate work
    within a single session.

    Attributes:
        _wp_extractor: Embedded :class:`FailurePreconditionExtractor` for WP
            computations requested by downstream pipeline stages.
        _artifact_cache: Map from fingerprint to cached artifact.
        _stats: Running counters for encoded artifacts, countermodel extractions,
            and minimisation calls.
    """

    def __init__(self) -> None:
        """Initialise the encoder with empty cache and zero statistics."""
        self._wp_extractor = FailurePreconditionExtractor()
        self._artifact_cache: dict[str, FailureArtifact] = {}
        self._stats: dict[str, int] = {
            "artifacts_encoded": 0,
            "from_countermodels": 0,
            "minimizations": 0,
        }
        logger.debug("FailureArtifactEncoder initialised")

    # ------------------------------------------------------------------
    # Core encoding
    # ------------------------------------------------------------------

    def encode_failure(
        self,
        kind: FailureKind,
        trigger: str,
        context: list[str],
        *,
        support: SupportRegion | None = None,
        severity_override: int | None = None,
    ) -> FailureArtifact:
        """Create and cache a :class:`FailureArtifact` from raw parameters.

        Constructs a new artifact with a generated unique identifier, assigns
        the default severity for the given kind (unless overridden), and
        attaches the copilot label ``copilot:<kind_lower>``.  The result is
        stored in the internal cache keyed by its fingerprint.

        Args:
            kind: The :class:`FailureKind` to encode.
            trigger: SMT2 formula that witnesses the failure (the trigger
                condition).
            context: List of SMT2 assumption strings providing the environment
                in which the failure may occur.
            support: Optional :class:`SupportRegion` to associate with the
                artifact.  A default region is used when not supplied.
            severity_override: When provided, overrides the kind's default
                severity.

        Returns:
            FailureArtifact: The newly constructed (or cached) artifact.
        """
        artifact_id = f"fail_{uuid.uuid4().hex[:8]}"
        severity = severity_override if severity_override is not None else kind.severity_default()
        copilot_label = f"copilot:{kind.name.lower()}"
        effective_support: SupportRegion = support if support is not None else SupportRegion()

        artifact = FailureArtifact(
            artifact_id=artifact_id,
            kind=kind,
            trigger_smt=trigger,
            context_assumptions=tuple(context),
            support=effective_support,
            severity=severity,
            copilot_label=copilot_label,
        )

        # Cache by fingerprint for deduplication
        fp = artifact.fingerprint()
        if fp in self._artifact_cache:
            logger.debug(
                "Cache hit for fingerprint %s (kind=%s)", fp[:8], kind.name
            )
            return self._artifact_cache[fp]

        self._artifact_cache[fp] = artifact
        self._stats["artifacts_encoded"] += 1
        logger.info(
            "Encoded failure artifact %s kind=%s severity=%s trigger=%r",
            artifact_id,
            kind.name,
            severity,
            trigger[:60],
        )
        return artifact

    def encode_from_countermodel(self, countermodel: Countermodel) -> FailureArtifact:
        """Extract a :class:`FailureArtifact` from a Z3 countermodel.

        Inspects the :class:`Countermodel` for known failure-class attributes
        and maps them to the appropriate :class:`FailureKind`.  If the
        countermodel carries an explicit :class:`FailureClass`, that is used
        directly.  Otherwise the method falls back to ``ASSERTION_FAILURE``.

        The trigger formula is derived from the countermodel's obstruction
        expression, and assumptions are taken from its context terms.

        Args:
            countermodel: A :class:`Countermodel` produced by a Z3 session.

        Returns:
            FailureArtifact: Encoded failure artifact derived from the
                countermodel.
        """
        # Attempt to map FailureClass -> FailureKind
        kind = FailureKind.ASSERTION_FAILURE
        try:
            failure_class = countermodel.failure_class
            _class_map: dict[Any, FailureKind] = {
                FailureClass.PRECONDITION: FailureKind.PRECONDITION_VIOLATION,
                FailureClass.POSTCONDITION: FailureKind.POSTCONDITION_VIOLATION,
                FailureClass.ASSERTION: FailureKind.ASSERTION_FAILURE,
                FailureClass.DIVISION_BY_ZERO: FailureKind.DIVISION_BY_ZERO,
                FailureClass.INDEX_OOB: FailureKind.INDEX_OUT_OF_BOUNDS,
                FailureClass.NULL_DEREF: FailureKind.NULL_DEREF,
                FailureClass.TYPE_ERROR: FailureKind.TYPE_MISMATCH,
                FailureClass.OVERFLOW: FailureKind.ARITHMETIC_OVERFLOW,
            }
            kind = _class_map.get(failure_class, FailureKind.ASSERTION_FAILURE)
        except AttributeError:
            logger.debug(
                "Countermodel has no failure_class attribute; defaulting to ASSERTION_FAILURE"
            )

        # Extract trigger from obstruction expression
        trigger = "(assert false)"  # safe default
        try:
            obstruction = countermodel.obstruction_smt
            if obstruction:
                trigger = obstruction
        except AttributeError:
            pass

        # Extract context assumptions
        context: list[str] = []
        try:
            raw_context = countermodel.context_assumptions
            if isinstance(raw_context, (list, tuple)):
                context = [str(a) for a in raw_context]
        except AttributeError:
            pass

        # Extract support region
        support: SupportRegion | None = None
        try:
            support = countermodel.support_region
        except AttributeError:
            pass

        self._stats["from_countermodels"] += 1
        logger.debug(
            "Encoding failure from countermodel: kind=%s trigger=%r", kind.name, trigger[:60]
        )
        return self.encode_failure(kind, trigger, context, support=support)

    def encode_assertion_failure(
        self,
        assert_smt: str,
        assumptions: list[str],
    ) -> FailureArtifact:
        """Convenience encoder for assertion-failure artifacts.

        Wraps the given assertion formula in a negation to form the trigger
        (the failure fires when the assertion does *not* hold) and delegates
        to :meth:`encode_failure` with kind ``ASSERTION_FAILURE``.

        Args:
            assert_smt: SMT2 formula that is expected to hold (the positive
                assertion).  The trigger will be its negation.
            assumptions: Context assumptions in scope at the assertion point.

        Returns:
            FailureArtifact: Encoded assertion-failure artifact.
        """
        trigger = f"(not {assert_smt})"
        return self.encode_failure(
            FailureKind.ASSERTION_FAILURE,
            trigger,
            assumptions,
        )

    def encode_arithmetic_failure(
        self,
        expr_smt: str,
        bounds: tuple[float, float],
    ) -> FailureArtifact:
        """Convenience encoder for arithmetic overflow/underflow artifacts.

        Constructs a disjunctive trigger formula asserting that the given
        expression falls outside the specified numeric bounds.

        Args:
            expr_smt: SMT2 expression that may overflow or underflow.
            bounds: ``(lower, upper)`` tuple defining the valid range.  The
                trigger fires when the expression is below ``lower`` or above
                ``upper``.

        Returns:
            FailureArtifact: Encoded arithmetic-overflow artifact.
        """
        lower, upper = bounds
        trigger = f"(or (> {expr_smt} {upper}) (< {expr_smt} {lower}))"
        return self.encode_failure(
            FailureKind.ARITHMETIC_OVERFLOW,
            trigger,
            [],
        )

    # ------------------------------------------------------------------
    # Batch operations
    # ------------------------------------------------------------------

    def batch_encode_failures(
        self,
        failures: list[tuple[FailureKind, str, list[str]]],
    ) -> list[FailureArtifact]:
        """Encode multiple failure descriptions, tolerating individual errors.

        Iterates over the provided list of ``(kind, trigger, context)`` tuples
        and calls :meth:`encode_failure` for each one.  Errors encountered
        during individual encodings are logged but do not abort the batch.

        Args:
            failures: List of ``(FailureKind, trigger_smt, context)`` tuples.

        Returns:
            list[FailureArtifact]: Successfully encoded artifacts.  Entries
                that raised exceptions are silently omitted from the result.
        """
        results: list[FailureArtifact] = []
        for idx, (kind, trigger, context) in enumerate(failures):
            try:
                artifact = self.encode_failure(kind, trigger, context)
                results.append(artifact)
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    "batch_encode_failures: error at index %d (kind=%s): %s",
                    idx,
                    kind.name,
                    exc,
                )
        logger.info(
            "batch_encode_failures: %d/%d successful", len(results), len(failures)
        )
        return results

    # ------------------------------------------------------------------
    # Assumption minimisation
    # ------------------------------------------------------------------

    def minimize_assumptions(self, artifact: FailureArtifact) -> FailureArtifact:
        """Return a new artifact with a minimised set of context assumptions.

        Removes obviously redundant assumptions:
        * Duplicate strings (first occurrence is retained).
        * Occurrences of the literal ``true`` (always satisfied, carry no
          information).
        * Occurrences of the empty string.

        A more sophisticated implementation would invoke an SMT solver to
        check entailment between pairs of assumptions; that is left as a
        future extension.

        Args:
            artifact: The source :class:`FailureArtifact` whose assumptions
                should be minimised.

        Returns:
            FailureArtifact: New artifact with minimised assumptions.  If no
                change was needed the original artifact is returned unchanged.
        """
        self._stats["minimizations"] += 1
        seen: set[str] = set()
        minimised: list[str] = []
        for asm in artifact.context_assumptions:
            asm_clean = asm.strip()
            if not asm_clean:
                continue
            if asm_clean.lower() in ("true", "True"):
                continue
            if asm_clean in seen:
                continue
            seen.add(asm_clean)
            minimised.append(asm_clean)

        if tuple(minimised) == artifact.context_assumptions:
            # No change needed — return original frozen object
            return artifact

        logger.debug(
            "minimize_assumptions: %d -> %d assumptions for artifact %s",
            len(artifact.context_assumptions),
            len(minimised),
            artifact.artifact_id,
        )
        return FailureArtifact(
            artifact_id=artifact.artifact_id,
            kind=artifact.kind,
            trigger_smt=artifact.trigger_smt,
            context_assumptions=tuple(minimised),
            support=artifact.support,
            severity=artifact.severity,
            copilot_label=artifact.copilot_label,
        )

    # ------------------------------------------------------------------
    # Query emission
    # ------------------------------------------------------------------

    def emit_failure_query(self, artifact: FailureArtifact) -> str:
        """Emit a complete, solver-ready SMT2 query for the given artifact.

        Prepends an appropriate ``(set-logic ...)`` declaration based on the
        complexity of the trigger formula:
        * Arithmetic triggers (containing numeric operators) get ``QF_LIA``
          (quantifier-free linear integer arithmetic).
        * Triggers with quantifiers (containing ``exists`` or ``forall``) get
          ``LIA``.
        * All others get the general ``ALL`` logic.

        Args:
            artifact: The :class:`FailureArtifact` to emit a query for.

        Returns:
            str: Complete SMT2 query string including logic declaration.
        """
        trigger = artifact.trigger_smt
        if any(kw in trigger for kw in ("exists", "forall")):
            logic = "LIA"
        elif any(op in trigger for op in ("+", "-", "*", ">", "<", ">=", "<=")):
            logic = "QF_LIA"
        else:
            logic = "ALL"

        header = f"(set-logic {logic})\n"
        return header + artifact.to_smt2_query()

    # ------------------------------------------------------------------
    # Reporting
    # ------------------------------------------------------------------

    def copilot_failure_report(self, artifacts: list[FailureArtifact]) -> str:
        """Generate a comprehensive copilot-targeted failure report.

        The report is structured as a plain-text document covering:
        * Per-kind breakdown with counts.
        * Severity distribution summary.
        * Count of avoidable vs unavoidable failures.
        * Top-3 highest-severity failures with their trigger formulas and
          suggested WP hints.
        * Encoder performance statistics.

        Args:
            artifacts: List of :class:`FailureArtifact` objects to report on.

        Returns:
            str: Multi-section plain-text report.
        """
        if not artifacts:
            return "# Copilot Failure Report\n\nNo failure artifacts to report.\n"

        lines: list[str] = ["# Copilot Failure Report", ""]

        # --- Per-kind breakdown ---
        from collections import Counter
        kind_counts: Counter[str] = Counter(a.kind.name for a in artifacts)
        lines.append("## Failure Counts by Kind")
        for kind_name, count in sorted(kind_counts.items()):
            lines.append(f"  {kind_name}: {count}")
        lines.append("")

        # --- Severity distribution ---
        sev_counts: Counter[str] = Counter(a.severity_label() for a in artifacts)
        lines.append("## Severity Distribution")
        for label in ("critical", "high", "medium", "low"):
            lines.append(f"  {label}: {sev_counts.get(label, 0)}")
        lines.append("")

        # --- Avoidability ---
        avoidable = sum(1 for a in artifacts if a.is_avoidable())
        unavoidable = len(artifacts) - avoidable
        lines.append("## Avoidability Summary")
        lines.append(f"  Avoidable:   {avoidable}")
        lines.append(f"  Unavoidable: {unavoidable}")
        lines.append("")

        # --- Top-3 highest severity ---
        top = sorted(artifacts, key=lambda a: a.severity, reverse=True)[:3]
        lines.append("## Top Highest-Severity Failures")
        for rank, art in enumerate(top, start=1):
            lines.append(f"  [{rank}] id={art.artifact_id}  kind={art.kind.name}  severity={art.severity}")
            lines.append(f"       trigger: {art.trigger_smt[:80]}")
            hint = self._wp_extractor.copilot_wp_hint(art)
            # Only first line of hint for brevity
            lines.append(f"       hint: {hint.splitlines()[0]}")
        lines.append("")

        # --- Encoder stats ---
        lines.append("## Encoder Statistics")
        for key, val in self._stats.items():
            lines.append(f"  {key}: {val}")
        lines.append("")

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Property accessors
    # ------------------------------------------------------------------

    @property
    def wp_extractor(self) -> FailurePreconditionExtractor:
        """Return the embedded WP extractor.

        Returns:
            FailurePreconditionExtractor: The WP/SP computation helper.
        """
        return self._wp_extractor

    @property
    def stats(self) -> dict[str, int]:
        """Return a snapshot of the current encoding statistics.

        Returns:
            dict[str, int]: Shallow copy of the statistics dictionary.
        """
        return dict(self._stats)

    @property
    def cache_size(self) -> int:
        """Return the number of artifacts currently held in the cache.

        Returns:
            int: Number of cached artifacts.
        """
        return len(self._artifact_cache)


# ========================== Module-level helpers ============================


def encode_simple_failure(kind: FailureKind, trigger: str) -> FailureArtifact:
    """Convenience function for one-shot failure encoding with no context.

    Creates a transient :class:`FailureArtifactEncoder` and encodes a single
    failure with an empty context assumption list.  This is the simplest
    possible way to create a :class:`FailureArtifact` and is intended for
    interactive use, tests, and quick scripting.

    The encoder is discarded after encoding, so no caching benefits apply.
    For repeated encoding, prefer instantiating :class:`FailureArtifactEncoder`
    directly.

    Args:
        kind: The :class:`FailureKind` of the failure to encode.
        trigger: SMT2 formula that witnesses the failure condition.

    Returns:
        FailureArtifact: Newly constructed failure artifact with empty context.

    Example::

        artifact = encode_simple_failure(
            FailureKind.DIVISION_BY_ZERO, "(= denom 0)"
        )
        print(artifact.severity_label())  # "high"
        print(artifact.negation_formula())  # "(not (= denom 0))"
    """
    encoder = FailureArtifactEncoder()
    return encoder.encode_failure(kind, trigger, [])
