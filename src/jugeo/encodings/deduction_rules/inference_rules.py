r"""Inference-rule framework for JuGeo — ``theory2.tex`` Chapter 33, §33.1.

This module implements the foundational infrastructure for representing,
instantiating, and applying inference rules in the JuGeo deduction system.

An inference rule is a schema

.. math::

   \frac{\Phi_1 \quad \Phi_2 \quad \cdots \quad \Phi_n}{\Psi}
         \;[\text{sc}_1, \ldots, \text{sc}_k]

where each :math:`\Phi_i` is a *premise schema* containing meta-variables
(written :math:`?X`, :math:`?A`, etc.), :math:`\Psi` is the *conclusion
schema*, and the :math:`\text{sc}_j` are *side conditions* — Boolean
expressions constraining the meta-variable instantiation.

Architecture
------------
- :class:`RuleSchema`              – abstract base for all rule schemas
- :class:`PremiseSet`              – ordered collection of premise patterns
- :class:`ConclusionForm`          – conclusion pattern with meta-var tracking
- :class:`SideConditionEvaluator`  – evaluates Boolean side conditions
- :class:`UnificationEngine`       – full first-order unification
- :class:`CopilotRuleSuggester`    – Copilot-assisted rule completion

Theory alignment
----------------
§33.1 specifies that every primitive rule must satisfy the *subformula
property*: every formula in a premise is a sub-formula of the conclusion
or of an already-discharged hypothesis.  This module encodes that check
in :meth:`RuleSchema.check_subformula_property`.
"""

from __future__ import annotations

import hashlib
import re
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any, Callable, Iterable, Mapping, Sequence

# ── External jugeo imports (graceful degradation) ────────────────────────────

try:
    from jugeo.solver.z3_session import Z3Session, Z3Formula, Z3Encoder, Z3Result
except Exception:  # pragma: no cover
    class Z3Session:  # type: ignore[no-redef]
        """Stub: jugeo.solver.z3_session not available."""

    class Z3Formula:  # type: ignore[no-redef]
        """Stub: jugeo.solver.z3_session not available."""

    class Z3Encoder:  # type: ignore[no-redef]
        """Stub: jugeo.solver.z3_session not available."""

    class Z3Result:  # type: ignore[no-redef]
        """Stub: jugeo.solver.z3_session not available."""

try:
    from jugeo.solver.reconstruction import ModelReconstruction
except Exception:  # pragma: no cover
    class ModelReconstruction:  # type: ignore[no-redef]
        """Stub: jugeo.solver.reconstruction not available."""

try:
    from jugeo.judgments.judgment_terms import JudgmentTerm
except Exception:  # pragma: no cover
    class JudgmentTerm:  # type: ignore[no-redef]
        """Stub: jugeo.judgments.judgment_terms not available."""

try:
    from jugeo.evidence.trust import TrustAlgebra, TrustLevel
except Exception:  # pragma: no cover
    class TrustAlgebra:  # type: ignore[no-redef]
        """Stub: jugeo.evidence.trust not available."""

    class TrustLevel:  # type: ignore[no-redef]
        """Stub: jugeo.evidence.trust not available."""

try:
    from jugeo.encodings.deduction_rules.models import (
        DeductionRule, RuleKind, ApplicationResult,
        make_rule, make_axiom_rule, _new_id, _stable_hash, _now_iso,
    )
except Exception:
    pass

# ── Local fallback stubs (used when models.py is not importable) ─────────────

if "RuleKind" not in globals():  # pragma: no cover
    class RuleKind(str, Enum):  # type: ignore[no-redef]
        """Minimal RuleKind stub."""
        STRUCTURAL = "structural"
        SEMANTIC = "semantic"
        AXIOM = "axiom"
        DERIVED = "derived"
        LOGICAL = "logical"
        EQUALITY = "equality"
        MODAL = "modal"
        CUSTOM = "custom"

if "ApplicationResult" not in globals():  # pragma: no cover
    class ApplicationResult(str, Enum):  # type: ignore[no-redef]
        """Minimal ApplicationResult stub."""
        APPLIED = "applied"
        INAPPLICABLE = "inapplicable"
        SIDE_CONDITION_FAILURE = "side-condition-failure"
        UNIFICATION_FAILURE = "unification-failure"
        ERROR = "error"

if "DeductionRule" not in globals():  # pragma: no cover
    @dataclass
    class DeductionRule:  # type: ignore[no-redef]
        """Minimal DeductionRule stub."""
        rule_id: str = field(default_factory=lambda: f"rule-{uuid.uuid4().hex[:8]}")
        rule_name: str = ""
        premises: tuple[str, ...] = field(default_factory=tuple)
        conclusion: str = ""
        side_conditions: dict[str, Any] = field(default_factory=dict)
        rule_kind: Any = None
        trust_required: Any = None
        metadata: dict[str, Any] = field(default_factory=dict)

if "_new_id" not in globals():  # pragma: no cover
    def _new_id(prefix: str = "id") -> str:  # type: ignore[misc]
        return f"{prefix}-{uuid.uuid4().hex[:12]}"

if "_now_iso" not in globals():  # pragma: no cover
    def _now_iso() -> str:  # type: ignore[misc]
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

if "_stable_hash" not in globals():  # pragma: no cover
    def _stable_hash(s: str) -> str:  # type: ignore[misc]
        return hashlib.sha256(s.encode()).hexdigest()[:16]

