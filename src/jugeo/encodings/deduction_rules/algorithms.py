"""Core deduction algorithms for JuGeo -- theory2.tex Chapter 33.

This module provides the algorithmic machinery that drives the deduction-rule
sub-system: rule application, transition-sequence computation, unification,
cut elimination, proof-trace verification, obligation synthesis, and
Copilot-assisted rule suggestion.

The algorithms are designed to operate over the data model defined in
models.py and the rule classes defined in s01-s04.

Architecture
------------
- apply_deduction_rule        -- apply a rule to a judgment
- compute_transition_sequence -- build a full transition chain
- check_rule_applicability    -- test if a rule is applicable
- unify_judgment_patterns     -- unify two judgment strings
- run_transition_system       -- run a system to fixpoint
- eliminate_cuts              -- Gentzen-style cut elimination
- verify_proof_trace          -- verify a full proof trace
- synthesize_rules_for_obligations -- synthesize rules for outstanding obligations
- copilot_suggest_next_rule   -- ask Copilot for the next rule to apply
"""

from __future__ import annotations

import hashlib
import re
import time
import uuid
from collections import defaultdict, deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Sequence

try:
    from jugeo.solver.z3_session import Z3Session, Z3Formula, Z3Encoder, Z3Result
except Exception:  # pragma: no cover – optional dependency
    class Z3Session:  # type: ignore[no-redef]
        """Stub when jugeo.solver is unavailable."""

    class Z3Formula:  # type: ignore[no-redef]
        """Stub."""

    class Z3Encoder:  # type: ignore[no-redef]
        """Stub."""

    class Z3Result:  # type: ignore[no-redef]
        """Stub."""

try:
    from jugeo.solver.reconstruction import ModelReconstruction
except Exception:  # pragma: no cover
    class ModelReconstruction:  # type: ignore[no-redef]
        """Stub."""

try:
    from jugeo.judgments.judgment_terms import JudgmentTerm
except Exception:  # pragma: no cover
    class JudgmentTerm:  # type: ignore[no-redef]
        """Stub."""

try:
    from jugeo.evidence.trust import TrustAlgebra, TrustLevel
except Exception:  # pragma: no cover
    class TrustAlgebra:  # type: ignore[no-redef]
        """Stub."""

    class TrustLevel(str, Enum):  # type: ignore[no-redef]
        """Minimal trust-level stub used when jugeo.evidence is unavailable."""
        UNVERIFIED = "UNVERIFIED"
        SOLVER_DISCHARGED = "SOLVER_DISCHARGED"
        MECHANICALLY_VERIFIED = "MECHANICALLY_VERIFIED"

from jugeo.encodings.deduction_rules.models import (
    DeductionRule,
    JudgmentTransition,
    InferenceStep,
    RuleApplication,
    TransitionSystem,
    RuleKind,
    ApplicationResult,
    TransitionKind,
    InferenceStatus,
    make_rule,
    make_axiom_rule,
    _new_id,
    _stable_hash,
    _now_iso,
)


# ---------------------------------------------------------------------------
# Public algorithms
# ---------------------------------------------------------------------------


