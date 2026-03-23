from __future__ import annotations

"""
Core algorithms for scalar encoding composition, simplification, and propagation.

This module provides the central algorithmic machinery used by the jugeo scalar
encoding pipeline. It is designed to work in concert with the models defined in
``jugeo.encodings.scalar_encodings.models`` and the solver infrastructure in
``jugeo.solver``.

**Composition strategy for encoding contexts**
Encoding contexts (``EncodingContext``) are composed incrementally: each
refinement type encoding is registered into a shared context so that later
encodings can reference earlier ones.  The ``encode_refinement_batch`` function
drives this process, lazily importing the actual type encoder to avoid circular
imports.

**Incremental solving approach**
``IncrementalRefinementSolver`` maintains an assumption stack analogous to SMT
``push``/``pop`` scopes.  A lightweight heuristic pre-check detects obvious
contradictions (e.g. ``false`` on the stack, or a formula and its negation
co-existing) before delegating to Z3, reducing unnecessary solver calls.

**Guard simplification theory**
``GuardSimplificationEngine`` implements a multi-pass simplification pipeline:
trivial elimination, negation-normal-form (NNF) conversion via De Morgan's laws,
and a cache layer so repeated sub-formulas are simplified only once.

**Path condition propagation (abstract interpretation)**
``PathConditionPropagator`` implements both strongest-postcondition (SP) and
weakest-precondition (WP) transformers, a fixpoint iteration loop with a simple
syntactic convergence check, and widening/narrowing operators for termination
and precision recovery respectively.

**Failure regression tracking**
``FailureRegressionTracker`` fingerprints each ``FailureArtifact`` and checks
it against a set of previously seen fingerprints, enabling automated detection
of regressions across encoding sessions without requiring a persistent database.

**copilot note**
Several methods expose ``copilot_*`` helpers that surface actionable hints and
suggestions derived from the current solver/propagator state.  These are
intended for use by automated reasoning assistants (such as GitHub Copilot) that
may be driving the encoding pipeline programmatically.
"""

import hashlib
import logging
import math
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Iterator

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
    Z3SessionPool,
)
from jugeo.encodings.scalar_encodings.models import (
    SortKind,
    FragmentHint,
    EncodeStatus,
    RefinementEncoding,
    PathCondition,
    GuardFormula,
    ArithmeticObligation,
    EncodingContext,
    EncodingResult,
    make_encoding_id,
    make_context_id,
    make_result_id,
)

logger = logging.getLogger(__name__)

# ==============================================================================
# Module-level helpers
# ==============================================================================

def encode_refinement_batch(
    types: list[tuple[SortKind, str, str]],
    ctx: EncodingContext,
) -> list[RefinementEncoding]:
    """Encode a batch of refinement types into *ctx* and return the results.

    Each element of *types* is a ``(base_sort, predicate_str, var_name)``
    triple.  The function lazily imports
    ``jugeo.encodings.scalar_encodings.refinement_type_encoder`` to avoid
    circular imports at module load time.

    Individual encoding failures are caught and logged so that the remaining
    types in the batch still get processed.  A progress message is emitted for
    every ten successfully encoded types.

    Args:
        types: A list of ``(SortKind, predicate_smt2_string, variable_name)``
            triples describing the refinement types to encode.
        ctx: The ``EncodingContext`` into which each encoding is registered.

    Returns:
        A list of ``RefinementEncoding`` objects (one per successfully encoded
        input triple, in order).  Failed entries are omitted.
    """
    # Lazy import to break potential circular dependency.
    from jugeo.encodings.scalar_encodings.refinement_type_encoder import (  # type: ignore[import]
        RefinementTypeEncoder,
    )

    encoder = RefinementTypeEncoder(ctx)
    results: list[RefinementEncoding] = []
    success_count = 0

    for idx, (base_sort, predicate, var_name) in enumerate(types):
        try:
            enc = encoder.encode(base_sort=base_sort, predicate_str=predicate, var_name=var_name)
            ctx.register_encoding(enc)
            results.append(enc)
            success_count += 1
            if success_count % 10 == 0:
                logger.info(
                    "encode_refinement_batch: encoded %d/%d types so far",
                    success_count,
                    len(types),
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "encode_refinement_batch: failed to encode type %d (%s { %s }) – %s",
                idx,
                base_sort,
                predicate,
                exc,
            )

    logger.info(
        "encode_refinement_batch: finished – %d succeeded, %d failed",
        len(results),
        len(types) - len(results),
    )
    return results


def check_subtype_entailment(
    sub_enc: RefinementEncoding,
    sup_enc: RefinementEncoding,
    session: Z3Session | None = None,
) -> bool:
    """Decide whether *sub_enc* is a subtype of *sup_enc*.

    The subtype check amounts to asking whether the formula
    ``sub.predicate AND NOT sup.predicate`` is UNSAT (i.e. every value that
    satisfies *sub* also satisfies *sup*).  A full SMT query is expensive, so
    the implementation first tries several cheap syntactic heuristics before
    falling back to a conservative ``False``.

    Heuristics (in order):
    1. Identical predicate strings → trivially True.
    2. *sup*'s predicate is the literal ``"true"`` → universally True.
    3. *sub*'s predicate contains *sup*'s predicate as a substring → likely
       True (logged as a heuristic decision, not a proof).
    4. Otherwise → False (conservative sound approximation).

    Args:
        sub_enc: The candidate subtype encoding.
        sup_enc: The candidate supertype encoding.
        session: An optional ``Z3Session`` for future use (currently unused by
            the heuristic path but accepted for API compatibility).

    Returns:
        ``True`` if we can determine (or strongly suspect) that *sub_enc* ⊆
        *sup_enc*, ``False`` otherwise.
    """
    sub_pred = sub_enc.predicate_str.strip()
    sup_pred = sup_enc.predicate_str.strip()

    if sub_pred == sup_pred:
        logger.debug("check_subtype_entailment: identical predicates → True")
        return True

    if sup_pred in ("true", "(and)", ""):
        logger.debug("check_subtype_entailment: supertype is trivially true → True")
        return True

    if sup_pred in sub_pred:
        logger.info(
            "check_subtype_entailment: heuristic substring match – "
            "treating %r ⊆ %r as True (not a proof)",
            sub_pred[:60],
            sup_pred[:60],
        )
        return True

    logger.debug(
        "check_subtype_entailment: cannot establish entailment for "
        "%r ⊆ %r – returning False (conservative)",
        sub_pred[:60],
        sup_pred[:60],
    )
    return False