if "make_rule" not in globals():  # pragma: no cover
    def make_rule(  # type: ignore[misc]
        name: str,
        premises: Sequence[str],
        conclusion: str,
        kind: Any = None,
        side_conditions: dict[str, Any] | None = None,
        **metadata: Any,
    ) -> "DeductionRule":
        return DeductionRule(
            rule_id=_stable_hash(f"rule:{name}:{conclusion}"),
            rule_name=name,
            premises=tuple(premises),
            conclusion=conclusion,
            side_conditions=side_conditions or {},
            rule_kind=kind,
            metadata=dict(metadata),
        )

if "make_axiom_rule" not in globals():  # pragma: no cover
    def make_axiom_rule(name: str, conclusion: str, **metadata: Any) -> "DeductionRule":  # type: ignore[misc]
        return make_rule(name, [], conclusion, kind=RuleKind.AXIOM, **metadata)

# ── Module-level helpers ──────────────────────────────────────────────────────

_META_VAR_RE = re.compile(r'\?[A-Za-z_][A-Za-z0-9_]*')
_UPPER_RE = re.compile(r'^[A-Z][A-Za-z0-9_]*$')


def _tokenize(pattern: str) -> list[str]:
    """Tokenize *pattern* into a list of whitespace-separated tokens.

    Parentheses are treated as separate tokens so that ``(P Q)`` becomes
    ``['(', 'P', 'Q', ')']``.

    Parameters
    ----------
    pattern:
        A pattern string such as ``"?X -> ?Y"`` or ``"(?A /\\ ?B)"``

    Returns
    -------
    list[str]
        Non-empty list of token strings.
    """
    # Insert spaces around parens/brackets so they split cleanly
    spaced = re.sub(r'([()\[\]])', r' \1 ', pattern)
    return [t for t in spaced.split() if t]


def _apply_subst_to_pattern(pattern: str, subst: Mapping[str, str]) -> str:
    """Replace every meta-variable occurrence in *pattern* with its binding.

    Replacement is done longest-first to avoid partial substitution (e.g.
    ``?AB`` must not be partially replaced by a binding for ``?A``).

    Parameters
    ----------
    pattern:
        Source pattern possibly containing ``?VarName`` tokens.
    subst:
        Meta-variable bindings, keys may or may not include the leading ``?``.

    Returns
    -------
    str
        The pattern with all meta-variables substituted.
    """
    result = pattern
    for var in sorted(subst.keys(), key=len, reverse=True):
        val = str(subst[var])
        # Replace exact occurrences (whole-word) of the variable
        result = re.sub(r'(?<!\w)' + re.escape(var) + r'(?!\w)', val, result)
    return result


def _extract_meta_vars_from(pattern: str) -> frozenset[str]:
    """Scan *pattern* and return all meta-variable names (``?X`` style).

    Parameters
    ----------
    pattern:
        Pattern string.

    Returns
    -------
    frozenset[str]
        All ``?Name`` tokens found in *pattern*.
    """
    return frozenset(_META_VAR_RE.findall(pattern))


def _token_overlap_score(s1: str, s2: str) -> float:
    """Compute a simple Jaccard token-overlap score between two strings.

    Parameters
    ----------
    s1, s2:
        Two strings to compare.

    Returns
    -------
    float
        Value in ``[0.0, 1.0]`` — 1.0 means identical token sets.
    """
    t1 = set(_tokenize(s1))
    t2 = set(_tokenize(s2))
    if not t1 and not t2:
        return 1.0
    intersection = len(t1 & t2)
    union = len(t1 | t2)
    return intersection / union if union else 0.0


# ── UnificationEngine ─────────────────────────────────────────────────────────