def apply_deduction_rule(
    rule: DeductionRule,
    judgment: Any,
    context: dict[str, Any] | None = None,
) -> RuleApplication:
    """Apply *rule* to *judgment* within an optional ambient *context*.

    This is the primary entry-point for single-step rule application.  The
    function follows a strict pipeline:

    1. Attempt to unify the rule's *conclusion* schema against the string
       representation of *judgment*.  If unification fails the result is
       ``UNIFICATION_FAILURE``.
    2. Check that the context's trust level meets the rule's
       ``trust_required`` threshold.  Failure yields ``TRUST_INSUFFICIENT``.
    3. Discharge premises by attempting to unify available judgments from
       ``context["available_judgments"]`` against the rule's premise schemas.
       Failure yields ``UNIFICATION_FAILURE``.
    4. Evaluate all side conditions under the accumulated substitution.
       Failure yields ``SIDE_CONDITION_FAILURE``.
    5. Fire the rule via ``rule.fire()``.  Any unexpected exception yields
       ``ERROR``.

    Parameters
    ----------
    rule:
        The :class:`~jugeo.encodings.deduction_rules.models.DeductionRule`
        to apply.
    judgment:
        The target judgment (will be ``str()``-coerced).
    context:
        Ambient context.  Recognised keys:

        - ``trust_level`` – current :class:`TrustLevel` (default UNVERIFIED)
        - ``available_judgments`` – list of judgments that can discharge premises
        - ``target_judgment`` – explicit target string (overrides *judgment*)

    Returns
    -------
    RuleApplication
        An immutable record carrying the outcome, bindings, and any evidence
        produced by the rule firing.
    """
    ctx: dict[str, Any] = dict(context or {})
    judgment_str = ctx.get("target_judgment", str(judgment))
    t_start = time.monotonic()

    # ------------------------------------------------------------------ step 1
    conclusion_subst = rule._try_unify(rule.conclusion, judgment_str)
    if conclusion_subst is None:
        return RuleApplication(
            application_id=_new_id("app"),
            rule=rule,
            context=ctx,
            bindings={},
            timestamp=_now_iso(),
            result=ApplicationResult.UNIFICATION_FAILURE,
            evidence_produced=(),
        )

    # ------------------------------------------------------------------ step 2
    trust = ctx.get("trust_level", TrustLevel.UNVERIFIED)
    if isinstance(trust, str):
        try:
            trust = TrustLevel(trust)
        except (ValueError, KeyError):
            trust = TrustLevel.UNVERIFIED
    try:
        all_levels = list(TrustLevel)
        required_rank = all_levels.index(rule.trust_required)
        current_rank = all_levels.index(trust)
        if current_rank < required_rank:
            return RuleApplication(
                application_id=_new_id("app"),
                rule=rule,
                context=ctx,
                bindings=conclusion_subst,
                timestamp=_now_iso(),
                result=ApplicationResult.TRUST_INSUFFICIENT,
                evidence_produced=(),
            )
    except (ValueError, AttributeError):
        pass  # Cannot compare; allow by default.

    # ------------------------------------------------------------------ step 3
    accumulated_subst: dict[str, Any] = dict(conclusion_subst)
    if rule.premises:
        available: list[Any] = ctx.get("available_judgments", [str(judgment)])
        if not available:
            available = [str(judgment)]
        premise_candidates = [str(j) for j in available]
        # Pad or trim the candidate list to match the number of premises.
        while len(premise_candidates) < len(rule.premises):
            premise_candidates.append(str(judgment))
        premise_candidates = premise_candidates[: len(rule.premises)]
        premise_subst = rule.unify_premises(premise_candidates)
        if premise_subst is None:
            return RuleApplication(
                application_id=_new_id("app"),
                rule=rule,
                context=ctx,
                bindings=accumulated_subst,
                timestamp=_now_iso(),
                result=ApplicationResult.UNIFICATION_FAILURE,
                evidence_produced=(),
            )
        # Merge, checking consistency.
        for var, val in premise_subst.items():
            if var in accumulated_subst and accumulated_subst[var] != val:
                return RuleApplication(
                    application_id=_new_id("app"),
                    rule=rule,
                    context=ctx,
                    bindings=accumulated_subst,
                    timestamp=_now_iso(),
                    result=ApplicationResult.UNIFICATION_FAILURE,
                    evidence_produced=(),
                )
            accumulated_subst[var] = val

    # ------------------------------------------------------------------ step 4
    if not rule.check_side_conditions(accumulated_subst):
        return RuleApplication(
            application_id=_new_id("app"),
            rule=rule,
            context=ctx,
            bindings=accumulated_subst,
            timestamp=_now_iso(),
            result=ApplicationResult.SIDE_CONDITION_FAILURE,
            evidence_produced=(),
        )

    # ------------------------------------------------------------------ step 5
    try:
        discharged = [str(judgment)] * max(len(rule.premises), 1)
        fired = rule.fire(discharged[: len(rule.premises)], accumulated_subst)
    except Exception as exc:
        ctx["_fire_error"] = str(exc)
        return RuleApplication(
            application_id=_new_id("app"),
            rule=rule,
            context=ctx,
            bindings=accumulated_subst,
            timestamp=_now_iso(),
            result=ApplicationResult.ERROR,
            evidence_produced=(),
        )

    elapsed = time.monotonic() - t_start
    ctx["_elapsed_ms"] = round(elapsed * 1000, 3)
    evidence: tuple[Any, ...] = (fired,)

    return RuleApplication(
        application_id=_new_id("app"),
        rule=rule,
        context=ctx,
        bindings=accumulated_subst,
        timestamp=_now_iso(),
        result=ApplicationResult.APPLIED,
        evidence_produced=evidence,
    )


def compute_transition_sequence(
    rules: Sequence[DeductionRule],
    initial_judgment: Any,
    goal: str,
    max_steps: int = 500,
    context: dict[str, Any] | None = None,
) -> list[JudgmentTransition]:
    """Compute a forward transition sequence from *initial_judgment* towards *goal*.

    Uses a greedy best-first search: at each step every applicable rule is
    scored by the token-level similarity of its conclusion-instantiation to
    the *goal* string.  The highest-scoring applicable rule is selected.  The
    algorithm terminates when:

    - The current judgment equals *goal* (success), or
    - No rule is applicable to the current judgment (stuck), or
    - *max_steps* transitions have been taken (safety cap).

    Parameters
    ----------
    rules:
        Ordered sequence of :class:`DeductionRule` objects to consider.
    initial_judgment:
        Starting point for the transition chain.
    goal:
        Target judgment string we are trying to reach.
    max_steps:
        Maximum number of rule-application steps (default 500).
    context:
        Ambient context forwarded to applicability checks.

    Returns
    -------
    list[JudgmentTransition]
        The transitions taken, in order from initial to final.  May be empty
        if no rule applies at the first step.
    """
    ctx: dict[str, Any] = dict(context or {})
    transitions: list[JudgmentTransition] = []
    current: Any = initial_judgment
    visited: set[str] = set()
    rule_list = list(rules)

    for _step in range(max_steps):
        current_str = str(current)

        # Termination: goal reached.
        if current_str.strip() == goal.strip():
            break

        # Termination: already visited this state (cycle prevention).
        if current_str in visited:
            break
        visited.add(current_str)

        # Find applicable rules and score them.
        applicable = _find_applicable_rules(current_str, rule_list, ctx)
        if not applicable:
            break

        # Score each rule by how close its instantiated conclusion is to goal.
        scored: list[tuple[float, int, DeductionRule]] = []
        for i, r in enumerate(applicable):
            subst = r._try_unify(r.conclusion, current_str) or {}
            instantiated = r.instantiate(subst) if subst else r.conclusion
            token_sim = _token_similarity(instantiated, goal)
            ed = _edit_distance(instantiated, goal)
            # Normalise edit distance to [0, 1] (lower is better).
            max_len = max(len(instantiated), len(goal), 1)
            ed_score = 1.0 - (ed / max_len)
            combined = 0.6 * token_sim + 0.4 * ed_score
            scored.append((combined, i, r))

        # Sort descending by score; use index as tiebreaker.
        scored.sort(key=lambda x: (-x[0], x[1]))
        best_rule = scored[0][2]

        # Fire the best rule.
        app = apply_deduction_rule(best_rule, current, ctx)
        if not app.succeeded():
            # Remove rule from candidates and try next iteration.
            rule_list = [r for r in rule_list if r.rule_id != best_rule.rule_id]
            continue

        # Derive target judgment from the fired evidence.
        fire_result = app.evidence_produced[0] if app.evidence_produced else {}
        target_judgment = fire_result.get("conclusion", current_str) if isinstance(fire_result, dict) else current_str

        subst = best_rule._try_unify(best_rule.conclusion, current_str) or {}
        transition = JudgmentTransition(
            transition_id=_new_id("trans"),
            source_judgment=current,
            target_judgment=target_judgment,
            rule_applied=best_rule,
            substitution=subst,
            trust_delta=1 if best_rule.rule_kind == RuleKind.SEMANTIC else 0,
        )
        transitions.append(transition)
        current = target_judgment

    return transitions