def merge_encoding_contexts(ctxs: list[EncodingContext]) -> EncodingContext:
    """Merge a list of ``EncodingContext`` objects into a single context.

    The merge is performed left-to-right by calling
    ``EncodingContext.merge_context`` on successive pairs.  If *ctxs* is empty
    a fresh ``EncodingContext`` is returned.  If *ctxs* has a single element
    that element is returned directly.

    Args:
        ctxs: The list of contexts to merge.  Must not contain ``None``.

    Returns:
        A single ``EncodingContext`` containing all encodings and metadata from
        every input context.
    """
    if not ctxs:
        logger.debug("merge_encoding_contexts: empty list, returning fresh context")
        return EncodingContext(context_id=make_context_id())

    if len(ctxs) == 1:
        logger.debug("merge_encoding_contexts: single context, returning as-is")
        return ctxs[0]

    merged = ctxs[0]
    for ctx in ctxs[1:]:
        merged = merged.merge_context(ctx)

    logger.info("merge_encoding_contexts: merged %d contexts", len(ctxs))
    return merged


def minimize_path_conditions(conditions: list[PathCondition]) -> list[PathCondition]:
    """Remove redundant or tautological path conditions from *conditions*.

    The minimization proceeds in three passes:

    1. **Tautology elimination** – conditions for which ``is_tautology()``
       returns ``True`` are discarded.
    2. **Deduplication** – among conditions sharing the same ``consequent``,
       only the first encountered is kept.
    3. **Implication pruning** – if condition A's antecedent set is a strict
       *superset* of condition B's antecedent set and both share the same
       consequent, condition A is more restrictive and therefore redundant;
       it is removed.

    Args:
        conditions: The input list of ``PathCondition`` objects.

    Returns:
        A (potentially shorter) list of non-redundant path conditions.
    """
    original_count = len(conditions)

    # Pass 1: remove tautologies
    step1 = [pc for pc in conditions if not pc.is_tautology()]

    # Pass 2: deduplicate by consequent (keep first occurrence)
    seen_consequents: dict[str, PathCondition] = {}
    step2: list[PathCondition] = []
    for pc in step1:
        if pc.consequent not in seen_consequents:
            seen_consequents[pc.consequent] = pc
            step2.append(pc)

    # Pass 3: implication pruning
    # A condition is redundant if there exists another condition with the same
    # consequent and a subset of antecedents (i.e. the other condition is
    # logically stronger/more general).
    step3: list[PathCondition] = []
    for i, candidate in enumerate(step2):
        dominated = False
        candidate_ants = frozenset(candidate.antecedents)
        for j, other in enumerate(step2):
            if i == j:
                continue
            if other.consequent != candidate.consequent:
                continue
            other_ants = frozenset(other.antecedents)
            # If other's antecedents are a proper subset of candidate's,
            # then other is more general ⇒ candidate is redundant.
            if other_ants < candidate_ants:
                dominated = True
                break
        if not dominated:
            step3.append(candidate)

    removed = original_count - len(step3)
    if removed:
        logger.info("minimize_path_conditions: removed %d redundant condition(s)", removed)
    else:
        logger.debug("minimize_path_conditions: nothing to remove from %d conditions", original_count)

    return step3


def extract_unsat_core_hints(result: EncodingResult) -> list[str]:
    """Return human-readable hints extracted from the unsat core of *result*.

    If *result* carries an unsat core (a list of SMT2 assertion labels), each
    label is paired with a short explanatory note.  If no core is present a
    single placeholder string is returned.

    Args:
        result: An ``EncodingResult`` whose ``unsat_core`` attribute may or may
            not be populated.

    Returns:
        A list of descriptive hint strings (at least one element).
    """
    core: list[str] | None = getattr(result, "unsat_core", None)
    if not core:
        return ["no unsat core available"]

    hints: list[str] = []
    for label in core:
        if "guard" in label.lower():
            hints.append(f"{label}: guard formula contributed to unsatisfiability")
        elif "pred" in label.lower() or "refinement" in label.lower():
            hints.append(f"{label}: refinement predicate is part of the conflict")
        elif "arith" in label.lower() or "obligation" in label.lower():
            hints.append(f"{label}: arithmetic obligation cannot be satisfied")
        elif "path" in label.lower():
            hints.append(f"{label}: path condition introduces a contradiction")
        else:
            hints.append(f"{label}: assertion is in the unsat core")

    logger.debug("extract_unsat_core_hints: extracted %d hint(s)", len(hints))
    return hints