@dataclass(slots=True)
class UnificationEngine:
    """First-order unification engine for string-based rule patterns.

    Implements Robinson's unification algorithm over tokenised pattern strings.
    A *meta-variable* is any token that begins with ``?`` (e.g. ``?X``,
    ``?Phi``) or consists of a single uppercase letter (e.g. ``A``, ``P``).

    The engine is stateful: it tracks the number of unification steps taken
    via :attr:`_step_count` so that runaway recursion can be detected.

    Parameters
    ----------
    occurs_check:
        If ``True`` (default), the occurs-check is performed to prevent
        cyclic substitutions.
    max_steps:
        Maximum number of token-pair comparisons before aborting.
    _step_count:
        Internal counter — reset with :meth:`reset`.
    """

    occurs_check: bool = True
    max_steps: int = 10_000
    _step_count: int = 0

    # ── Public entry point ────────────────────────────────────────────────

    def unify(self, pattern: str, target: str) -> dict[str, str] | None:
        """Unify *pattern* against *target*, returning a substitution or ``None``.

        The pattern and target are tokenised with :func:`_tokenize`.  Each
        token is then unified independently; a meta-variable binds to
        exactly one target token.

        Parameters
        ----------
        pattern:
            Pattern string that may contain meta-variables.
        target:
            Concrete (or partly-concrete) string to match against.

        Returns
        -------
        dict[str, str] | None
            A substitution mapping meta-variable names to their matched
            strings, or ``None`` if unification fails.
        """
        self.reset()
        ps = _tokenize(pattern)
        ts = _tokenize(target)
        return self._unify_tokens(ps, ts, {})

    def _unify_tokens(
        self,
        ps: list[str],
        ts: list[str],
        subst: dict[str, str],
    ) -> dict[str, str] | None:
        """Recursively unify token lists *ps* and *ts* under *subst*.

        Parameters
        ----------
        ps:
            Pattern token list.
        ts:
            Target token list.
        subst:
            Current substitution accumulated so far.

        Returns
        -------
        dict[str, str] | None
            Extended substitution or ``None`` on failure.
        """
        if len(ps) != len(ts):
            return None
        result: dict[str, str] = dict(subst)
        for p_tok, t_tok in zip(ps, ts):
            self._step_count += 1
            if self._step_count > self.max_steps:
                return None  # Step limit exceeded — abort

            p_grounded = self._apply_subst(p_tok, result)
            t_grounded = self._apply_subst(t_tok, result)

            if p_grounded == t_grounded:
                continue  # Already unified

            if self._is_meta_var(p_grounded):
                if self.occurs_check and self._occurs(p_grounded, t_grounded, result):
                    return None
                result[p_grounded] = t_grounded
            elif self._is_meta_var(t_grounded):
                # Symmetric: target token is a meta-var
                if self.occurs_check and self._occurs(t_grounded, p_grounded, result):
                    return None
                result[t_grounded] = p_grounded
            else:
                return None  # Clash between two distinct ground tokens
        return result

    def _is_meta_var(self, token: str) -> bool:
        """Return ``True`` if *token* should be treated as a meta-variable.

        A token is a meta-variable when it starts with ``?`` followed by an
        identifier character, or is a single uppercase ASCII letter.

        Parameters
        ----------
        token:
            A single token string.
        """
        if _META_VAR_RE.fullmatch(token):
            return True
        if len(token) == 1 and token.isupper():
            return True
        return False

    def _occurs(self, var: str, term: str, subst: dict[str, str]) -> bool:
        """Return ``True`` if *var* occurs in *term* after applying *subst*.

        This is the standard occurs-check that prevents cyclic unifiers.

        Parameters
        ----------
        var:
            The meta-variable to look for.
        term:
            The term in which to search.
        subst:
            Current partial substitution.
        """
        expanded = self._apply_subst(term, subst)
        return var in _tokenize(expanded)

    def _apply_subst(self, term: str, subst: dict[str, str]) -> str:
        """Apply *subst* to a single token *term*.

        If *term* is bound in *subst*, return the binding; otherwise return
        *term* unchanged.

        Parameters
        ----------
        term:
            A single token or short expression.
        subst:
            Current substitution.
        """
        return subst.get(term, term)

    # ── Sequence unification ──────────────────────────────────────────────

    def unify_sequence(
        self,
        patterns: Sequence[str],
        targets: Sequence[str],
    ) -> dict[str, str] | None:
        """Unify corresponding pairs of pattern / target strings.

        Processes pairs left to right, threading the accumulated substitution
        through each call to :meth:`unify`.

        Parameters
        ----------
        patterns:
            Ordered pattern strings.
        targets:
            Ordered target strings.  Must have the same length as *patterns*.

        Returns
        -------
        dict[str, str] | None
            A single merged substitution, or ``None`` if any pair fails.
        """
        if len(patterns) != len(targets):
            return None
        accumulated: dict[str, str] = {}
        for pat, tgt in zip(patterns, targets):
            self.reset()
            # Ground both strings with accumulated substitution first
            grounded_pat = _apply_subst_to_pattern(pat, accumulated)
            partial = self._unify_tokens(
                _tokenize(grounded_pat), _tokenize(tgt), accumulated
            )
            if partial is None:
                return None
            accumulated = partial
        return accumulated

    def compose_substitutions(
        self,
        s1: dict[str, str],
        s2: dict[str, str],
    ) -> dict[str, str] | None:
        """Compose substitutions *s1* and *s2* (apply *s1* after *s2*).

        The result maps each variable to the value it would have after first
        applying *s2* then *s1*.  Returns ``None`` if the two substitutions
        are inconsistent on their common domain.

        Parameters
        ----------
        s1, s2:
            Substitution dictionaries to compose.
        """
        result: dict[str, str] = {}
        # For each binding in s2, apply s1 to its value
        for var, val in s2.items():
            new_val = _apply_subst_to_pattern(val, s1)
            result[var] = new_val
        # Add bindings from s1 not already covered
        for var, val in s1.items():
            if var in result:
                if result[var] != val:
                    return None  # Inconsistent
            else:
                result[var] = val
        return result

    def most_general_unifier(self, p1: str, p2: str) -> dict[str, str] | None:
        """Compute the most-general unifier (MGU) of two pattern strings.

        The MGU is the *least* substitution (in the subsumption order) that
        makes the two patterns syntactically identical after application.

        Parameters
        ----------
        p1, p2:
            Pattern strings, both may contain meta-variables.
        """
        self.reset()
        toks1 = _tokenize(p1)
        toks2 = _tokenize(p2)
        return self._unify_tokens(toks1, toks2, {})

    def reset(self) -> None:
        """Reset the internal step counter to zero."""
        self._step_count = 0


# ── PremiseSet ────────────────────────────────────────────────────────────────