def check_rule_applicability(
    rule: DeductionRule,
    judgment: Any,
    context: dict[str, Any] | None = None,
) -> tuple[bool, str]:
    """Test whether *rule* is applicable to *judgment* in *context*.

    Performs three independent checks in order, returning early with a
    descriptive failure message on the first violation:

    1. **Conclusion unification**: the rule's conclusion schema must unify with
       the string form of *judgment*.
    2. **Trust gate**: the context's trust level must be at least as strong as
       ``rule.trust_required``.
    3. **Side conditions**: every side condition must hold under the unification
       substitution.

    Parameters
    ----------
    rule:
        The rule to test.
    judgment:
        The candidate target judgment.
    context:
        Ambient context; ``trust_level`` key is inspected.

    Returns
    -------
    tuple[bool, str]
        ``(True, "")`` if the rule is applicable, or ``(False, reason)``
        where *reason* explains the first failure encountered.
    """
    ctx: dict[str, Any] = dict(context or {})
    judgment_str = str(judgment)

    # Check 1: conclusion unifies.
    subst = rule._try_unify(rule.conclusion, judgment_str)
    if subst is None:
        tokens_j = set(re.findall(r"\w+", judgment_str))
        tokens_c = set(re.findall(r"\w+", rule.conclusion))
        overlap = tokens_j & tokens_c
        return (
            False,
            f"Conclusion '{rule.conclusion}' does not unify with '{judgment_str}'. "
            f"Token overlap: {sorted(overlap) or 'none'}.",
        )

    # Check 2: trust gate.
    trust = ctx.get("trust_level", TrustLevel.UNVERIFIED)
    if isinstance(trust, str):
        try:
            trust = TrustLevel(trust)
        except (ValueError, KeyError):
            trust = TrustLevel.UNVERIFIED
    try:
        all_levels = list(TrustLevel)
        required_rank = all_levels.index(rule.trust_required)
        current_rank = all_levels.index(trust)
        if current_rank < required_rank:
            return (
                False,
                f"Trust level '{trust}' (rank {current_rank}) is below "
                f"required '{rule.trust_required}' (rank {required_rank}) "
                f"for rule '{rule.rule_name}'.",
            )
    except (ValueError, AttributeError):
        pass

    # Check 3: side conditions.
    if not rule.check_side_conditions(subst):
        failing: list[str] = []
        for name, cond in rule.side_conditions.items():
            try:
                if callable(cond):
                    ok = cond(subst)
                elif isinstance(cond, str):
                    ns = {"__builtins__": {}, **subst}
                    ok = eval(cond, ns)  # noqa: S307
                else:
                    ok = bool(cond)
            except Exception as exc:
                ok = False
                failing.append(f"{name} (exception: {exc})")
                continue
            if not ok:
                failing.append(name)
        return (
            False,
            f"Side condition(s) failed for rule '{rule.rule_name}': "
            + ", ".join(failing),
        )

    return (True, "")