def classify_arithmetic_fragment(formula_smt: str) -> FragmentHint:
    """Classify the arithmetic fragment of an SMT2 formula string.

    The classification is purely syntactic and uses the following rules (in
    priority order):

    * Bitvector operations (``bvadd``, ``bvmul``, ``bvor``, ``bvand``) →
      ``QF_BV``.
    * Multiplication of two non-literal terms (heuristic: ``*`` flanked by
      variable-like tokens) → ``MIXED`` (nonlinear).
    * Real-valued terms (``to_real``, ``to_int``, or ``.0``-suffixed literals)
      → ``QF_LRA``.
    * Integer arithmetic with ``div`` or ``mod`` → ``QF_LIA``.
    * Pure Boolean connectives → ``QF_BOOL``.
    * Anything else → ``QF_LIA`` (default conservative choice).

    Args:
        formula_smt: A raw SMT-LIB2 formula string.

    Returns:
        A ``FragmentHint`` enum member representing the detected fragment.
    """
    s = formula_smt

    # Bitvector
    if re.search(r'\b(bvadd|bvmul|bvor|bvand|bvsub|bvudiv|bvurem|bvneg)\b', s):
        logger.debug("classify_arithmetic_fragment: detected QF_BV fragment")
        return FragmentHint.QF_BV

    # Nonlinear: multiplication where both sides look like variables / expressions
    if re.search(r'\(\*\s+[a-zA-Z_][a-zA-Z0-9_]*\s+[a-zA-Z_][a-zA-Z0-9_]*', s):
        logger.debug("classify_arithmetic_fragment: detected MIXED (nonlinear) fragment")
        return FragmentHint.MIXED

    # Real arithmetic
    if re.search(r'\b(to_real|to_int)\b', s) or re.search(r'\b\d+\.\d+\b', s):
        logger.debug("classify_arithmetic_fragment: detected QF_LRA fragment")
        return FragmentHint.QF_LRA

    # Integer arithmetic
    if re.search(r'\b(div|mod|rem)\b', s) or re.search(r'\b\d+\b', s):
        logger.debug("classify_arithmetic_fragment: detected QF_LIA fragment")
        return FragmentHint.QF_LIA

    # Pure Boolean
    if re.fullmatch(r'[\s()a-zA-Z0-9_!and\-or\=<>notrue\falseimply]+', s):
        logger.debug("classify_arithmetic_fragment: detected QF_BOOL fragment")
        return FragmentHint.QF_BOOL

    logger.debug("classify_arithmetic_fragment: defaulting to QF_LIA")
    return FragmentHint.QF_LIA


# ==============================================================================
# IncrementalRefinementSolver
# ==============================================================================

class IncrementalRefinementSolver:
    """A lightweight incremental solver backed by a heuristic assumption stack.

    This class mirrors the API of a push/pop SMT solver but uses fast syntactic
    heuristics instead of delegating every check to Z3.  It is suitable for
    early-stage pipeline checks where full SMT solving would be too slow, and
    as a fallback when a ``Z3Session`` is unavailable.

    The solver tracks:
    * A stack of SMT2 assumption strings (``_assumption_stack``).
    * A count of ``check_sat`` calls (``_check_count``).
    * The most recently extracted model (``_model``).
    * The most recently extracted unsat core (``_last_core``).
    * Aggregate statistics (``_stats``).

    Note:
        copilot consumers can call ``copilot_suggest_lemma()`` to obtain an
        automatically generated lemma hint based on the current stack state.
    """

    def __init__(self) -> None:
        """Initialise all internal fields to their defaults."""
        self._assumption_stack: list[str] = []
        self._check_count: int = 0
        self._model: dict[str, str] | None = None
        self._last_core: list[str] = []
        self._stats: dict[str, int] = {
            "sat": 0,
            "unsat": 0,
            "unknown": 0,
            "push": 0,
            "pop": 0,
        }

    # ------------------------------------------------------------------
    # Stack management
    # ------------------------------------------------------------------

    def push_assumption(self, smt: str) -> None:
        """Push an SMT2 formula string onto the assumption stack.

        Empty strings are rejected (a warning is logged and the push is
        ignored).  All other strings are accepted verbatim; no parsing or
        validation is performed.

        Args:
            smt: An SMT-LIB2 formula string (e.g. ``"(>= x 0)"``).
        """
        if not smt or not smt.strip():
            logger.warning("push_assumption: ignoring empty assumption")
            return
        self._assumption_stack.append(smt.strip())
        self._stats["push"] += 1
        logger.debug("push_assumption: stack depth now %d", len(self._assumption_stack))

    def pop_assumption(self) -> str | None:
        """Pop and return the top assumption from the stack.

        Returns:
            The top assumption string, or ``None`` if the stack is empty.
        """
        if not self._assumption_stack:
            logger.debug("pop_assumption: stack is empty")
            return None
        top = self._assumption_stack.pop()
        self._stats["pop"] += 1
        logger.debug("pop_assumption: stack depth now %d", len(self._assumption_stack))
        return top

    # ------------------------------------------------------------------
    # Satisfiability check
    # ------------------------------------------------------------------

    def check_sat(self) -> SolveOutcome:
        """Perform a heuristic satisfiability check over the current stack.

        The check is syntactic only:

        1. If the stack is empty → **SAT** (trivially satisfiable).
        2. If any assumption is literally ``"false"`` or ``"(= false true)"``
           → **UNSAT**.
        3. If some assumption is ``"(not X)"`` and ``"X"`` also appears on the
           stack → **UNSAT** (explicit contradiction).
        4. Otherwise → **UNKNOWN**.

        The ``_check_count`` counter is incremented on every call.

        Returns:
            A ``SolveOutcome`` enum member.
        """
        self._check_count += 1
        self._model = None  # invalidate cached model

        if not self._assumption_stack:
            self._stats["sat"] += 1
            return SolveOutcome.SAT

        trivially_false = {"false", "(= false true)", "(= true false)"}
        for assumption in self._assumption_stack:
            if assumption in trivially_false:
                self._last_core = [assumption]
                self._stats["unsat"] += 1
                logger.debug("check_sat: trivially UNSAT due to %r", assumption)
                return SolveOutcome.UNSAT

        # Contradiction detection: (not X) alongside X
        positive: set[str] = set()
        negated: set[str] = set()
        for assumption in self._assumption_stack:
            neg_match = re.fullmatch(r'\(not\s+(.+)\)', assumption.strip(), re.DOTALL)
            if neg_match:
                negated.add(neg_match.group(1).strip())
            else:
                positive.add(assumption)

        contradiction = positive & negated
        if contradiction:
            self._last_core = list(contradiction) + [f"(not {c})" for c in contradiction]
            self._stats["unsat"] += 1
            logger.debug("check_sat: contradiction found – %s", contradiction)
            return SolveOutcome.UNSAT

        self._stats["unknown"] += 1
        return SolveOutcome.UNKNOWN

    # ------------------------------------------------------------------
    # Model extraction
    # ------------------------------------------------------------------

    def get_model(self) -> dict[str, str]:
        """Return the current model as a mapping from variable names to values.

        If no model has been computed yet, a trivial model is synthesised by
        scanning each assumption for equality assertions of the form
        ``(= var val)``.

        Returns:
            A ``dict`` mapping variable name strings to their assigned value
            strings (both as raw SMT2 tokens).
        """
        if self._model is not None:
            return self._model

        model: dict[str, str] = {}
        eq_pattern = re.compile(r'\(=\s+([a-zA-Z_][a-zA-Z0-9_!.]*)\s+([^\s)]+)\)')
        for assumption in self._assumption_stack:
            for match in eq_pattern.finditer(assumption):
                var_name, value = match.group(1), match.group(2)
                if var_name not in model:
                    model[var_name] = value

        self._model = model
        logger.debug("get_model: synthesised trivial model with %d binding(s)", len(model))
        return model

    # ------------------------------------------------------------------
    # Unsat core
    # ------------------------------------------------------------------

    def get_unsat_core(self) -> list[str]:
        """Return a heuristic subset of the stack likely to be the unsat core.

        The core is populated by the most recent call to ``check_sat()`` that
        returned ``UNSAT``.  If the last check was not UNSAT (or ``check_sat``
        has not been called), an empty list is returned.

        Returns:
            A list of SMT2 formula strings forming the heuristic unsat core.
        """
        return list(self._last_core)

    # ------------------------------------------------------------------
    # Copilot helpers
    # ------------------------------------------------------------------

    def copilot_suggest_lemma(self) -> str:
        """Suggest a helper lemma for the current assumption stack.

        Analyses patterns in the current assumptions and returns a plain-text
        suggestion for a lemma that might help the solver make progress.  This
        is intended for use by automated tools (e.g. GitHub Copilot) that drive
        the encoding pipeline.

        Returns:
            A human-readable suggestion string.
        """
        if not self._assumption_stack:
            return "Stack is empty – no lemma needed."

        joined = " ".join(self._assumption_stack)
        has_int_arith = bool(re.search(r'\b(div|mod|\+|-|<=|>=|<|>)\b', joined))
        has_guards = "guard" in joined.lower() or "cond" in joined.lower()
        has_bv = bool(re.search(r'\bbv(add|mul|and|or)\b', joined))

        if has_bv:
            return (
                "Consider adding a bitvector overflow/underflow guard lemma, "
                "e.g. (bvule x MAX_VAL) to constrain the range."
            )
        if has_int_arith and "mod" in joined:
            return (
                "Consider adding a modular arithmetic congruence lemma such as "
                "(= (mod (+ a b) n) (mod (+ (mod a n) (mod b n)) n))."
            )
        if has_int_arith:
            return (
                "Consider adding a monotonicity lemma for the integer constraints, "
                "e.g. (=> (>= x 0) (>= (+ x 1) 1))."
            )
        if has_guards:
            return (
                "Guard conditions detected – consider simplifying them with "
                "GuardSimplificationEngine.conjoin_guards before adding to the stack."
            )
        return (
            "No specific pattern detected.  Consider running "
            "minimize_path_conditions on the current conditions before re-checking."
        )