@dataclass(slots=True)
class PremiseSet:
    """An ordered collection of premise patterns for an inference rule.

    Premise patterns may contain meta-variables.  Ordering constraints
    record which premise must be discharged before another (a DAG over
    indices), enabling the validator to flag out-of-order applications.

    Parameters
    ----------
    patterns:
        Tuple of premise-schema strings.
    labels:
        Optional human-readable label for each premise index.
    ordering_constraints:
        List of ``(i, j)`` pairs meaning *premise i must precede premise j*.
    """

    patterns: tuple[str, ...]
    labels: dict[int, str] = field(default_factory=dict)
    ordering_constraints: list[tuple[int, int]] = field(default_factory=list)

    def __len__(self) -> int:
        """Return the number of premise patterns."""
        return len(self.patterns)

    def __iter__(self):  # type: ignore[override]
        """Iterate over premise patterns in order."""
        return iter(self.patterns)

    def unify_all(
        self,
        candidates: Sequence[str],
        engine: UnificationEngine | None = None,
    ) -> dict[str, str] | None:
        """Attempt to unify every premise pattern against *candidates*.

        Uses :class:`UnificationEngine` to unify each pattern against the
        corresponding candidate, threading substitutions.

        Parameters
        ----------
        candidates:
            Concrete judgment strings to unify against.  Must have the
            same length as :attr:`patterns`.
        engine:
            Optional pre-configured engine; a fresh one is created if omitted.

        Returns
        -------
        dict[str, str] | None
            A single merged substitution, or ``None`` if any pattern fails.
        """
        if len(candidates) != len(self.patterns):
            return None
        eng = engine or UnificationEngine()
        return eng.unify_sequence(list(self.patterns), list(candidates))

    def permutations_allowed(self) -> bool:
        """Return ``True`` if no ordering constraints restrict premise order.

        When there are no :attr:`ordering_constraints`, callers may discharge
        premises in any order.
        """
        return len(self.ordering_constraints) == 0

    def add_constraint(self, i: int, j: int) -> "PremiseSet":
        """Return a copy of *self* with the additional constraint i ≺ j.

        Parameters
        ----------
        i:
            Index of the premise that must be discharged first.
        j:
            Index of the premise that must be discharged after *i*.
        """
        new_constraints = list(self.ordering_constraints) + [(i, j)]
        return PremiseSet(
            patterns=self.patterns,
            labels=dict(self.labels),
            ordering_constraints=new_constraints,
        )

    def label_of(self, index: int) -> str:
        """Return the human-readable label for premise *index*.

        Falls back to a generated label ``"premise_N"`` if not explicitly set.

        Parameters
        ----------
        index:
            Zero-based premise index.
        """
        return self.labels.get(index, f"premise_{index}")

    def to_list(self) -> list[str]:
        """Return the premise patterns as a plain list."""
        return list(self.patterns)

    @staticmethod
    def merge_substitutions(
        s1: dict[str, str],
        s2: dict[str, str],
    ) -> dict[str, str] | None:
        """Merge two substitution dictionaries, checking for conflicts.

        Two substitutions are *consistent* if every variable that appears in
        both maps to the same value.

        Parameters
        ----------
        s1, s2:
            Substitution dicts to merge.

        Returns
        -------
        dict[str, str] | None
            Merged substitution, or ``None`` if a conflict is detected.
        """
        merged = dict(s1)
        for var, val in s2.items():
            if var in merged:
                if merged[var] != val:
                    return None  # Conflict
            else:
                merged[var] = val
        return merged


# ── ConclusionForm ────────────────────────────────────────────────────────────

@dataclass(frozen=True, slots=True)
class ConclusionForm:
    """An immutable conclusion pattern with meta-variable and sort information.

    Wraps a single pattern string and pre-computes the sets of meta-variables
    and free variables present.  Sort annotations (``?X : sort``) allow the
    evaluator to perform simple sort checking during instantiation.

    Parameters
    ----------
    pattern:
        Conclusion schema string, e.g. ``"?A \\/ ?B"``.
    meta_vars:
        Frozen set of meta-variable names found in *pattern*.
    free_vars:
        Frozen set of free (non-meta) variable names.
    sort_annotations:
        Mapping from meta-variable name to its declared sort.
    """

    pattern: str
    meta_vars: frozenset[str]
    free_vars: frozenset[str]
    sort_annotations: dict[str, str]

    def instantiate(self, substitution: Mapping[str, str]) -> str:
        """Apply *substitution* to produce a ground conclusion string.

        Parameters
        ----------
        substitution:
            Mapping from meta-variable names (``?X`` style) to their values.

        Returns
        -------
        str
            The instantiated conclusion.
        """
        return _apply_subst_to_pattern(self.pattern, substitution)

    def unify(self, target: str) -> dict[str, str] | None:
        """Unify :attr:`pattern` against *target*.

        Delegates to :class:`UnificationEngine` for the actual unification.

        Parameters
        ----------
        target:
            Concrete (or partially concrete) judgment string.

        Returns
        -------
        dict[str, str] | None
            A substitution if unification succeeds, otherwise ``None``.
        """
        engine = UnificationEngine()
        return engine.unify(self.pattern, target)

    def is_ground(self, substitution: Mapping[str, str]) -> bool:
        """Return ``True`` if every meta-variable in this form is bound.

        Parameters
        ----------
        substitution:
            Current substitution.
        """
        return all(var in substitution for var in self.meta_vars)

    def free_meta_variables(
        self, substitution: Mapping[str, str]
    ) -> frozenset[str]:
        """Return meta-variables that are *not* bound in *substitution*.

        Parameters
        ----------
        substitution:
            Current partial substitution.
        """
        return frozenset(v for v in self.meta_vars if v not in substitution)

    def sort_check(self, substitution: Mapping[str, str]) -> list[str]:
        """Check sort annotations against *substitution* values.

        Each annotation ``?X : sort`` is a lightweight assertion.  This
        method returns a list of violation messages (empty if all is well).

        Parameters
        ----------
        substitution:
            Substitution providing concrete values for meta-variables.

        Returns
        -------
        list[str]
            Violation messages, one per failed annotation.
        """
        violations: list[str] = []
        for var, declared_sort in self.sort_annotations.items():
            val = substitution.get(var)
            if val is None:
                violations.append(
                    f"sort_check: {var!r} declared as {declared_sort!r} "
                    "but not bound in substitution"
                )
                continue
            # Simple syntactic sort: the value must contain the sort keyword
            if declared_sort.lower() not in str(val).lower():
                violations.append(
                    f"sort_check: {var!r}={val!r} does not match "
                    f"declared sort {declared_sort!r}"
                )
        return violations

    def rename_meta_vars(self, prefix: str) -> "ConclusionForm":
        """Return a copy of *self* with every meta-variable prefixed.

        Useful when two schemas need to be unified without clashing on
        shared meta-variable names.

        Parameters
        ----------
        prefix:
            String to prepend (after the ``?``) to every meta-variable.

        Returns
        -------
        ConclusionForm
            Fresh form with renamed meta-variables.
        """
        rename_map: dict[str, str] = {}
        for var in self.meta_vars:
            bare = var.lstrip("?")
            rename_map[var] = f"?{prefix}{bare}"
        new_pattern = _apply_subst_to_pattern(self.pattern, rename_map)
        new_meta = frozenset(rename_map.get(v, v) for v in self.meta_vars)
        new_sort = {rename_map.get(k, k): v for k, v in self.sort_annotations.items()}
        return ConclusionForm(
            pattern=new_pattern,
            meta_vars=new_meta,
            free_vars=self.free_vars,
            sort_annotations=new_sort,
        )

    def __str__(self) -> str:
        """Return the pattern string."""
        return self.pattern