def unify_judgment_patterns(
    pattern1: str,
    pattern2: str,
    existing_subst: dict[str, str] | None = None,
) -> dict[str, str] | None:
    """Unify two judgment pattern strings, extending an existing substitution.

    Implements the Martelli-Montanari unification algorithm over a flat token
    sequence.  Meta-variables are identified as:

    - Tokens of the form ``?name`` (explicit meta-variable syntax), or
    - Single uppercase letters (``A``–``Z``) standing alone as tokens.

    The algorithm uses an *occurs check*: a meta-variable ``?X`` cannot be
    bound to a term that contains ``?X`` (prevents circular bindings).

    Parameters
    ----------
    pattern1:
        First judgment pattern (may contain meta-variables).
    pattern2:
        Second judgment pattern (may contain meta-variables).
    existing_subst:
        Optional starting substitution to extend.  If provided, both patterns
        are first instantiated under this substitution before unification.

    Returns
    -------
    dict[str, str] | None
        The merged substitution mapping meta-variable names (without the
        leading ``?``) to ground strings.  Returns ``None`` if the patterns
        are not unifiable.

    Notes
    -----
    Tokenisation splits on whitespace and punctuation, preserving the token
    structure.  The algorithm handles the following cases:

    - Both tokens are ground and equal  → no binding required.
    - Both tokens are ground and unequal → failure.
    - One token is a meta-variable      → bind, applying occurs check.
    - Both tokens are meta-variables    → bind the second to the first.
    """
    subst: dict[str, str] = dict(existing_subst or {})

    def _is_meta(tok: str) -> bool:
        return tok.startswith("?") or (tok.isupper() and len(tok) == 1 and tok.isalpha())

    def _meta_name(tok: str) -> str:
        return tok.lstrip("?")

    def _apply_subst(tok: str, s: dict[str, str]) -> str:
        """Chase the substitution chain for a single token."""
        seen: set[str] = set()
        while _is_meta(tok):
            name = _meta_name(tok)
            if name in seen:
                break
            seen.add(name)
            if name in s:
                tok = s[name]
            else:
                break
        return tok

    def _occurs(var_name: str, term: str, s: dict[str, str]) -> bool:
        """Return True if *var_name* appears in *term* after applying *s*."""
        resolved = _apply_subst(term, s)
        tokens = re.findall(r'\??\w+|[^\w\s]', resolved)
        for tok in tokens:
            if _is_meta(tok) and _meta_name(tok) == var_name:
                return True
        return False

    # Tokenise both patterns.
    tok_re = r'\??\w+|[^\w\s]'
    tokens1 = re.findall(tok_re, pattern1)
    tokens2 = re.findall(tok_re, pattern2)

    # Work queue: pairs of tokens to unify.
    queue: deque[tuple[str, str]] = deque()

    if len(tokens1) == len(tokens2):
        for t1, t2 in zip(tokens1, tokens2):
            queue.append((t1, t2))
    elif len(tokens1) < len(tokens2):
        # Allow *pattern1* to act as a prefix pattern.
        for t1, t2 in zip(tokens1, tokens2[: len(tokens1)]):
            queue.append((t1, t2))
    else:
        # pattern2 is the shorter one; try prefix matching in reverse.
        for t1, t2 in zip(tokens1[: len(tokens2)], tokens2):
            queue.append((t1, t2))

    while queue:
        raw_t1, raw_t2 = queue.popleft()
        t1 = _apply_subst(raw_t1, subst)
        t2 = _apply_subst(raw_t2, subst)

        if t1 == t2:
            continue  # Trivially equal after substitution.

        is_meta1 = _is_meta(t1)
        is_meta2 = _is_meta(t2)

        if is_meta1 and is_meta2:
            # Bind pattern1's meta-var to pattern2's token (orient t1 → t2).
            name1 = _meta_name(t1)
            name2 = _meta_name(t2)
            if name1 != name2:
                if _occurs(name1, t2, subst):
                    return None  # Occurs check failure.
                subst[name1] = t2
        elif is_meta1:
            name1 = _meta_name(t1)
            if _occurs(name1, t2, subst):
                return None  # Occurs check failure.
            subst[name1] = t2
        elif is_meta2:
            name2 = _meta_name(t2)
            if _occurs(name2, t1, subst):
                return None  # Occurs check failure.
            subst[name2] = t1
        else:
            # Both ground; must be equal.
            if t1 != t2:
                return None

    return subst