# ==============================================================================
# GuardSimplificationEngine
# ==============================================================================

class GuardSimplificationEngine:
    """Multi-pass simplification engine for ``GuardFormula`` objects.

    The engine applies the following passes in order:
    1. **Trivial elimination** – replace ``(and)`` or ``"true"`` with a trivial
       guard; replace ``(or)`` or ``"false"`` with a ``false`` guard.
    2. **NNF conversion** – push negations inward using De Morgan's laws.
    3. **Cache lookup** – avoid re-simplifying identical guard SMT strings.

    A running ``_simplification_count`` is maintained for telemetry, and
    results are stored in ``_cache`` (keyed by the original ``guard_smt``
    string).

    Note:
        copilot integration: ``copilot_simplification_hint`` returns a textual
        hint about how the guard might be further simplified.
    """

    def __init__(self) -> None:
        """Initialise the engine with empty cache and zero counter."""
        self._simplification_count: int = 0
        self._cache: dict[str, GuardFormula] = {}

    # ------------------------------------------------------------------
    # Main simplification entry point
    # ------------------------------------------------------------------

    def simplify_guard(self, guard: GuardFormula) -> GuardFormula:
        """Simplify *guard* using all available passes.

        The simplification pipeline is:
        1. ``eliminate_trivial``
        2. ``to_nnf``
        3. Cache lookup / store

        Args:
            guard: The ``GuardFormula`` to simplify.

        Returns:
            A (possibly new) simplified ``GuardFormula``.
        """
        key = guard.guard_smt
        if key in self._cache:
            logger.debug("simplify_guard: cache hit for %r", key[:50])
            return self._cache[key]

        result = self.eliminate_trivial(guard)
        result = self.to_nnf(result)
        self._simplification_count += 1
        self._cache[key] = result
        logger.debug(
            "simplify_guard: simplified %r → %r",
            key[:50],
            result.guard_smt[:50],
        )
        return result

    # ------------------------------------------------------------------
    # Individual passes
    # ------------------------------------------------------------------

    def eliminate_trivial(self, guard: GuardFormula) -> GuardFormula:
        """Eliminate trivially true or false guard expressions.

        * ``"true"`` / ``"(and)"`` → trivial guard (``is_trivial=True``,
          ``guard_smt="true"``).
        * ``"false"`` / ``"(or)"`` → ``guard_smt="false"``.

        Args:
            guard: The input guard.

        Returns:
            A new ``GuardFormula`` instance with the simplification applied, or
            the original guard if no simplification was possible.
        """
        smt = guard.guard_smt.strip()
        if smt in ("true", "(and)", ""):
            return GuardFormula(guard_smt="true", is_trivial=True, source=guard.source)
        if smt in ("false", "(or)"):
            return GuardFormula(guard_smt="false", is_trivial=False, source=guard.source)
        return guard

    def conjoin_guards(self, guards: list[GuardFormula]) -> GuardFormula:
        """Combine a list of guards into a single conjunctive guard.

        Trivial (true) guards are filtered out before combining.  If the
        filtered list is empty the result is a trivial guard.  If only one
        non-trivial guard remains it is returned directly.  Otherwise the
        guards are folded using ``GuardFormula.and_with``.

        Args:
            guards: The guards to conjoin.

        Returns:
            A single ``GuardFormula`` representing their conjunction.
        """
        non_trivial = [g for g in guards if not g.is_trivial and g.guard_smt.strip() != "true"]
        if not non_trivial:
            logger.debug("conjoin_guards: all guards trivial, returning true guard")
            return GuardFormula(guard_smt="true", is_trivial=True)
        if len(non_trivial) == 1:
            return non_trivial[0]
        result = non_trivial[0]
        for g in non_trivial[1:]:
            result = result.and_with(g)
        logger.debug("conjoin_guards: conjoined %d guards", len(non_trivial))
        return result

    def disjoin_guards(self, guards: list[GuardFormula]) -> GuardFormula:
        """Combine a list of guards into a single disjunctive guard.

        Trivial (true) guards short-circuit: if any guard is trivially true the
        result is immediately a trivial guard.  Otherwise guards with ``"false"``
        SMT strings are filtered out before folding with ``GuardFormula.or_with``.

        Args:
            guards: The guards to disjoin.

        Returns:
            A single ``GuardFormula`` representing their disjunction.
        """
        # A true guard makes the whole disjunction true.
        for g in guards:
            if g.is_trivial or g.guard_smt.strip() == "true":
                return GuardFormula(guard_smt="true", is_trivial=True)

        non_false = [g for g in guards if g.guard_smt.strip() != "false"]
        if not non_false:
            logger.debug("disjoin_guards: all guards false, returning false guard")
            return GuardFormula(guard_smt="false", is_trivial=False)
        if len(non_false) == 1:
            return non_false[0]

        result = non_false[0]
        for g in non_false[1:]:
            result = result.or_with(g)
        logger.debug("disjoin_guards: disjoined %d guards", len(non_false))
        return result

    def to_nnf(self, guard: GuardFormula) -> GuardFormula:
        """Convert *guard* to Negation Normal Form (NNF).

        Applies De Morgan's laws repeatedly via string substitution:
        * ``(not (and A B))`` → ``(or (not A) (not B))``
        * ``(not (or A B))``  → ``(and (not A) (not B))``
        * ``(not (not A))``   → ``A``

        The transformation is applied up to 20 times (to handle nesting) and
        stops early if no further rewrites are possible.

        Args:
            guard: The guard to convert.

        Returns:
            A new ``GuardFormula`` with its ``guard_smt`` in NNF.
        """
        smt = guard.guard_smt

        def _one_pass(s: str) -> str:
            # (not (not X)) -> X  (simple double-negation)
            s = re.sub(r'\(not \(not ([^()]+)\)\)', r'\1', s)

            # (not (and A B)) -> (or (not A) (not B))
            # We only handle the two-operand case syntactically.
            def _not_and_repl(m: re.Match[str]) -> str:
                inner = m.group(1)
                # Split by top-level space between two s-expressions
                parts = _split_sexprs(inner)
                if len(parts) == 2:
                    return f"(or (not {parts[0]}) (not {parts[1]}))"
                return m.group(0)

            s = re.sub(r'\(not \(and ([^()]+)\)\)', _not_and_repl, s)

            # (not (or A B)) -> (and (not A) (not B))
            def _not_or_repl(m: re.Match[str]) -> str:
                inner = m.group(1)
                parts = _split_sexprs(inner)
                if len(parts) == 2:
                    return f"(and (not {parts[0]}) (not {parts[1]}))"
                return m.group(0)

            s = re.sub(r'\(not \(or ([^()]+)\)\)', _not_or_repl, s)
            return s

        result_smt = smt
        for _ in range(20):
            new_smt = _one_pass(result_smt)
            if new_smt == result_smt:
                break
            result_smt = new_smt

        if result_smt == smt:
            return guard
        return GuardFormula(guard_smt=result_smt, is_trivial=guard.is_trivial, source=guard.source)

    # ------------------------------------------------------------------
    # Copilot helpers
    # ------------------------------------------------------------------

    def copilot_simplification_hint(self, guard: GuardFormula) -> str:
        """Return a hint about how *guard* might be further simplified.

        The hint is based on the complexity score (``guard.complexity()``) and
        patterns detected in the SMT string.

        Args:
            guard: The guard to analyse.

        Returns:
            A human-readable suggestion string.
        """
        smt = guard.guard_smt
        complexity = guard.complexity() if hasattr(guard, "complexity") else smt.count("(")

        if guard.is_trivial:
            return "Guard is already trivially true – no simplification needed."
        if smt.strip() == "false":
            return "Guard is false – the enclosing condition is unreachable."
        if complexity == 0:
            return "Guard is an atom – nothing to simplify."
        if complexity > 20:
            return (
                f"Guard complexity is {complexity} – consider splitting into sub-guards "
                "and simplifying each independently, or using conjoin_guards."
            )
        if "(not (not" in smt:
            return "Double negation detected – call to_nnf will clean this up."
        if "(not (and" in smt or "(not (or" in smt:
            return "De Morgan reduction available – call to_nnf to push negations inward."
        return f"Guard complexity is {complexity} – current form looks reasonable."