# ── SideConditionEvaluator ────────────────────────────────────────────────────

@dataclass(slots=True)
class SideConditionEvaluator:
    """Evaluates the side conditions attached to an inference rule.

    Side conditions may be:

    - Callable objects ``f(bindings) -> bool``
    - String expressions evaluated in a restricted namespace
    - Plain ``bool`` constants

    Parameters
    ----------
    conditions:
        Mapping from condition name to condition value.
    trusted_callables:
        Set of fully-qualified callable names that may be invoked during
        ``safe_eval``.
    max_depth:
        Maximum nesting depth for recursive condition evaluation.
    """

    conditions: dict[str, Any]
    trusted_callables: set[str] = field(default_factory=set)
    max_depth: int = 50

    # ── Evaluation ────────────────────────────────────────────────────────

    def evaluate(
        self, bindings: Mapping[str, Any]
    ) -> tuple[bool, list[str]]:
        """Evaluate all side conditions against *bindings*.

        Conditions are evaluated independently; evaluation continues even
        if one fails, so that all failures are reported at once.

        Parameters
        ----------
        bindings:
            Current meta-variable bindings.

        Returns
        -------
        tuple[bool, list[str]]
            ``(all_passed, failure_messages)`` — *all_passed* is ``True``
            only when every condition passes.
        """
        failures: list[str] = []
        for name, condition in self.conditions.items():
            try:
                passed = self.evaluate_one(name, condition, bindings)
            except Exception as exc:
                passed = False
                failures.append(f"{name}: exception during evaluation — {exc}")
                continue
            if not passed:
                failures.append(f"{name}: condition not satisfied")
        return (len(failures) == 0, failures)

    def evaluate_one(
        self,
        name: str,
        condition: Any,
        bindings: Mapping[str, Any],
    ) -> bool:
        """Evaluate a single *condition* with the given *bindings*.

        Dispatches to the appropriate evaluation strategy based on the
        type of *condition*.

        Parameters
        ----------
        name:
            Condition identifier (used in error messages).
        condition:
            The condition: ``bool``, ``str``, or callable.
        bindings:
            Current meta-variable bindings.
        """
        if isinstance(condition, bool):
            return condition
        if callable(condition):
            return bool(condition(dict(bindings)))
        if isinstance(condition, str):
            ns: dict[str, Any] = dict(bindings)
            return self.safe_eval(condition, ns)
        # Unknown condition type — treat as truthy if non-None
        return condition is not None

    def add_condition(self, name: str, condition: Any) -> None:
        """Register a new side condition.

        Parameters
        ----------
        name:
            Unique identifier for the condition.
        condition:
            Condition value (bool, callable, or expression string).
        """
        self.conditions[name] = condition

    def remove_condition(self, name: str) -> bool:
        """Remove the condition named *name*.

        Parameters
        ----------
        name:
            Condition to remove.

        Returns
        -------
        bool
            ``True`` if the condition was present and removed.
        """
        if name in self.conditions:
            del self.conditions[name]
            return True
        return False

    def list_conditions(self) -> list[str]:
        """Return a sorted list of condition names."""
        return sorted(self.conditions.keys())

    def safe_eval(self, expr: str, namespace: dict[str, Any]) -> bool:
        """Evaluate *expr* in a restricted namespace.

        Built-in dangerous names (``__import__``, ``exec``, ``eval``,
        ``open``, ``compile``) are removed from the evaluation namespace.

        Parameters
        ----------
        expr:
            Python expression string to evaluate.
        namespace:
            Variable bindings available during evaluation.

        Returns
        -------
        bool
            The Boolean result of evaluating *expr*.

        Raises
        ------
        ValueError
            If *expr* contains a forbidden construct.
        """
        forbidden = {"__import__", "exec", "eval", "open", "compile", "__builtins__"}
        for bad in forbidden:
            if bad in expr:
                raise ValueError(f"Forbidden construct in side condition: {bad!r}")
        safe_ns: dict[str, Any] = {
            k: v for k, v in namespace.items()
            if not str(k).startswith("__")
        }
        safe_ns["__builtins__"] = {}
        return bool(eval(expr, safe_ns))  # noqa: S307

    def explain_failure(
        self, name: str, bindings: Mapping[str, Any]
    ) -> str:
        """Produce a detailed explanation of why condition *name* failed.

        Parameters
        ----------
        name:
            Condition identifier.
        bindings:
            Bindings at the time of failure.

        Returns
        -------
        str
            Human-readable explanation.
        """
        condition = self.conditions.get(name)
        if condition is None:
            return f"Condition {name!r} not registered."
        bound_vars = ", ".join(f"{k}={v!r}" for k, v in bindings.items())
        cond_repr = repr(condition) if callable(condition) else str(condition)
        return (
            f"Side condition {name!r} FAILED.\n"
            f"  Condition: {cond_repr}\n"
            f"  Bindings:  {{{bound_vars}}}"
        )

    def clone_with(
        self, extra: Mapping[str, Any]
    ) -> "SideConditionEvaluator":
        """Return a copy of *self* with additional conditions from *extra*.

        Parameters
        ----------
        extra:
            New conditions to merge in.  Existing conditions are preserved;
            conflicting names are overwritten by *extra*.
        """
        merged = {**self.conditions, **extra}
        return SideConditionEvaluator(
            conditions=merged,
            trusted_callables=set(self.trusted_callables),
            max_depth=self.max_depth,
        )