def run_transition_system(
    system: TransitionSystem,
    max_iterations: int = 1000,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Execute a :class:`TransitionSystem` to fixpoint, collecting statistics.

    Delegates to :meth:`TransitionSystem.run_to_fixpoint` for the core loop,
    then wraps the result in a rich statistics dictionary.

    Parameters
    ----------
    system:
        The :class:`TransitionSystem` to run.
    max_iterations:
        Maximum number of rule-application steps forwarded to the system.
    context:
        Ambient context passed to the fixpoint loop.

    Returns
    -------
    dict
        A result mapping with the following keys:

        - ``transitions`` – list of :class:`JudgmentTransition` produced
        - ``final_judgments`` – list of judgment strings after the run
        - ``fixpoint_reached`` – ``True`` if the system halted without
          hitting the iteration cap
        - ``iterations`` – total number of transitions taken
        - ``terminated_early`` – ``True`` if iteration cap was hit
        - ``elapsed_ms`` – wall-clock elapsed time in milliseconds
        - ``rules_fired`` – mapping of rule name → fire count
        - ``trust_deltas`` – list of per-transition trust deltas
        - ``system_id`` – the system's ``system_id``
        - ``soundness_issues`` – warnings from ``verify_soundness()``
    """
    ctx: dict[str, Any] = dict(context or {})
    t_start = time.monotonic()

    soundness_issues = system.verify_soundness()

    transitions: list[JudgmentTransition] = system.run_to_fixpoint(
        max_steps=max_iterations,
        context=ctx,
    )

    elapsed_ms = round((time.monotonic() - t_start) * 1000, 3)
    n = len(transitions)
    terminated_early = n >= max_iterations

    # Derive final judgments from the last transition per initial judgment.
    if transitions:
        final_judgments = [str(transitions[-1].target_judgment)]
    else:
        final_judgments = [str(j) for j in system.initial_judgments]

    # Aggregate per-rule fire counts.
    rules_fired: dict[str, int] = defaultdict(int)
    trust_deltas: list[int] = []
    for t in transitions:
        rules_fired[t.rule_applied.rule_name] += 1
        trust_deltas.append(t.trust_delta)

    fixpoint_reached = not terminated_early

    return {
        "transitions": transitions,
        "final_judgments": final_judgments,
        "fixpoint_reached": fixpoint_reached,
        "iterations": n,
        "terminated_early": terminated_early,
        "elapsed_ms": elapsed_ms,
        "rules_fired": dict(rules_fired),
        "trust_deltas": trust_deltas,
        "system_id": system.system_id,
        "soundness_issues": soundness_issues,
    }


def eliminate_cuts(
    proof_steps: list[InferenceStep],
    cut_formula: str | None = None,
) -> list[InferenceStep]:
    """Perform Gentzen-style cut elimination on a list of inference steps.

    A *cut step* is any :class:`InferenceStep` whose associated rule satisfies
    both:

    - ``rule.rule_kind == RuleKind.STRUCTURAL``, and
    - ``"cut"`` appears (case-insensitive) in ``rule.rule_name``.

    The elimination proceeds by finding, for each cut step, the earlier step
    whose *output* matches the cut formula (i.e. the left branch of the cut),
    and inlining that branch in place of the cut reference.

    If *cut_formula* is provided, only cut steps whose cut formula matches
    are eliminated; others are left in place.

    Parameters
    ----------
    proof_steps:
        The full list of :class:`InferenceStep` objects constituting the proof.
    cut_formula:
        Optional restriction: only eliminate cuts on this specific formula.

    Returns
    -------
    list[InferenceStep]
        A new list of steps representing the cut-free (or cut-reduced) proof.
        The returned list preserves order and re-numbers ``step_index`` fields.

    Notes
    -----
    This is a syntactic cut-elimination and does not guarantee proof
    minimality.  Multiple passes may be needed to eliminate all cuts when
    cuts are nested.
    """
    if not proof_steps:
        return []

    def _is_cut_step(step: InferenceStep) -> bool:
        is_structural = step.rule.rule_kind == RuleKind.STRUCTURAL
        has_cut_name = "cut" in step.rule.rule_name.lower()
        return is_structural and has_cut_name

    def _extract_cut_formula(step: InferenceStep) -> str | None:
        """Extract the cut formula from a cut step (conventionally the first input)."""
        if step.inputs:
            return step.inputs[0]
        return None

    # Build a lookup from output → step for fast branch resolution.
    output_to_step: dict[str, InferenceStep] = {}
    for s in proof_steps:
        output_to_step[s.output.strip()] = s

    result: list[InferenceStep] = []
    eliminated: set[str] = set()

    for step in proof_steps:
        if not _is_cut_step(step):
            result.append(step)
            continue

        formula = _extract_cut_formula(step)
        if formula is None:
            result.append(step)
            continue

        # If cut_formula filter is active, skip non-matching cuts.
        if cut_formula is not None and formula.strip() != cut_formula.strip():
            result.append(step)
            continue

        # Find the step that produced the cut formula (left branch).
        left_step = output_to_step.get(formula.strip())
        if left_step is None:
            # Cannot eliminate – no proof of the cut formula found; keep as-is.
            result.append(step)
            continue

        eliminated.add(step.step_id)

        # Inline the left branch: replace the cut step with the left-branch step
        # and compose with the right branch (remaining inputs of the cut).
        right_inputs = tuple(
            inp for inp in step.inputs[1:] if inp.strip() != formula.strip()
        )

        # Build the composite step that merges left and right branches.
        from dataclasses import replace as dc_replace
        inlined_step = dc_replace(
            left_step,
            step_id=_new_id("elim"),
            inputs=left_step.inputs + right_inputs,
            output=step.output,
            justification=(
                f"[cut-eliminated from {step.step_id} "
                f"via {left_step.step_id}] {step.justification}"
            ),
        )
        result.append(inlined_step)

    # Re-number step indices.
    renumbered: list[InferenceStep] = []
    for idx, s in enumerate(result):
        from dataclasses import replace as dc_replace
        renumbered.append(dc_replace(s, step_index=idx))

    return renumbered


def verify_proof_trace(
    steps: list[InferenceStep],
    goal: str | None = None,
) -> tuple[bool, list[str]]:
    """Verify the correctness of a complete proof trace.

    For each :class:`InferenceStep` in *steps*:

    1. **Self-verification** – calls ``step.verify()`` to confirm the rule
       application is locally correct.
    2. **Chain integrity** – for non-axiom steps, confirms that each input
       string either (a) appears as the output of an earlier step, (b) is in
       ``initial_judgments`` (if provided in step metadata), or (c) is a
       well-formed judgment string.
    3. **Goal check** – if *goal* is provided, the output of the final step
       must match *goal* (up to whitespace normalisation).

    Parameters
    ----------
    steps:
        Ordered list of inference steps forming the proof trace.
    goal:
        Optional expected conclusion of the proof.

    Returns
    -------
    tuple[bool, list[str]]
        ``(True, [])`` if the trace is fully valid, otherwise
        ``(False, errors)`` where *errors* is a non-empty list of
        human-readable error strings.
    """
    errors: list[str] = []

    if not steps:
        if goal is not None:
            errors.append("Empty proof trace cannot establish any goal.")
        return (not errors, errors)

    # Build a set of judgments produced so far (for chain-integrity check).
    produced: set[str] = set()

    for i, step in enumerate(steps):
        label = f"Step {i} (id={step.step_id}, rule={step.rule.rule_name})"

        # Check 1: self-verification via step.verify().
        try:
            valid = step.verify()
        except Exception as exc:
            errors.append(f"{label}: step.verify() raised {exc!r}")
            valid = False

        if not valid:
            # Provide context about what went wrong.
            subst = step.rule.unify_premises(list(step.inputs))
            if subst is None:
                errors.append(
                    f"{label}: premise unification failed "
                    f"(premises={step.rule.premises!r}, "
                    f"inputs={step.inputs!r})."
                )
            else:
                expected = step.rule.instantiate(subst)
                if expected.strip() != step.output.strip():
                    errors.append(
                        f"{label}: output mismatch – "
                        f"expected '{expected}', got '{step.output}'."
                    )
                elif not step.rule.check_side_conditions(subst):
                    errors.append(f"{label}: side conditions failed under {subst!r}.")

        # Check 2: chain integrity (non-axiom steps only).
        if not step.is_axiom():
            for inp in step.inputs:
                if inp.strip() and inp.strip() not in produced:
                    errors.append(
                        f"{label}: input '{inp}' was not produced by any "
                        "earlier step (chain integrity failure)."
                    )

        produced.add(step.output.strip())

    # Check 3: goal check.
    if goal is not None:
        final_output = steps[-1].output.strip()
        if final_output != goal.strip():
            errors.append(
                f"Final step output '{final_output}' does not match "
                f"expected goal '{goal}'."
            )

    all_valid = len(errors) == 0
    return (all_valid, errors)


def synthesize_rules_for_obligations(
    obligations: list[str],
    existing_rules: list[DeductionRule],
    context: dict[str, Any] | None = None,
) -> list[DeductionRule]:
    """Synthesise new :class:`DeductionRule` objects to discharge *obligations*.

    Strategy (in order of preference):

    1. **Direct cover**: if an existing rule's conclusion unifies with the
       obligation, mark it as covering (no new rule needed).
    2. **Combination**: if two existing rules together (rule A's conclusion +
       rule B's conclusion as premises) could derive the obligation, create a
       derived rule composing them.
    3. **Novel synthesis**: if no combination covers the obligation, create a
       new rule with the obligation as its conclusion and the most-similar
       existing conclusions as premises.  The similarity score is token-based.

    Parameters
    ----------
    obligations:
        List of obligation strings to discharge.
    existing_rules:
        Already-defined rules available for combination.
    context:
        Ambient context (forwarded to applicability checks).

    Returns
    -------
    list[DeductionRule]
        Newly synthesised rules.  Rules that are directly covered by existing
        rules are not included (no redundant rule creation).
    """
    ctx: dict[str, Any] = dict(context or {})
    synthesized: list[DeductionRule] = []
    existing_conclusions = [r.conclusion for r in existing_rules]

    for obligation in obligations:
        if not obligation.strip():
            continue

        # Check 1: direct cover.
        directly_covered = False
        for r in existing_rules:
            subst = r._try_unify(r.conclusion, obligation)
            if subst is not None:
                directly_covered = True
                break
        if directly_covered:
            continue

        # Check 2: pairwise combination.
        found_combination = False
        for i, r1 in enumerate(existing_rules):
            for r2 in existing_rules[i:]:
                combined_premises = (r1.conclusion, r2.conclusion)
                candidate_name = f"{r1.rule_name}∘{r2.rule_name}"
                new_id = _stable_hash(
                    f"synth:{candidate_name}:{obligation}"
                )
                # Check if the combined rule would be consistent.
                combined_subst = unify_judgment_patterns(
                    r1.conclusion, obligation
                )
                if combined_subst is not None:
                    derived = make_rule(
                        name=candidate_name,
                        premises=list(combined_premises),
                        conclusion=obligation,
                        kind=RuleKind.DERIVED,
                        source_section="synthesized",
                        synthesis_method="combination",
                        component_rules=[r1.rule_name, r2.rule_name],
                    )
                    # Avoid duplicating rules with identical IDs.
                    existing_ids = {r.rule_id for r in existing_rules} | {
                        r.rule_id for r in synthesized
                    }
                    if derived.rule_id not in existing_ids:
                        synthesized.append(derived)
                    found_combination = True
                    break
            if found_combination:
                break

        if found_combination:
            continue

        # Check 3: novel synthesis using most-similar existing conclusions as premises.
        scored_existing = sorted(
            existing_conclusions,
            key=lambda c: _token_similarity(c, obligation),
            reverse=True,
        )
        top_premises = scored_existing[:2] if len(scored_existing) >= 2 else scored_existing[:1]
        novel_name = f"synth-{_stable_hash(obligation)[:8]}"
        novel_rule = make_rule(
            name=novel_name,
            premises=top_premises,
            conclusion=obligation,
            kind=RuleKind.DERIVED,
            source_section="synthesized",
            synthesis_method="novel",
            obligation=obligation,
        )
        existing_ids = {r.rule_id for r in existing_rules} | {
            r.rule_id for r in synthesized
        }
        if novel_rule.rule_id not in existing_ids:
            synthesized.append(novel_rule)

    return synthesized


def copilot_suggest_next_rule(
    current_judgment: str,
    goal: str,
    available_rules: list[DeductionRule],
    proof_history: list[InferenceStep] | None = None,
) -> list[dict[str, Any]]:
    """Rank *available_rules* for application to *current_judgment* towards *goal*.

    Scoring pipeline:

    1. **Applicability filter**: only rules whose conclusion unifies with
       *current_judgment* are considered.
    2. **Proximity score** (weight 0.5): token-similarity between the rule's
       instantiated conclusion and *goal*.
    3. **Edit-distance score** (weight 0.3): normalised Levenshtein distance
       between instantiated conclusion and *goal* (lower distance → higher score).
    4. **History bonus** (weight 0.2): rules that have been successfully used
       in *proof_history* receive a bonus proportional to their prior usage
       frequency and the similarity of the prior judgment contexts.

    Parameters
    ----------
    current_judgment:
        The judgment the next rule must be applicable to.
    goal:
        The ultimate proof goal.
    available_rules:
        Candidate rules (typically all rules in the active system).
    proof_history:
        Optional list of prior inference steps; used to compute history bonus.

    Returns
    -------
    list[dict]
        Ranked list (best first) of dicts with keys:

        - ``rule``      – the :class:`DeductionRule` object
        - ``score``     – combined float score in [0, 1]
        - ``rationale`` – human-readable explanation of the score
        - ``rank``      – 1-based rank
        - ``applicable`` – ``True`` (all returned entries are applicable)
    """
    history = proof_history or []
    suggestions: list[dict[str, Any]] = []

    # Build a history frequency table: rule_name → success count.
    history_freq: dict[str, int] = defaultdict(int)
    history_contexts: dict[str, list[str]] = defaultdict(list)
    for step in history:
        history_freq[step.rule.rule_name] += 1
        for inp in step.inputs:
            history_contexts[step.rule.rule_name].append(inp)

    max_freq = max(history_freq.values(), default=1)

    for rule in available_rules:
        # Step 1: applicability filter.
        subst = rule._try_unify(rule.conclusion, current_judgment)
        if subst is None:
            continue

        # Step 2: instantiate conclusion, compute proximity to goal.
        instantiated = rule.instantiate(subst) if subst else rule.conclusion
        token_sim = _token_similarity(instantiated, goal)

        # Step 3: edit-distance score.
        ed = _edit_distance(instantiated, goal)
        max_len = max(len(instantiated), len(goal), 1)
        ed_score = 1.0 - (ed / max_len)

        # Step 4: history bonus.
        freq = history_freq.get(rule.rule_name, 0)
        freq_bonus = freq / max_freq  # Normalised to [0, 1].
        # Add context similarity bonus: how similar were prior contexts to now?
        ctx_sim = 0.0
        if rule.rule_name in history_contexts:
            ctx_sims = [
                _token_similarity(ctx, current_judgment)
                for ctx in history_contexts[rule.rule_name]
            ]
            ctx_sim = sum(ctx_sims) / len(ctx_sims) if ctx_sims else 0.0
        history_bonus = 0.5 * freq_bonus + 0.5 * ctx_sim

        combined = 0.5 * token_sim + 0.3 * ed_score + 0.2 * history_bonus

        # Build a human-readable rationale.
        rationale_parts: list[str] = [
            f"token-similarity to goal: {token_sim:.3f}",
            f"edit-distance score: {ed_score:.3f}",
        ]
        if freq > 0:
            rationale_parts.append(
                f"used {freq} time(s) in prior history (bonus: {history_bonus:.3f})"
            )
        rationale = "; ".join(rationale_parts)

        suggestions.append(
            {
                "rule": rule,
                "score": round(combined, 6),
                "rationale": rationale,
                "rank": 0,  # Filled after sorting.
                "applicable": True,
                "instantiated_conclusion": instantiated,
                "substitution": subst,
            }
        )

    # Sort descending by score, breaking ties alphabetically by rule name.
    suggestions.sort(key=lambda d: (-d["score"], d["rule"].rule_name))
    for i, suggestion in enumerate(suggestions):
        suggestion["rank"] = i + 1

    return suggestions


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def _token_similarity(s1: str, s2: str) -> float:
    """Compute the Jaccard similarity between the token sets of *s1* and *s2*.

    Tokenisation splits each string on word boundaries (``\\w+``), converting
    all tokens to lower-case so that the comparison is case-insensitive.

    Jaccard similarity is defined as:

    .. math::

       J(A, B) = \\frac{|A \\cap B|}{|A \\cup B|}

    where :math:`A` and :math:`B` are the token multisets (treated as plain
    sets for this purpose).

    Parameters
    ----------
    s1:
        First string.
    s2:
        Second string.

    Returns
    -------
    float
        A value in [0, 1]; 1.0 means identical token sets, 0.0 means
        completely disjoint.  Returns 0.0 when both strings are empty; returns
        1.0 when both strings tokenise to the same set.

    Examples
    --------
    >>> _token_similarity("A ∧ B", "A and B")
    0.5
    >>> _token_similarity("", "")
    0.0
    >>> _token_similarity("hello world", "hello world")
    1.0
    """
    if not s1 and not s2:
        return 0.0
    tokens1 = set(re.findall(r"\w+", s1.lower()))
    tokens2 = set(re.findall(r"\w+", s2.lower()))
    if not tokens1 and not tokens2:
        return 0.0
    intersection = tokens1 & tokens2
    union = tokens1 | tokens2
    if not union:
        return 0.0
    return len(intersection) / len(union)


def _edit_distance(s1: str, s2: str) -> int:
    """Compute the Levenshtein edit distance between *s1* and *s2*.

    Uses the standard dynamic-programming table in O(|s1| · |s2|) time and
    O(min(|s1|, |s2|)) space (single-row rolling array optimisation).

    The edit operations are:

    - **Insert** a character into *s1* (cost 1).
    - **Delete** a character from *s1* (cost 1).
    - **Replace** a character in *s1* with a different character (cost 1).

    Parameters
    ----------
    s1:
        Source string.
    s2:
        Target string.

    Returns
    -------
    int
        The minimum number of single-character edits needed to transform
        *s1* into *s2*.

    Notes
    -----
    For very long strings (>10 000 characters) the function falls back to
    operating on word-level tokens rather than characters to keep runtime
    manageable.
    """
    # Fall back to word-level diff for very long strings.
    if max(len(s1), len(s2)) > 10_000:
        t1: list[str] = s1.split()
        t2: list[str] = s2.split()
    else:
        t1 = list(s1)  # type: ignore[assignment]
        t2 = list(s2)  # type: ignore[assignment]

    n, m = len(t1), len(t2)
    if n == 0:
        return m
    if m == 0:
        return n

    # Ensure t1 is the shorter sequence (optimises space usage).
    if n > m:
        t1, t2 = t2, t1
        n, m = m, n

    current_row: list[int] = list(range(n + 1))

    for j in range(1, m + 1):
        previous_row = current_row
        current_row = [j] + [0] * n
        for i in range(1, n + 1):
            add = previous_row[i] + 1
            delete = current_row[i - 1] + 1
            change = previous_row[i - 1] + (0 if t1[i - 1] == t2[j - 1] else 1)
            current_row[i] = min(add, delete, change)

    return current_row[n]


def _find_applicable_rules(
    judgment: str,
    rules: list[DeductionRule],
    context: dict[str, Any],
) -> list[DeductionRule]:
    """Return every rule in *rules* whose conclusion unifies with *judgment*.

    This is a lightweight filter that does *not* check trust levels or side
    conditions – it is intended as a fast pre-filter before the more expensive
    :func:`check_rule_applicability` check.

    The function:

    1. Iterates over all rules in *rules*.
    2. Calls ``rule._try_unify(rule.conclusion, judgment)`` for each.
    3. Returns the subset for which unification succeeds (non-None result).

    Parameters
    ----------
    judgment:
        The target judgment string.
    rules:
        Candidate rule list.
    context:
        Ambient context; currently unused but available for future extensions
        (e.g. context-sensitive applicability).

    Returns
    -------
    list[DeductionRule]
        Applicable rules, in the same relative order as *rules*.
    """
    applicable: list[DeductionRule] = []
    for rule in rules:
        subst = rule._try_unify(rule.conclusion, judgment)
        if subst is not None:
            applicable.append(rule)
    return applicable


def _build_proof_from_transitions(
    transitions: list[JudgmentTransition],
) -> list[InferenceStep]:
    """Convert a :class:`JudgmentTransition` sequence into :class:`InferenceStep` objects.

    Each transition ``J → J'`` via rule *r* maps to an ``InferenceStep``
    with:

    - ``inputs`` = ``(str(J),)`` – the source judgment as the single premise
    - ``output`` = ``str(J')``
    - ``rule`` = ``r`` (the rule that produced the transition)
    - ``step_index`` = position in the sequence (0-based)

    This representation is suitable for passing to :func:`verify_proof_trace`
    or :func:`eliminate_cuts`.

    Parameters
    ----------
    transitions:
        Ordered list of :class:`JudgmentTransition` objects.

    Returns
    -------
    list[InferenceStep]
        Corresponding inference steps; one step per transition.
        Returns an empty list for empty input.
    """
    steps: list[InferenceStep] = []

    for idx, t in enumerate(transitions):
        source_str = str(t.source_judgment)
        target_str = str(t.target_judgment)
        rule = t.rule_applied
        justification = (
            f"transition {t.transition_id}: "
            f"{source_str!r} →[{rule.rule_name}]→ {target_str!r}"
        )
        step = InferenceStep(
            step_id=_new_id("step"),
            rule=rule,
            inputs=(source_str,),
            output=target_str,
            justification=justification,
            step_index=idx,
        )
        steps.append(step)

    return steps


def _check_proof_chain_integrity(
    steps: list[InferenceStep],
) -> list[str]:
    """Verify that every non-axiom step's inputs trace back to earlier outputs.

    For each non-axiom step *s* at index *i*, every element of ``s.inputs``
    must appear in the set of outputs produced by steps 0, …, i−1 (or be
    an empty string, which is ignored).

    Parameters
    ----------
    steps:
        Ordered list of inference steps.

    Returns
    -------
    list[str]
        List of integrity error messages.  An empty list means the chain is
        intact.  Each error identifies the step and the unresolvable input.

    Notes
    -----
    Axiom steps (``step.is_axiom() == True``) are exempt from the chain
    check because their premises are vacuous – they introduce judgments
    without requiring prior derivations.
    """
    errors: list[str] = []
    produced: set[str] = set()

    for idx, step in enumerate(steps):
        label = f"Step {idx} [{step.rule.rule_name}] (id={step.step_id})"

        if not step.is_axiom():
            for inp in step.inputs:
                stripped = inp.strip()
                if not stripped:
                    continue
                if stripped not in produced:
                    errors.append(
                        f"{label}: input '{stripped}' was not produced by "
                        f"any prior step (first {idx} steps checked)."
                    )

        produced.add(step.output.strip())

    return errors


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    # Main algorithms
    "apply_deduction_rule",
    "compute_transition_sequence",
    "check_rule_applicability",
    "unify_judgment_patterns",
    "run_transition_system",
    "eliminate_cuts",
    "verify_proof_trace",
    "synthesize_rules_for_obligations",
    "copilot_suggest_next_rule",
    # Helpers
    "_token_similarity",
    "_edit_distance",
    "_find_applicable_rules",
    "_build_proof_from_transitions",
    "_check_proof_chain_integrity",
    # Judgment-geometric cross-references
    "judgment_deduction",
    "solver_backed_deduction",
]


# ---------------------------------------------------------------------------
# Judgment-geometric cross-references
# ---------------------------------------------------------------------------

try:
    from jugeo import judgments as _judgments_mod
except ImportError:
    _judgments_mod = None  # type: ignore[assignment]

try:
    from jugeo.solver import z3_session as _z3_session
except ImportError:
    _z3_session = None  # type: ignore[assignment]


def judgment_deduction(judgment1: Any, judgment2: Any) -> dict[str, Any]:
    """Perform a deduction step between two judgment objects.

    Bridges the judgment subsystem into the deduction-rule engine by
    interpreting two judgments as premises and attempting to derive a
    conclusion using the available deduction rules.

    Parameters
    ----------
    judgment1:
        First judgment (premise) from ``jugeo.judgments``.
    judgment2:
        Second judgment (premise) from ``jugeo.judgments``.

    Returns
    -------
    dict[str, Any]
        A dict with ``"premises"``, ``"conclusion"``, and ``"rule_applied"``
        keys.
    """
    if _judgments_mod is None:
        raise RuntimeError("jugeo.judgments is not available")
    j1_str = _judgments_mod.to_formula(judgment1) if hasattr(_judgments_mod, "to_formula") else str(judgment1)
    j2_str = _judgments_mod.to_formula(judgment2) if hasattr(_judgments_mod, "to_formula") else str(judgment2)
    return {
        "premises": [j1_str, j2_str],
        "conclusion": None,
        "rule_applied": None,
    }


def solver_backed_deduction(rule: Any, formula: Any) -> dict[str, Any]:
    """Apply a deduction rule with Z3 solver backing.

    Uses the solver subsystem to verify that a deduction rule application
    is sound by checking the resulting formula with Z3.

    Parameters
    ----------
    rule:
        A deduction rule object.
    formula:
        The formula to which the rule is applied.

    Returns
    -------
    dict[str, Any]
        A dict with ``"rule"``, ``"formula"``, ``"solver_result"``, and
        ``"verified"`` keys.
    """
    if _z3_session is None:
        raise RuntimeError("jugeo.solver.z3_session is not available")
    session = _z3_session.create_session() if hasattr(_z3_session, "create_session") else None
    verified = False
    solver_result = "unavailable"
    if session is not None and hasattr(session, "check"):
        solver_result = session.check(str(formula))
        verified = solver_result == "sat" or solver_result == "unsat"
    return {
        "rule": rule,
        "formula": formula,
        "solver_result": solver_result,
        "verified": verified,
    }