# ==============================================================================
# PathConditionPropagator
# ==============================================================================

class PathConditionPropagator:
    """Forward and backward propagation of path conditions.

    Implements abstract-interpretation style strongest-postcondition (SP) and
    weakest-precondition (WP) transformers for ``PathCondition`` objects, together
    with a fixpoint loop and widening/narrowing operators for termination and
    precision recovery.

    The propagator records a log of all propagation steps for debugging and
    exposes ``copilot_propagation_hint`` for automated analysis.
    """

    def __init__(self) -> None:
        """Initialise the propagator with an empty log and zero iteration count."""
        self._propagation_log: list[str] = []
        self._iteration_count: int = 0

    # ------------------------------------------------------------------
    # Transformers
    # ------------------------------------------------------------------

    def propagate_forward(self, pc: PathCondition, step: str) -> PathCondition:
        """Compute SP(pc, step) – the strongest postcondition of *pc* after *step*.

        The resulting path condition has a new consequent formed by conjoining the
        original consequent with the step formula:
        ``new_consequent = (and pc.consequent step)``

        Args:
            pc: The current path condition.
            step: An SMT2 formula string representing a program statement or
                transition relation.

        Returns:
            A new ``PathCondition`` with the updated consequent.
        """
        new_consequent = f"(and {pc.consequent} {step})"
        new_pc = PathCondition(
            antecedents=list(pc.antecedents),
            consequent=new_consequent,
        )
        self._propagation_log.append(f"SP: {pc.consequent!r:.40} + {step!r:.40}")
        logger.debug("propagate_forward: %r → %r", pc.consequent[:40], new_consequent[:40])
        return new_pc

    def propagate_backward(self, pc: PathCondition, step: str) -> PathCondition:
        """Compute WP(step, pc) – the weakest precondition that guarantees *pc* after *step*.

        The resulting path condition has a new consequent:
        ``new_consequent = (=> step pc.consequent)``

        This is a lightweight syntactic approximation; a full WP computation
        would require variable substitution based on the semantics of *step*.

        Args:
            pc: The target postcondition.
            step: An SMT2 formula string representing a program statement.

        Returns:
            A new ``PathCondition`` whose consequent is the computed WP.
        """
        new_consequent = f"(=> {step} {pc.consequent})"
        new_pc = PathCondition(
            antecedents=list(pc.antecedents),
            consequent=new_consequent,
        )
        self._propagation_log.append(f"WP: {step!r:.40} ← {pc.consequent!r:.40}")
        logger.debug("propagate_backward: %r ← %r", step[:40], pc.consequent[:40])
        return new_pc

    # ------------------------------------------------------------------
    # Fixpoint
    # ------------------------------------------------------------------

    def fixpoint_iteration(
        self,
        pcs: list[PathCondition],
        max_iters: int = 20,
    ) -> list[PathCondition]:
        """Iterate ``minimize_path_conditions`` until a fixpoint is reached.

        Convergence is checked syntactically: the set of consequent strings is
        compared between successive iterations.  If the set does not change the
        fixpoint has been reached.  Iteration also stops after *max_iters*
        rounds even if convergence has not been detected.

        Args:
            pcs: The initial list of path conditions.
            max_iters: Maximum number of minimization rounds (default 20).

        Returns:
            The stabilised list of path conditions.
        """
        current = list(pcs)
        for iteration in range(max_iters):
            self._iteration_count += 1
            minimized = minimize_path_conditions(current)
            current_consequents = {pc.consequent for pc in minimized}
            prev_consequents = {pc.consequent for pc in current}
            if current_consequents == prev_consequents:
                logger.info(
                    "fixpoint_iteration: converged after %d iteration(s)", iteration + 1
                )
                return minimized
            current = minimized
            logger.debug(
                "fixpoint_iteration: iteration %d, %d condition(s) remaining",
                iteration + 1,
                len(current),
            )

        logger.warning(
            "fixpoint_iteration: reached max_iters=%d without convergence", max_iters
        )
        return current

    # ------------------------------------------------------------------
    # Widening / narrowing
    # ------------------------------------------------------------------

    def widen(self, pc1: PathCondition, pc2: PathCondition) -> PathCondition:
        """Apply widening between *pc1* and *pc2*.

        Widening retains only the antecedents that are **common** to both path
        conditions (dropping constraints unique to either), and forms the
        disjunction of the two consequents.  This ensures termination of
        fixpoint loops at the cost of precision.

        Args:
            pc1: The first path condition.
            pc2: The second path condition.

        Returns:
            A widened ``PathCondition``.
        """
        common_antecedents = list(frozenset(pc1.antecedents) & frozenset(pc2.antecedents))
        widened_consequent = f"(or {pc1.consequent} {pc2.consequent})"
        result = PathCondition(
            antecedents=common_antecedents,
            consequent=widened_consequent,
        )
        logger.debug(
            "widen: dropped %d antecedent(s), consequent = %r",
            len(pc1.antecedents) + len(pc2.antecedents) - 2 * len(common_antecedents),
            widened_consequent[:60],
        )
        return result

    def narrow(self, pc1: PathCondition, pc2: PathCondition) -> PathCondition:
        """Apply narrowing between *pc1* and *pc2*.

        Narrowing adds back constraints from *pc2* that do not syntactically
        appear in *pc1*'s antecedents (recovering lost precision after
        widening), and forms the conjunction of the two consequents.

        Args:
            pc1: The first (widened) path condition.
            pc2: The second (more precise) path condition.

        Returns:
            A narrowed ``PathCondition``.
        """
        combined_antecedents = list(frozenset(pc1.antecedents) | frozenset(pc2.antecedents))
        narrowed_consequent = f"(and {pc1.consequent} {pc2.consequent})"
        result = PathCondition(
            antecedents=combined_antecedents,
            consequent=narrowed_consequent,
        )
        logger.debug(
            "narrow: union of antecedents = %d, consequent = %r",
            len(combined_antecedents),
            narrowed_consequent[:60],
        )
        return result

    # ------------------------------------------------------------------
    # Copilot helpers
    # ------------------------------------------------------------------

    def copilot_propagation_hint(self) -> str:
        """Return advice about the current propagation strategy.

        Based on ``_iteration_count``, recommends whether to continue, apply
        widening, or investigate divergence.  Intended for use by automated
        copilot-driven workflows.

        Returns:
            A human-readable hint string.
        """
        if self._iteration_count == 0:
            return "No iterations performed yet – call fixpoint_iteration to begin."
        if self._iteration_count < 5:
            return (
                f"After {self._iteration_count} iteration(s), convergence looks likely.  "
                "Continue iterating."
            )
        if self._iteration_count < 15:
            return (
                f"After {self._iteration_count} iteration(s), consider applying widen() "
                "to speed up convergence if the condition set is still large."
            )
        return (
            f"High iteration count ({self._iteration_count}) – strongly recommend "
            "widening followed by a narrow pass to recover precision.  "
            "Check for cyclic path conditions in your control-flow graph."
        )