# ── RuleSchema ────────────────────────────────────────────────────────────────

@dataclass(slots=True)
class RuleSchema:
    """An inference-rule schema parameterised by meta-variables.

    A schema abstracts over a family of concrete rules sharing the same
    logical structure.  Instantiating the schema (binding all meta-variables)
    produces a concrete :class:`~jugeo.encodings.deduction_rules.models.DeductionRule`.

    The schema is the primary object manipulated by the proof-search engine:
    it is matched against goals, its side conditions are checked, and — upon
    success — it is instantiated to record the actual rule firing.

    Formal definition (§33.1)
    ~~~~~~~~~~~~~~~~~~~~~~~~~~
    A schema :math:`\\mathcal{S}` consists of:

    - A set :math:`\\mathcal{M}` of meta-variables
    - An ordered tuple of *premise patterns* :math:`(P_1, \\ldots, P_n)`
    - A *conclusion pattern* :math:`C`
    - A set of *side conditions* over :math:`\\mathcal{M}`

    Parameters
    ----------
    schema_id:
        Stable unique identifier.
    schema_name:
        Human-readable name (e.g. ``"∧-intro"``).
    meta_variables:
        Set of meta-variable names present in the patterns.
    premise_patterns:
        Ordered premise schema strings.
    conclusion_pattern:
        Conclusion schema string.
    schema_kind:
        :class:`RuleKind` classification.
    subformula_check:
        Whether :meth:`check_subformula_property` is enforced on this schema.
    metadata:
        Free-form annotations.
    """

    schema_id: str
    schema_name: str
    meta_variables: frozenset[str]
    premise_patterns: tuple[str, ...]
    conclusion_pattern: str
    schema_kind: RuleKind
    subformula_check: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)

    # ── Instantiation ─────────────────────────────────────────────────────

    def instantiate(self, substitution: Mapping[str, str]) -> "DeductionRule":
        """Substitute meta-variables and return a concrete :class:`DeductionRule`.

        All meta-variable occurrences in premise and conclusion patterns are
        replaced by their bindings from *substitution*.  Unbound meta-variables
        are left in place (the rule will be partially instantiated).

        Parameters
        ----------
        substitution:
            Mapping from meta-variable name (e.g. ``"?X"``) to a concrete
            judgment string.

        Returns
        -------
        DeductionRule
            A concrete rule with the substitution applied.
        """
        new_premises = [
            _apply_subst_to_pattern(p, substitution)
            for p in self.premise_patterns
        ]
        new_conclusion = _apply_subst_to_pattern(
            self.conclusion_pattern, substitution
        )
        return make_rule(
            name=self.schema_name,
            premises=new_premises,
            conclusion=new_conclusion,
            kind=self.schema_kind,
            source_schema=self.schema_id,
            instantiation=dict(substitution),
            **{k: v for k, v in self.metadata.items()
               if isinstance(k, str)},
        )

    # ── Subformula property ───────────────────────────────────────────────

    def check_subformula_property(self) -> bool:
        """Verify that every non-meta token in premises appears in the conclusion.

        The *subformula property* (§33.1) ensures that no premise introduces
        connectives or constants that are not present in the conclusion.
        This is a necessary (but not sufficient) condition for cut-admissibility.

        Returns
        -------
        bool
            ``True`` if the property holds for all premises.
        """
        conclusion_tokens = set(_tokenize(self.conclusion_pattern))
        for premise in self.premise_patterns:
            for tok in _tokenize(premise):
                if tok in ("(", ")", "[", "]"):
                    continue
                if not _META_VAR_RE.fullmatch(tok) and tok not in conclusion_tokens:
                    return False
        return True

    # ── Meta-variable extraction ──────────────────────────────────────────

    def extract_meta_variables(self) -> frozenset[str]:
        """Scan all patterns and return the set of meta-variable names.

        Inspects both premise patterns and the conclusion pattern.

        Returns
        -------
        frozenset[str]
            All ``?Name`` tokens found across all patterns.
        """
        found: set[str] = set()
        for p in (*self.premise_patterns, self.conclusion_pattern):
            found.update(_extract_meta_vars_from(p))
        return frozenset(found)

    # ── Conclusion matching ───────────────────────────────────────────────

    def matches_conclusion(self, target: str) -> dict[str, str] | None:
        """Try to unify the conclusion pattern against *target*.

        Parameters
        ----------
        target:
            A concrete judgment string.

        Returns
        -------
        dict[str, str] | None
            A substitution if the conclusion pattern matches, otherwise ``None``.
        """
        engine = UnificationEngine()
        return engine.unify(self.conclusion_pattern, target)

    # ── Conversion ────────────────────────────────────────────────────────

    def to_deduction_rule(self, **kwargs: Any) -> "DeductionRule":
        """Convert to a partially-ground :class:`DeductionRule`.

        Meta-variables remain in the patterns; callers may pass keyword
        arguments that are forwarded to :func:`make_rule` as metadata.

        Parameters
        ----------
        **kwargs:
            Additional metadata for the resulting rule.
        """
        return make_rule(
            name=self.schema_name,
            premises=list(self.premise_patterns),
            conclusion=self.conclusion_pattern,
            kind=self.schema_kind,
            schema_id=self.schema_id,
            **kwargs,
        )

    # ── Utility ───────────────────────────────────────────────────────────

    def arity(self) -> int:
        """Return the number of premise patterns (the rule's arity)."""
        return len(self.premise_patterns)

    def is_axiom_schema(self) -> bool:
        """Return ``True`` if this schema has no premises (axiom schema)."""
        return len(self.premise_patterns) == 0

    def describe(self) -> str:
        """Return a multi-line human-readable description of this schema.

        Returns
        -------
        str
            A formatted string showing premises, conclusion, meta-variables,
            and side-condition count.
        """
        lines: list[str] = [
            f"Schema: {self.schema_name} [{self.schema_kind.value}]",
            f"  ID      : {self.schema_id}",
            f"  Meta-vars: {', '.join(sorted(self.meta_variables)) or '(none)'}",
        ]
        if self.premise_patterns:
            lines.append("  Premises:")
            for i, p in enumerate(self.premise_patterns, 1):
                lines.append(f"    [{i}] {p}")
        else:
            lines.append("  Premises: (axiom — no premises)")
        lines.append(f"  Conclusion: {self.conclusion_pattern}")
        sfp = self.check_subformula_property()
        lines.append(f"  Subformula property: {'✓' if sfp else '✗'}")
        return "\n".join(lines)

    def copilot_complete(self, partial: str) -> list[str]:
        """Suggest completions for *partial* by analogy with this schema.

        Uses the conclusion pattern as a template, substituting the tokens
        of *partial* to suggest plausible full conclusions.

        Parameters
        ----------
        partial:
            An incomplete judgment string.

        Returns
        -------
        list[str]
            Up to five suggested completions.
        """  # copilot - suggest completions
        engine = UnificationEngine()
        suggestions: list[str] = []
        # Try to unify partial against the conclusion to get a partial substitution
        subst = engine.unify(self.conclusion_pattern, partial)
        if subst is not None:
            # Instantiate with available bindings
            instantiated = _apply_subst_to_pattern(
                self.conclusion_pattern, subst
            )
            suggestions.append(instantiated)
        # Offer the raw conclusion pattern as a template
        if self.conclusion_pattern not in suggestions:
            suggestions.append(self.conclusion_pattern)
        # Offer each premise pattern as a possible premise to discharge
        for p in self.premise_patterns:
            candidate = f"discharge: {p}"
            if candidate not in suggestions:
                suggestions.append(candidate)
        return suggestions[:5]