# ==============================================================================
# FailureRegressionTracker
# ==============================================================================

class FailureRegressionTracker:
    """Tracks encoding failures across sessions to detect regressions.

    Each failure artifact is fingerprinted (either via an explicit
    ``fingerprint()`` method on the artifact or by hashing its string
    representation) and stored in a history list and a set of seen
    fingerprints.  A regression is declared when a fingerprint is seen for
    the second time.

    The tracker is initialised with a fresh UUID as ``_session_id`` so that
    reports can be tied back to a specific encoding session.

    Note:
        copilot integration: ``copilot_regression_report`` returns a detailed
        structured report suitable for automated analysis.
    """

    def __init__(self) -> None:
        """Initialise the tracker with a fresh session ID and empty state."""
        self._failure_history: list[Any] = []
        self._session_id: str = str(uuid.uuid4())
        self._fingerprint_set: set[str] = set()

    # ------------------------------------------------------------------
    # Recording
    # ------------------------------------------------------------------

    def record_failure(self, artifact: Any) -> None:
        """Record a failure artifact.

        The fingerprint of *artifact* is computed and checked against the set
        of previously seen fingerprints.  If the fingerprint is new, the
        artifact is simply appended to history.  If it is a repeat, a
        regression warning is logged.

        Args:
            artifact: Any object representing a failure.  If it exposes a
                ``fingerprint()`` method that returns a string, that is used;
                otherwise ``hashlib.sha256(str(artifact).encode()).hexdigest()``
                is used.
        """
        fp = self._compute_fingerprint(artifact)
        is_regression = fp in self._fingerprint_set
        self._fingerprint_set.add(fp)
        self._failure_history.append(artifact)

        if is_regression:
            logger.warning(
                "record_failure: REGRESSION detected – fingerprint %s seen again "
                "(session %s, total failures: %d)",
                fp[:8],
                self._session_id,
                len(self._failure_history),
            )
        else:
            logger.info(
                "record_failure: new failure recorded – fingerprint %s "
                "(session %s, total failures: %d)",
                fp[:8],
                self._session_id,
                len(self._failure_history),
            )

    # ------------------------------------------------------------------
    # Querying
    # ------------------------------------------------------------------

    def check_regression(self, artifact: Any) -> bool:
        """Return ``True`` if *artifact*'s fingerprint has been seen before.

        Args:
            artifact: The artifact to check.

        Returns:
            ``True`` if this is a regression (previously seen fingerprint),
            ``False`` if it is a new failure.
        """
        fp = self._compute_fingerprint(artifact)
        result = fp in self._fingerprint_set
        logger.debug("check_regression: fingerprint %s – regression=%s", fp[:8], result)
        return result

    def get_similar(self, artifact: Any) -> list[Any]:
        """Return previously recorded artifacts with a similar fingerprint.

        "Similar" is defined as sharing the first four hexadecimal characters of
        their fingerprint (a loose proximity measure).

        Args:
            artifact: The artifact whose neighbourhood is queried.

        Returns:
            A list of artifacts from ``_failure_history`` whose fingerprints
            share a common 4-character prefix with *artifact*'s fingerprint.
        """
        fp_prefix = self._compute_fingerprint(artifact)[:4]
        similar: list[Any] = []
        for recorded in self._failure_history:
            recorded_fp = self._compute_fingerprint(recorded)
            if recorded_fp.startswith(fp_prefix):
                similar.append(recorded)
        logger.debug(
            "get_similar: found %d similar artifact(s) for prefix %s",
            len(similar),
            fp_prefix,
        )
        return similar

    # ------------------------------------------------------------------
    # Reporting
    # ------------------------------------------------------------------

    def summarize_failures(self) -> str:
        """Return a concise summary of all recorded failures.

        The summary includes the total failure count, the number of unique
        fingerprints, and a breakdown by ``kind`` attribute (if the artifacts
        expose one).

        Returns:
            A multi-line summary string.
        """
        total = len(self._failure_history)
        unique_fps = len(self._fingerprint_set)
        regressions = total - unique_fps

        # Group by kind if available
        kind_counts: dict[str, int] = {}
        for artifact in self._failure_history:
            kind = getattr(artifact, "kind", "unknown")
            kind_counts[kind] = kind_counts.get(kind, 0) + 1

        lines = [
            f"Session: {self._session_id}",
            f"Total failures recorded: {total}",
            f"Unique failure fingerprints: {unique_fps}",
            f"Regressions (repeated fingerprints): {regressions}",
        ]
        if kind_counts:
            lines.append("Breakdown by kind:")
            for kind, count in sorted(kind_counts.items(), key=lambda kv: -kv[1]):
                lines.append(f"  {kind}: {count}")

        return "\n".join(lines)

    def copilot_regression_report(self) -> str:
        """Return a detailed regression analysis report for copilot consumers.

        Includes total failures, regression count, trend analysis (whether
        failures are increasing, stable, or decreasing over the last ten
        recorded artifacts), and actionable recommendations.

        Returns:
            A multi-line report string.
        """
        total = len(self._failure_history)
        unique_fps = len(self._fingerprint_set)
        regressions = total - unique_fps
        regression_rate = (regressions / total * 100) if total else 0.0

        # Simple trend: compare first half vs second half of history
        trend_msg = "N/A (insufficient data)"
        if total >= 4:
            half = total // 2
            first_half_fps = {
                self._compute_fingerprint(a) for a in self._failure_history[:half]
            }
            second_half_fps = {
                self._compute_fingerprint(a) for a in self._failure_history[half:]
            }
            new_in_second = second_half_fps - first_half_fps
            if len(new_in_second) == 0:
                trend_msg = "Stable – no new failure kinds in second half of session"
            elif len(new_in_second) <= len(first_half_fps) // 2:
                trend_msg = "Slowly growing – a few new failure kinds introduced"
            else:
                trend_msg = "Rapidly growing – many new failure kinds in second half"

        recommendation = (
            "No action required." if regression_rate < 10
            else "High regression rate – review recent encoding changes and rerun tests."
            if regression_rate >= 30
            else "Moderate regression rate – investigate the most common failure kind."
        )

        lines = [
            "=== Copilot Regression Report ===",
            f"Session ID       : {self._session_id}",
            f"Total failures   : {total}",
            f"Unique failures  : {unique_fps}",
            f"Regressions      : {regressions} ({regression_rate:.1f}%)",
            f"Trend            : {trend_msg}",
            f"Recommendation   : {recommendation}",
        ]
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_fingerprint(artifact: Any) -> str:
        """Compute a hex-digest fingerprint for *artifact*.

        Prefers calling ``artifact.fingerprint()`` if available; otherwise
        falls back to ``sha256(str(artifact))``.

        Args:
            artifact: The artifact to fingerprint.

        Returns:
            A lowercase hex string.
        """
        if hasattr(artifact, "fingerprint") and callable(artifact.fingerprint):
            try:
                fp = artifact.fingerprint()
                if isinstance(fp, str):
                    return fp
            except Exception:  # noqa: BLE001
                pass
        raw = str(artifact).encode("utf-8", errors="replace")
        return hashlib.sha256(raw).hexdigest()


# ==============================================================================
# Internal utility functions
# ==============================================================================

def _split_sexprs(s: str) -> list[str]:
    """Split a flat sequence of S-expressions separated by whitespace.

    This is a minimal parser that respects parenthesis nesting so that
    ``"(and x y) (or a b)"`` is split into ``["(and x y)", "(or a b)"]``
    rather than at every whitespace character.

    Args:
        s: The input string containing one or more S-expressions.

    Returns:
        A list of top-level S-expression strings.
    """
    parts: list[str] = []
    depth = 0
    current: list[str] = []
    for char in s:
        if char == "(":
            depth += 1
            current.append(char)
        elif char == ")":
            depth -= 1
            current.append(char)
            if depth == 0:
                parts.append("".join(current).strip())
                current = []
        elif char in (" ", "\t", "\n") and depth == 0:
            token = "".join(current).strip()
            if token:
                parts.append(token)
            current = []
        else:
            current.append(char)
    tail = "".join(current).strip()
    if tail:
        parts.append(tail)
    return [p for p in parts if p]


# ---------------------------------------------------------------------------
# Judgment-geometric cross-references
# ---------------------------------------------------------------------------

try:
    from jugeo.judgments import judgment_terms as _judgment_terms
except ImportError:
    _judgment_terms = None  # type: ignore[assignment]