# ── CopilotRuleSuggester ──────────────────────────────────────────────────────

@dataclass(slots=True)
class CopilotRuleSuggester:
    """Copilot-assisted rule suggestion for interactive proof search.

    Maintains a library of :class:`RuleSchema` objects and ranks them by
    their relevance to a given proof goal.  Feedback from the user is
    incorporated to improve future rankings.

    Parameters
    ----------
    rule_library:
        The known schemas that may be suggested.
    suggestion_history:
        Log of past suggestions and feedback.
    max_suggestions:
        Maximum number of schemas to return per query.
    """

    rule_library: list[RuleSchema]
    suggestion_history: list[dict[str, Any]] = field(default_factory=list)
    max_suggestions: int = 10

    # ── Suggestion ────────────────────────────────────────────────────────

    def suggest_for_goal(
        self,
        goal: str,
        context: Mapping[str, Any] | None = None,
    ) -> list[RuleSchema]:
        """Find schemas whose conclusions match *goal*.

        Schemas are scored with :meth:`_score_schema` and returned in
        descending score order.

        Parameters
        ----------
        goal:
            The target judgment string.
        context:
            Optional proof context (currently unused but reserved for
            future heuristics).

        Returns
        -------
        list[RuleSchema]
            Up to :attr:`max_suggestions` schemas, best first.
        """  # copilot - find schemas whose conclusions match goal
        ranked = self.rank_suggestions(goal, self.rule_library)
        result = [schema for schema, score in ranked if score > 0.0]
        # Log the suggestion event
        self.suggestion_history.append(
            {
                "goal": goal,
                "timestamp": _now_iso(),
                "n_suggested": len(result),
                "top_schema": result[0].schema_id if result else None,
            }
        )
        return result[: self.max_suggestions]

    def suggest_premises_for(self, conclusion: str) -> list[list[str]]:
        """Suggest plausible premise sets for deriving *conclusion*.

        Looks up schemas whose conclusion patterns match *conclusion* and
        returns their premise pattern lists.

        Parameters
        ----------
        conclusion:
            Target conclusion string.

        Returns
        -------
        list[list[str]]
            One list of premise strings per matching schema.
        """  # copilot - suggest plausible premise sets
        results: list[list[str]] = []
        engine = UnificationEngine()
        for schema in self.rule_library:
            if engine.unify(schema.conclusion_pattern, conclusion) is not None:
                results.append(list(schema.premise_patterns))
        return results

    def rank_suggestions(
        self,
        goal: str,
        candidates: list[RuleSchema],
    ) -> list[tuple[RuleSchema, float]]:
        """Rank *candidates* by relevance to *goal*.

        Scoring combines token-overlap similarity (from :meth:`_score_schema`)
        with a feedback bonus for schemas previously rated useful.

        Parameters
        ----------
        goal:
            Goal judgment string.
        candidates:
            Schemas to rank.

        Returns
        -------
        list[tuple[RuleSchema, float]]
            Pairs of ``(schema, score)``, sorted descending.
        """
        # Count positive feedback per schema
        feedback_counts: dict[str, int] = defaultdict(int)
        for entry in self.suggestion_history:
            if entry.get("was_useful") and "schema_id" in entry:
                feedback_counts[entry["schema_id"]] += 1

        scored: list[tuple[RuleSchema, float]] = []
        for schema in candidates:
            base = self._score_schema(goal, schema)
            bonus = 0.05 * feedback_counts.get(schema.schema_id, 0)
            scored.append((schema, min(1.0, base + bonus)))
        scored.sort(key=lambda t: t[1], reverse=True)
        return scored

    def add_to_library(self, schema: RuleSchema) -> None:
        """Add *schema* to the rule library if not already present.

        Parameters
        ----------
        schema:
            Schema to add.
        """
        existing_ids = {s.schema_id for s in self.rule_library}
        if schema.schema_id not in existing_ids:
            self.rule_library.append(schema)

    def feedback(self, schema_id: str, was_useful: bool) -> None:
        """Record user feedback for a schema.

        Parameters
        ----------
        schema_id:
            ID of the schema the feedback applies to.
        was_useful:
            Whether the schema led to a successful proof step.
        """
        self.suggestion_history.append(
            {
                "schema_id": schema_id,
                "was_useful": was_useful,
                "timestamp": _now_iso(),
            }
        )

    def _score_schema(self, goal: str, schema: RuleSchema) -> float:
        """Compute a relevance score for *schema* with respect to *goal*.

        Combines:

        1. Token-Jaccard overlap between *goal* and the conclusion pattern.
        2. A small bonus if unification actually succeeds.

        Parameters
        ----------
        goal:
            Target judgment string.
        schema:
            Schema to score.

        Returns
        -------
        float
            Score in ``[0.0, 1.0]``.
        """
        overlap = _token_overlap_score(goal, schema.conclusion_pattern)
        engine = UnificationEngine()
        unification_bonus = 0.2 if engine.unify(schema.conclusion_pattern, goal) is not None else 0.0
        return min(1.0, overlap + unification_bonus)

    def summarize_library(self) -> str:
        """Return a one-line-per-schema summary of the rule library.

        Returns
        -------
        str
            Multi-line string listing schema names, kinds, and arities.
        """
        if not self.rule_library:
            return "(empty library)"
        lines = [f"Rule library ({len(self.rule_library)} schemas):"]
        for schema in self.rule_library:
            lines.append(
                f"  {schema.schema_name!r:30s} "
                f"[{schema.schema_kind.value}]  "
                f"arity={schema.arity()}"
            )
        return "\n".join(lines)

    def copilot_prompt(self, goal: str) -> str:
        """Format a Copilot prompt for suggesting the next inference rule.

        Parameters
        ----------
        goal:
            The proof goal to suggest rules for.

        Returns
        -------
        str
            A formatted prompt string.
        """  # copilot - format a prompt for Copilot suggesting next rule
        top = self.suggest_for_goal(goal)[:3]
        names = ", ".join(s.schema_name for s in top) if top else "(no matches)"
        return (
            f"Goal: {goal}\n"
            f"Known matching schemas: {names}\n"
            f"Suggest the next inference rule to apply."
        )

    def export_library(self) -> list[dict[str, Any]]:
        """Serialize all schemas as plain dictionaries.

        Returns
        -------
        list[dict[str, Any]]
            One dict per schema, suitable for JSON serialization.
        """
        return [
            {
                "schema_id": s.schema_id,
                "schema_name": s.schema_name,
                "meta_variables": sorted(s.meta_variables),
                "premise_patterns": list(s.premise_patterns),
                "conclusion_pattern": s.conclusion_pattern,
                "schema_kind": s.schema_kind.value,
                "subformula_check": s.subformula_check,
                "metadata": s.metadata,
            }
            for s in self.rule_library
        ]


# ── Public API ────────────────────────────────────────────────────────────────

__all__ = [
    # Classes
    "RuleSchema",
    "PremiseSet",
    "ConclusionForm",
    "SideConditionEvaluator",
    "UnificationEngine",
    "CopilotRuleSuggester",
    # Helpers
    "_tokenize",
    "_apply_subst_to_pattern",
    "_extract_meta_vars_from",
    "_token_overlap_score",
]