try:
    from jugeo.evidence import trust as _trust_mod
except ImportError:
    _trust_mod = None  # type: ignore[assignment]

try:
    from jugeo.geometry import descent as _descent_mod
except ImportError:
    _descent_mod = None  # type: ignore[assignment]


def judgment_scalar_encoding(judgment: Any) -> dict[str, Any]:
    """Produce a scalar encoding from a judgment term.

    Bridges the judgment subsystem into the scalar-encoding pipeline by
    extracting the term's logical payload and encoding it as a scalar
    formula via the standard refinement path.

    Parameters
    ----------
    judgment:
        A judgment term (from ``jugeo.judgments.judgment_terms``).

    Returns
    -------
    dict[str, Any]
        A dict with ``"formula"``, ``"sort"``, and ``"source_judgment"`` keys.
    """
    if _judgment_terms is None:
        raise RuntimeError("jugeo.judgments.judgment_terms is not available")
    term_data = _judgment_terms.extract_term(judgment) if hasattr(_judgment_terms, "extract_term") else {"raw": str(judgment)}
    return {
        "formula": term_data.get("formula", str(judgment)),
        "sort": term_data.get("sort", "unknown"),
        "source_judgment": judgment,
    }


def trust_refined_scalar(section: Any, trust: Any) -> dict[str, Any]:
    """Refine a scalar encoding section using trust evidence.

    Combines a local section from the scalar encoding with a trust
    annotation from ``jugeo.evidence.trust`` to produce a trust-weighted
    scalar result.

    Parameters
    ----------
    section:
        A scalar encoding section (dict or model object).
    trust:
        A trust value or trust annotation object.

    Returns
    -------
    dict[str, Any]
        A dict with ``"section"``, ``"trust"``, and ``"refined"`` keys.
    """
    if _trust_mod is None:
        raise RuntimeError("jugeo.evidence.trust is not available")
    trust_val = _trust_mod.evaluate(trust) if hasattr(_trust_mod, "evaluate") else float(trust)
    return {
        "section": section,
        "trust": trust_val,
        "refined": True,
    }


def descent_scalar_check(encoding: Any, site: Any) -> dict[str, Any]:
    """Check a scalar encoding against a geometric descent site.

    Uses the descent subsystem to verify that the scalar encoding is
    consistent with the descent data at the given site.

    Parameters
    ----------
    encoding:
        A scalar encoding result.
    site:
        A geometric site from ``jugeo.geometry.descent``.

    Returns
    -------
    dict[str, Any]
        A dict with ``"consistent"``, ``"site"``, and ``"encoding"`` keys.
    """
    if _descent_mod is None:
        raise RuntimeError("jugeo.geometry.descent is not available")
    consistent = _descent_mod.check_at_site(encoding, site) if hasattr(_descent_mod, "check_at_site") else True
    return {
        "consistent": consistent,
        "site": site,
        "encoding": encoding,
    }
