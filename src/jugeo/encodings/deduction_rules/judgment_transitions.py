r"""Judgment-transition system for JuGeo — ``theory2.tex`` Chapter 33, §33.2.

This module implements the *judgment transition system* (JTS) that underlies
JuGeo's proof-computation model.  A transition

.. math::

   \Gamma \vdash J \;\xrightarrow{r[\sigma]}\; \Gamma' \vdash J'

represents a single deduction step: rule :math:`r` is applied with
substitution :math:`\sigma`, transforming judgment :math:`J` into :math:`J'`
(possibly changing the context :math:`\Gamma`).

Architecture
------------
- :class:`TransitionSchema`    – a parameterised transition specification
- :class:`SubstitutionAlgebra` – algebraic operations on substitution maps
- :class:`TransitionComposer`  – composes chains of transitions
- :class:`TrustDeltaComputer`  – computes trust-level changes
- :class:`TransitionValidator` – validates individual transitions
- :class:`ProofTrace`          – a full proof as an ordered sequence of transitions

Theory alignment
----------------
§33.2 defines the transition relation inductively.  §33.3 proves that every
transition preserves well-typedness of the context.  §33.5 establishes
confluence of the transition system.
"""

from __future__ import annotations

import hashlib
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
        JudgmentTransition,
        make_rule, make_axiom_rule, _new_id, _stable_hash, _now_iso,
    )
except Exception:
    pass

# ── Local fallback stubs ──────────────────────────────────────────────────────

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

if "JudgmentTransition" not in globals():  # pragma: no cover
    @dataclass
    class JudgmentTransition:  # type: ignore[no-redef]
        """Minimal JudgmentTransition stub."""
        transition_id: str = field(default_factory=lambda: f"t-{uuid.uuid4().hex[:8]}")
        source_judgment: Any = None
        target_judgment: Any = None
        rule_applied: Any = None
        substitution: dict[str, Any] = field(default_factory=dict)
        trust_delta: int = 0
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

import re as _re

_VAR_RE = _re.compile(r'\?[A-Za-z_][A-Za-z0-9_]*')


def _tokenize_str(s: str) -> list[str]:
    """Tokenise *s* into a list of whitespace-separated tokens.

    Parentheses and brackets are split out as individual tokens.

    Parameters
    ----------
    s:
        A judgment or pattern string.

    Returns
    -------
    list[str]
        Token list (may be empty for blank strings).
    """
    spaced = _re.sub(r'([()\[\]])', r' \1 ', s)
    return [t for t in spaced.split() if t]


def _apply_bindings(template: str, bindings: Mapping[str, Any]) -> str:
    """Apply *bindings* to *template*, replacing variable tokens.

    Substitution is performed longest-first to avoid partial replacement
    (e.g. ``?AB`` must not be inadvertently replaced by a binding for ``?A``).

    Parameters
    ----------
    template:
        Source string possibly containing ``?VarName`` tokens.
    bindings:
        Mapping from variable names to their replacement strings.

    Returns
    -------
    str
        Template with all bound variables substituted.
    """
    result = template
    for key in sorted(bindings.keys(), key=len, reverse=True):
        val = str(bindings[key])
        result = _re.sub(r'(?<!\w)' + _re.escape(str(key)) + r'(?!\w)', val, result)
    return result


def _simple_unify(pattern: str, target: str) -> dict[str, str] | None:
    """Simple token-by-token unification of *pattern* against *target*.

    Meta-variables (tokens matching ``?Name``) bind to corresponding tokens
    in *target*.  If the token counts differ or a non-meta token mismatches,
    ``None`` is returned.

    Parameters
    ----------
    pattern, target:
        Strings to unify.

    Returns
    -------
    dict[str, str] | None
        Substitution on success, ``None`` on failure.
    """
    p_toks = _tokenize_str(pattern)
    t_toks = _tokenize_str(target)
    if len(p_toks) != len(t_toks):
        return None
    subst: dict[str, str] = {}
    for p, t in zip(p_toks, t_toks):
        if _VAR_RE.fullmatch(p):
            existing = subst.get(p)
            if existing is not None and existing != t:
                return None
            subst[p] = t
        elif p != t:
            return None
    return subst


def _eval_int_expr(expr: str, bindings: Mapping[str, Any], default: int = 0) -> int:
    """Evaluate a simple arithmetic expression string.

    Only numeric literals and basic operators (``+``, ``-``, ``*``) are
    permitted.  If the expression contains a variable that is present in
    *bindings*, the variable is substituted first.

    Parameters
    ----------
    expr:
        Expression string, e.g. ``"-1"`` or ``"delta + 2"``.
    bindings:
        Variable bindings available for substitution.
    default:
        Value returned when evaluation fails.

    Returns
    -------
    int
        Evaluated integer value, or *default* on failure.
    """
    try:
        resolved = _apply_bindings(expr, bindings)
        # Only allow safe characters
        if not _re.match(r'^[\d\s\+\-\*\/\(\)]+$', resolved):
            return default
        return int(eval(resolved, {"__builtins__": {}}))  # noqa: S307
    except Exception:
        return default


# ── SubstitutionAlgebra ───────────────────────────────────────────────────────

@dataclass(frozen=True, slots=True)
class SubstitutionAlgebra:
    """Algebraic structure for operations on substitution mappings.

    A substitution :math:`\\sigma` is a finite map from variable names to
    terms (represented here as strings).  The algebra provides:

    - *composition* (``compose``)     — :math:`\\sigma \\circ \\tau`
    - *restriction*  (``restrict``)  — :math:`\\sigma|_V`
    - *extension*    (``extend``)    — :math:`\\sigma[x \\mapsto t]`
    - *merging*      (``merge``)     — partial join when consistent

    This class is *frozen* (immutable); every mutating operation returns a
    new instance.

    Parameters
    ----------
    bindings:
        The underlying variable-to-value map.
    domain:
        Frozen set of variable names (must match ``bindings.keys()``).
    codomain_vars:
        Frozen set of variable names appearing in the values.
    """

    bindings: dict[str, Any]
    domain: frozenset[str]
    codomain_vars: frozenset[str]

    # ── Application ───────────────────────────────────────────────────────

    def apply_to(self, term: str) -> str:
        """Apply the substitution to *term*, returning the result.

        All occurrences of bound variables in *term* are replaced by their
        corresponding values.  Replacement is longest-first.

        Parameters
        ----------
        term:
            Source string.

        Returns
        -------
        str
            Term with substitution applied.
        """
        return _apply_bindings(term, self.bindings)

    # ── Composition ───────────────────────────────────────────────────────

    def compose(self, other: "SubstitutionAlgebra") -> "SubstitutionAlgebra":
        """Compose *self* after *other*: :math:`(\\sigma \\circ \\tau)(x) = \\sigma(\\tau(x))`.

        For each variable *x* in *other*'s domain, the composed substitution
        maps *x* to ``self.apply_to(other[x])``.  Variables only in *self*'s
        domain are carried over unchanged.

        Parameters
        ----------
        other:
            The substitution :math:`\\tau` applied first.

        Returns
        -------
        SubstitutionAlgebra
            The composed substitution :math:`\\sigma \\circ \\tau`.
        """
        new_bindings: dict[str, Any] = {}
        # Apply self to each value of other
        for var, val in other.bindings.items():
            new_bindings[var] = self.apply_to(str(val))
        # Carry over variables from self not in other's domain
        for var, val in self.bindings.items():
            if var not in new_bindings:
                new_bindings[var] = val
        new_domain = frozenset(new_bindings.keys())
        new_codomain = frozenset(str(v) for v in new_bindings.values())
        return SubstitutionAlgebra(
            bindings=new_bindings,
            domain=new_domain,
            codomain_vars=new_codomain,
        )

    # ── Restriction ───────────────────────────────────────────────────────

    def restrict(self, vars: Iterable[str]) -> "SubstitutionAlgebra":
        """Return a copy of *self* restricted to the variables in *vars*.

        Parameters
        ----------
        vars:
            Variable names to keep.

        Returns
        -------
        SubstitutionAlgebra
            Restricted substitution.
        """
        keep = frozenset(vars)
        new_bindings = {k: v for k, v in self.bindings.items() if k in keep}
        new_domain = frozenset(new_bindings.keys())
        new_codomain = frozenset(str(v) for v in new_bindings.values())
        return SubstitutionAlgebra(
            bindings=new_bindings,
            domain=new_domain,
            codomain_vars=new_codomain,
        )

    # ── Extension ─────────────────────────────────────────────────────────

    def extend(self, extra: Mapping[str, Any]) -> "SubstitutionAlgebra":
        """Return a copy of *self* extended with the bindings in *extra*.

        Existing bindings take precedence unless overwritten by *extra*.

        Parameters
        ----------
        extra:
            Additional bindings to add or overwrite.

        Returns
        -------
        SubstitutionAlgebra
            Extended substitution.
        """
        new_bindings = {**self.bindings, **extra}
        new_domain = frozenset(new_bindings.keys())
        new_codomain = frozenset(str(v) for v in new_bindings.values())
        return SubstitutionAlgebra(
            bindings=new_bindings,
            domain=new_domain,
            codomain_vars=new_codomain,
        )

    # ── Consistency and merge ─────────────────────────────────────────────

    def is_consistent_with(self, other: "SubstitutionAlgebra") -> bool:
        """Return ``True`` if *self* and *other* agree on their shared domain.

        Two substitutions are *consistent* when every variable that belongs
        to both their domains maps to the same value.

        Parameters
        ----------
        other:
            Substitution to compare against.
        """
        shared = self.domain & other.domain
        for var in shared:
            if str(self.bindings[var]) != str(other.bindings[var]):
                return False
        return True

    def merge(self, other: "SubstitutionAlgebra") -> "SubstitutionAlgebra | None":
        """Merge *self* and *other* into a single consistent substitution.

        Returns ``None`` if they are inconsistent on any shared variable.

        Parameters
        ----------
        other:
            Substitution to merge with.

        Returns
        -------
        SubstitutionAlgebra | None
            Merged substitution, or ``None`` on conflict.
        """
        if not self.is_consistent_with(other):
            return None
        return self.extend(other.bindings)

    # ── Class methods ─────────────────────────────────────────────────────

    @classmethod
    def identity(cls) -> "SubstitutionAlgebra":
        """Return the identity (empty) substitution.

        The identity maps no variable; it is the unit element for composition.
        """
        return cls(bindings={}, domain=frozenset(), codomain_vars=frozenset())

    # ── Predicates ────────────────────────────────────────────────────────

    def is_identity(self) -> bool:
        """Return ``True`` if this substitution is empty (the identity map)."""
        return len(self.bindings) == 0

    def __contains__(self, var: str) -> bool:  # type: ignore[override]
        """Return ``True`` if *var* is in the domain."""
        return var in self.bindings

    def __len__(self) -> int:
        """Return the size of the domain."""
        return len(self.bindings)

    def to_dict(self) -> dict[str, Any]:
        """Return the underlying bindings as a plain dictionary."""
        return dict(self.bindings)


# ── TransitionSchema ──────────────────────────────────────────────────────────

@dataclass(slots=True)
class TransitionSchema:
    """A parameterised specification for a single judgment transition.

    A :class:`TransitionSchema` captures the *shape* of a deduction step:
    given a source judgment matching :attr:`source_pattern`, applying the
    named rule with a computed substitution yields a target judgment derived
    from :attr:`target_pattern`.

    Pre- and post-conditions are checked against a proof context dictionary,
    enabling the system to enforce sequencing constraints (e.g. the context
    must contain a specific hypothesis before a rule may fire).

    Parameters
    ----------
    schema_id:
        Stable unique identifier.
    source_pattern:
        Pattern string for the source judgment (may contain ``?X`` variables).
    target_pattern:
        Pattern string for the target judgment.
    rule_name:
        Name of the :class:`DeductionRule` this schema describes.
    substitution_vars:
        Frozen set of meta-variable names in the patterns.
    trust_delta_formula:
        Arithmetic expression string evaluated to produce the trust delta.
    preconditions:
        List of callables or expression strings checked before application.
    postconditions:
        List of callables or expression strings checked after application.
    metadata:
        Free-form annotations.
    """

    schema_id: str
    source_pattern: str
    target_pattern: str
    rule_name: str
    substitution_vars: frozenset[str]
    trust_delta_formula: str
    preconditions: list[Any] = field(default_factory=list)
    postconditions: list[Any] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    # ── Matching ──────────────────────────────────────────────────────────

    def matches_source(self, judgment: str) -> dict[str, str] | None:
        """Try to unify :attr:`source_pattern` with *judgment*.

        Parameters
        ----------
        judgment:
            The concrete source judgment string.

        Returns
        -------
        dict[str, str] | None
            Substitution if matching succeeds, otherwise ``None``.
        """
        return _simple_unify(self.source_pattern, judgment)

    # ── Application ───────────────────────────────────────────────────────

    def apply(self, judgment: str, bindings: dict[str, str]) -> str:
        """Produce the target judgment by instantiating :attr:`target_pattern`.

        Merges *bindings* (which typically come from :meth:`matches_source`)
        with any additional variables before applying the substitution.

        Parameters
        ----------
        judgment:
            Source judgment (used for context if needed).
        bindings:
            Meta-variable bindings obtained from unification.

        Returns
        -------
        str
            The instantiated target judgment.
        """
        return _apply_bindings(self.target_pattern, bindings)

    # ── Trust delta ───────────────────────────────────────────────────────

    def compute_trust_delta(self, bindings: dict[str, str]) -> int:
        """Evaluate :attr:`trust_delta_formula` to produce a trust delta.

        The formula is a simple arithmetic expression; variable names from
        *bindings* are substituted in before evaluation.

        Parameters
        ----------
        bindings:
            Current meta-variable bindings.

        Returns
        -------
        int
            Trust delta (positive = gain, negative = loss).
        """
        return _eval_int_expr(self.trust_delta_formula, bindings, default=0)

    # ── Condition checks ──────────────────────────────────────────────────

    def check_preconditions(self, context: Mapping[str, Any]) -> bool:
        """Verify all preconditions hold in *context*.

        Preconditions are evaluated in order; the first failure short-circuits
        evaluation and returns ``False``.

        Parameters
        ----------
        context:
            The proof context as a plain dictionary.

        Returns
        -------
        bool
            ``True`` only if every precondition passes.
        """
        for cond in self.preconditions:
            try:
                if callable(cond):
                    result = cond(dict(context))
                elif isinstance(cond, str):
                    safe_ns = {k: v for k, v in context.items() if not str(k).startswith("__")}
                    safe_ns["__builtins__"] = {}
                    result = bool(eval(cond, safe_ns))  # noqa: S307
                else:
                    result = bool(cond)
                if not result:
                    return False
            except Exception:
                return False
        return True

    def check_postconditions(self, result: str, context: Mapping[str, Any]) -> bool:
        """Verify all postconditions hold after applying the transition.

        Parameters
        ----------
        result:
            The produced target judgment string.
        context:
            The proof context (should include the result for inspection).

        Returns
        -------
        bool
            ``True`` only if every postcondition passes.
        """
        augmented = {**context, "result": result, "target": result}
        for cond in self.postconditions:
            try:
                if callable(cond):
                    ok = cond(dict(augmented))
                elif isinstance(cond, str):
                    safe_ns = {k: v for k, v in augmented.items() if not str(k).startswith("__")}
                    safe_ns["__builtins__"] = {}
                    ok = bool(eval(cond, safe_ns))  # noqa: S307
                else:
                    ok = bool(cond)
                if not ok:
                    return False
            except Exception:
                return False
        return True

    # ── Conversion ────────────────────────────────────────────────────────

    def to_deduction_rule(self) -> "DeductionRule":
        """Convert this schema to a :class:`DeductionRule`.

        The source pattern becomes the single premise; the target pattern
        becomes the conclusion.

        Returns
        -------
        DeductionRule
            A rule encoding this transition schema.
        """
        return make_rule(
            name=self.rule_name,
            premises=[self.source_pattern],
            conclusion=self.target_pattern,
            kind=RuleKind.DERIVED,
            schema_id=self.schema_id,
            trust_delta_formula=self.trust_delta_formula,
        )

    # ── Validation ────────────────────────────────────────────────────────

    def validate(self) -> list[str]:
        """Return a list of validation error messages, or empty if valid.

        Checks:
        - Patterns are non-empty strings.
        - Rule name is non-empty.
        - Substitution vars are a subset of variables actually appearing
          in the patterns.

        Returns
        -------
        list[str]
            Error messages; empty list means the schema is valid.
        """
        errors: list[str] = []
        if not self.source_pattern.strip():
            errors.append("source_pattern is empty")
        if not self.target_pattern.strip():
            errors.append("target_pattern is empty")
        if not self.rule_name.strip():
            errors.append("rule_name is empty")
        # Check that declared substitution_vars actually appear in patterns
        combined = f"{self.source_pattern} {self.target_pattern}"
        found_vars = frozenset(_VAR_RE.findall(combined))
        undeclared = self.substitution_vars - found_vars
        if undeclared:
            errors.append(
                f"substitution_vars declared but not found in patterns: "
                f"{sorted(undeclared)}"
            )
        return errors

    def describe(self) -> str:
        """Return a human-readable description of this transition schema.

        Returns
        -------
        str
            Multi-line formatted string.
        """
        lines = [
            f"TransitionSchema: {self.rule_name!r}",
            f"  ID             : {self.schema_id}",
            f"  Source pattern : {self.source_pattern}",
            f"  Target pattern : {self.target_pattern}",
            f"  Subst. vars    : {', '.join(sorted(self.substitution_vars)) or '(none)'}",
            f"  Trust formula  : {self.trust_delta_formula or '0'}",
            f"  Preconditions  : {len(self.preconditions)}",
            f"  Postconditions : {len(self.postconditions)}",
        ]
        return "\n".join(lines)


# ── TransitionComposer ────────────────────────────────────────────────────────

@dataclass(slots=True)
class TransitionComposer:
    """Composes an ordered sequence of judgment transitions into a chain.

    Each :class:`JudgmentTransition` in :attr:`transitions` is checked to
    ensure that its ``source_judgment`` matches the ``target_judgment`` of
    the preceding step (when :attr:`verify_chain` is ``True``).

    The composer supports slicing, reversal, and folding all transitions into
    a single representative transition for compact proof certificates.

    Parameters
    ----------
    transitions:
        Ordered list of transitions forming the chain.
    verify_chain:
        If ``True``, :meth:`append` rejects transitions that break the chain.
    """

    transitions: list[JudgmentTransition]
    verify_chain: bool = True

    # ── Mutation ──────────────────────────────────────────────────────────

    def append(self, transition: JudgmentTransition) -> bool:
        """Add *transition* to the end of the chain.

        If :attr:`verify_chain` is ``True`` and the chain is non-empty, the
        source judgment of *transition* must match the target of the last
        step.  Returns ``False`` (and does not append) if the chain would be
        broken.

        Parameters
        ----------
        transition:
            The next transition in the proof.

        Returns
        -------
        bool
            ``True`` if the transition was appended, ``False`` if rejected.
        """
        if self.verify_chain and self.transitions:
            last_target = self.transitions[-1].target_judgment
            # Accept if source matches last target (string comparison)
            if str(transition.source_judgment) != str(last_target):
                return False
        self.transitions.append(transition)
        return True

    # ── Composition ───────────────────────────────────────────────────────

    def compose_all(self) -> "JudgmentTransition | None":
        """Fold all transitions into a single composite transition.

        The composite transition has:
        - ``source_judgment`` of the first step
        - ``target_judgment`` of the last step
        - ``trust_delta`` equal to the sum of all deltas
        - A rule name summarising the chain

        Returns
        -------
        JudgmentTransition | None
            The composite transition, or ``None`` if the list is empty.
        """
        if not self.transitions:
            return None
        first = self.transitions[0]
        last = self.transitions[-1]
        total_delta = sum(t.trust_delta for t in self.transitions)
        rule_names = " ∘ ".join(
            getattr(t.rule_applied, "rule_name", str(t.rule_applied))
            for t in self.transitions
        )
        # Build a minimal composite rule
        composite_rule = make_axiom_rule(
            name=f"[composed: {rule_names}]",
            conclusion=str(last.target_judgment),
            step_count=len(self.transitions),
        )
        return JudgmentTransition(
            transition_id=_new_id("comp"),
            source_judgment=first.source_judgment,
            target_judgment=last.target_judgment,
            rule_applied=composite_rule,
            substitution={},
            trust_delta=total_delta,
        )

    # ── Inspection ────────────────────────────────────────────────────────

    def is_complete(self) -> bool:
        """Return ``True`` if the chain has no gaps.

        A chain is *complete* when every successive pair of transitions has
        matching target / source judgments.

        Returns
        -------
        bool
            ``True`` if the chain is gapless.
        """
        for i in range(len(self.transitions) - 1):
            cur_target = str(self.transitions[i].target_judgment)
            next_source = str(self.transitions[i + 1].source_judgment)
            if cur_target != next_source:
                return False
        return True

    def intermediate_judgments(self) -> list[Any]:
        """Return all intermediate judgment states in the chain.

        Includes the initial source and all targets (including the final one).

        Returns
        -------
        list[Any]
            Ordered list of judgment values.
        """
        if not self.transitions:
            return []
        judgments: list[Any] = [self.transitions[0].source_judgment]
        for t in self.transitions:
            judgments.append(t.target_judgment)
        return judgments

    def total_trust_delta(self) -> int:
        """Return the sum of trust deltas across all transitions.

        Returns
        -------
        int
            Aggregate trust change.
        """
        return sum(t.trust_delta for t in self.transitions)

    # ── Transformation ────────────────────────────────────────────────────

    def reverse(self) -> "TransitionComposer":
        """Return a new composer with the transition order inverted.

        Source and target judgments in each transition are also swapped so
        that the reversed chain can be validated independently.

        Returns
        -------
        TransitionComposer
            A new composer with reversed, swapped transitions.
        """
        reversed_list: list[JudgmentTransition] = []
        for t in reversed(self.transitions):
            inverted = JudgmentTransition(
                transition_id=_new_id("rev"),
                source_judgment=t.target_judgment,
                target_judgment=t.source_judgment,
                rule_applied=t.rule_applied,
                substitution=t.substitution,
                trust_delta=-t.trust_delta,
            )
            reversed_list.append(inverted)
        return TransitionComposer(
            transitions=reversed_list,
            verify_chain=self.verify_chain,
        )

    def slice(self, start: int, end: int) -> "TransitionComposer":
        """Return a sub-chain from *start* (inclusive) to *end* (exclusive).

        Parameters
        ----------
        start:
            First transition index to include.
        end:
            One past the last transition index to include.

        Returns
        -------
        TransitionComposer
            Sub-chain (may be empty if ``start >= end``).
        """
        sub = self.transitions[start:end]
        return TransitionComposer(transitions=sub, verify_chain=self.verify_chain)

    # ── Serialisation ────────────────────────────────────────────────────

    def to_proof_steps(self) -> list[dict[str, Any]]:
        """Convert the chain to a list of proof-step dictionaries.

        Each dictionary contains:
        - ``step`` – one-based step number
        - ``source``, ``target`` – judgment strings
        - ``rule`` – rule name
        - ``trust_delta`` – trust change at this step
        - ``substitution`` – the substitution used

        Returns
        -------
        list[dict[str, Any]]
            Serialised proof steps.
        """
        steps: list[dict[str, Any]] = []
        for i, t in enumerate(self.transitions, 1):
            rule_name = getattr(t.rule_applied, "rule_name", str(t.rule_applied))
            steps.append(
                {
                    "step": i,
                    "source": str(t.source_judgment),
                    "target": str(t.target_judgment),
                    "rule": rule_name,
                    "trust_delta": t.trust_delta,
                    "substitution": dict(t.substitution),
                }
            )
        return steps

    # ── Validation ────────────────────────────────────────────────────────

    def verify(self) -> list[str]:
        """Validate each transition in the chain; return error messages.

        Checks:
        - Each transition has a non-None rule.
        - The chain is connected (no gaps between consecutive steps).

        Returns
        -------
        list[str]
            Validation errors; empty list means the chain is valid.
        """
        errors: list[str] = []
        for i, t in enumerate(self.transitions):
            if t.rule_applied is None:
                errors.append(f"step {i}: rule_applied is None")
            if i > 0:
                prev_target = str(self.transitions[i - 1].target_judgment)
                cur_source = str(t.source_judgment)
                if prev_target != cur_source:
                    errors.append(
                        f"step {i}: gap — previous target {prev_target!r} "
                        f"!= current source {cur_source!r}"
                    )
        return errors


# ── TrustDeltaComputer ────────────────────────────────────────────────────────

@dataclass(slots=True)
class TrustDeltaComputer:
    """Computes trust-level changes produced by deduction rule applications.

    Each rule kind carries a *default* trust delta; individual rules may
    override this via explicit registration.  The *escalation policy*
    determines whether consecutive positive deltas are compounded or capped.

    Parameters
    ----------
    base_deltas:
        Mapping from rule name to an explicit integer delta.
    trust_algebra:
        The trust algebra used for policy application (may be a stub).
    escalation_policy:
        One of ``"conservative"`` (cap at +1 per step) or
        ``"additive"`` (sum deltas without capping).
    """

    base_deltas: dict[str, int]
    trust_algebra: Any
    escalation_policy: str = "conservative"

    # ── Computation ───────────────────────────────────────────────────────

    def compute(
        self,
        rule_name: str,
        rule_kind: RuleKind,
        context: Mapping[str, Any],
    ) -> int:
        """Compute the trust delta for a given rule application.

        Looks up *rule_name* in :attr:`base_deltas` first; falls back to
        :meth:`default_delta` if not registered.  The result is passed
        through :meth:`apply_policy` and :meth:`clamp`.

        Parameters
        ----------
        rule_name:
            Name of the rule being applied.
        rule_kind:
            Kind of the rule (used for default delta lookup).
        context:
            Proof context, which may contain additional modifiers.

        Returns
        -------
        int
            Computed trust delta.
        """
        base = self.base_deltas.get(rule_name, self.default_delta(rule_kind))
        # Context may contain a multiplier
        multiplier = int(context.get("trust_multiplier", 1))
        raw = base * multiplier
        current_level = context.get("current_trust_level", None)
        adjusted = self.apply_policy(raw, current_level)
        return self.clamp(adjusted)

    def accumulate(self, deltas: Sequence[int]) -> int:
        """Combine a sequence of deltas into a single aggregate value.

        Under the ``"conservative"`` policy, the result is capped at the
        minimum and maximum clamp values regardless of the sum.  Under
        ``"additive"``, the raw sum is returned (still subject to clamp).

        Parameters
        ----------
        deltas:
            Sequence of individual trust deltas.

        Returns
        -------
        int
            Aggregate delta.
        """
        if not deltas:
            return 0
        total = sum(deltas)
        if self.escalation_policy == "conservative":
            return self.clamp(total, min_val=-5, max_val=5)
        return total  # additive: caller clamps if needed

    def apply_policy(self, delta: int, current_level: Any) -> int:
        """Apply the escalation policy to *delta*.

        Under ``"conservative"`` policy, positive deltas are capped at +1
        regardless of the rule's nominal delta.  Negative deltas are
        passed through unchanged.

        Parameters
        ----------
        delta:
            The raw trust delta.
        current_level:
            The current trust level (type depends on TrustLevel stub/impl).

        Returns
        -------
        int
            Policy-adjusted delta.
        """
        if self.escalation_policy == "conservative" and delta > 1:
            return 1
        return delta

    def clamp(self, delta: int, min_val: int = -5, max_val: int = 5) -> int:
        """Clamp *delta* to the interval ``[min_val, max_val]``.

        Parameters
        ----------
        delta:
            Value to clamp.
        min_val:
            Lower bound (inclusive).
        max_val:
            Upper bound (inclusive).

        Returns
        -------
        int
            Clamped value.
        """
        return max(min_val, min(max_val, delta))

    def explain(self, rule_name: str, delta: int) -> str:
        """Produce a human-readable explanation for the computed *delta*.

        Parameters
        ----------
        rule_name:
            Name of the rule.
        delta:
            The computed delta.

        Returns
        -------
        str
            One-line explanation.
        """
        sign = "+" if delta >= 0 else ""
        registered = "explicit" if rule_name in self.base_deltas else "default"
        return (
            f"Rule {rule_name!r}: trust delta = {sign}{delta} "
            f"({registered}, policy={self.escalation_policy!r})"
        )

    def default_delta(self, rule_kind: RuleKind) -> int:
        """Return the default trust delta for a given rule kind.

        The defaults reflect the logical strength of the rule:

        - ``AXIOM``      → +2  (axioms are immediately trusted)
        - ``STRUCTURAL`` → +1  (structural manipulations are cheap)
        - ``SEMANTIC``   → +1
        - ``LOGICAL``    → +1
        - ``DERIVED``    → 0   (derived rules inherit trust from sub-proofs)
        - ``EQUALITY``   → +1
        - ``MODAL``      → 0   (modalities require external witnesses)
        - ``CUSTOM``     → 0

        Parameters
        ----------
        rule_kind:
            The :class:`RuleKind` to look up.

        Returns
        -------
        int
            Default trust delta.
        """
        defaults: dict[str, int] = {
            "axiom": 2,
            "structural": 1,
            "semantic": 1,
            "logical": 1,
            "derived": 0,
            "equality": 1,
            "modal": 0,
            "custom": 0,
        }
        kind_val = rule_kind.value if hasattr(rule_kind, "value") else str(rule_kind)
        return defaults.get(kind_val, 0)

    def register_rule_delta(self, rule_name: str, delta: int) -> None:
        """Register an explicit trust delta for a named rule.

        Parameters
        ----------
        rule_name:
            Rule name.
        delta:
            Explicit delta to associate with the rule.
        """
        self.base_deltas[rule_name] = delta


# ── TransitionValidator ───────────────────────────────────────────────────────

@dataclass(slots=True)
class TransitionValidator:
    """Validates individual and sequential judgment transitions.

    Checks structural invariants (rule existence, chain connectivity),
    optional type-preservation, and trust monotonicity.

    Parameters
    ----------
    strict_mode:
        If ``True``, any validation failure is an error.  In non-strict mode,
        missing rule registrations produce warnings rather than errors.
    known_rules:
        Registry of known :class:`DeductionRule` objects keyed by rule name.
    trust_required:
        Minimum required trust level for transitions to be accepted.
    """

    strict_mode: bool = True
    known_rules: dict[str, "DeductionRule"] = field(default_factory=dict)
    trust_required: Any = None

    # ── Validation ────────────────────────────────────────────────────────

    def validate(self, transition: "JudgmentTransition") -> list[str]:
        """Perform comprehensive validation of a single *transition*.

        Checks performed:
        1. ``rule_applied`` is not ``None``.
        2. The rule name is registered in :attr:`known_rules` (strict mode).
        3. Source and target judgments are not ``None``.
        4. Trust delta is within the accepted range ``[-5, +5]``.

        Parameters
        ----------
        transition:
            The transition to validate.

        Returns
        -------
        list[str]
            Validation error messages; empty means valid.
        """
        errors: list[str] = []
        if transition.rule_applied is None:
            errors.append("rule_applied is None")
            return errors  # Cannot proceed without a rule

        rule_name = getattr(transition.rule_applied, "rule_name", None)
        if rule_name is None:
            errors.append("rule_applied.rule_name is None")

        if self.strict_mode and not self.check_rule_exists(str(rule_name or "")):
            errors.append(f"rule {rule_name!r} not in known_rules registry")

        if transition.source_judgment is None:
            errors.append("source_judgment is None")
        if transition.target_judgment is None:
            errors.append("target_judgment is None")

        delta = transition.trust_delta
        if not (-5 <= delta <= 5):
            errors.append(
                f"trust_delta {delta} is out of range [-5, 5]"
            )

        return errors

    def validate_sequence(
        self, transitions: Sequence["JudgmentTransition"]
    ) -> list[str]:
        """Validate a chain of transitions.

        In addition to per-step validation, checks:
        - Consecutive steps are connected (target_i == source_{i+1}).
        - Trust monotonicity is not violated (in strict mode).

        Parameters
        ----------
        transitions:
            Ordered sequence of transitions.

        Returns
        -------
        list[str]
            All errors found across all steps.
        """
        all_errors: list[str] = []
        deltas: list[int] = []
        for i, t in enumerate(transitions):
            step_errors = [f"[step {i}] {e}" for e in self.validate(t)]
            all_errors.extend(step_errors)
            deltas.append(t.trust_delta)
            # Connectivity check
            if i > 0:
                prev = transitions[i - 1]
                if str(prev.target_judgment) != str(t.source_judgment):
                    all_errors.append(
                        f"[step {i}] chain gap: "
                        f"prev target={str(prev.target_judgment)!r} "
                        f"!= source={str(t.source_judgment)!r}"
                    )
        if self.strict_mode and not self.check_trust_monotonicity(deltas):
            all_errors.append("trust_monotonicity violated in sequence")
        return all_errors

    def check_rule_exists(self, rule_name: str) -> bool:
        """Return ``True`` if *rule_name* is in :attr:`known_rules`.

        Parameters
        ----------
        rule_name:
            Rule name to look up.
        """
        return rule_name in self.known_rules

    def check_type_preservation(self, source: Any, target: Any) -> bool:
        """Check that *source* and *target* judgments share the same type tag.

        The type tag is determined by the first whitespace-delimited token of
        the string representation.  Returns ``True`` if both tags match or if
        either value is ``None``.

        Parameters
        ----------
        source, target:
            Judgment values (compared as strings).
        """
        if source is None or target is None:
            return True
        src_tokens = _tokenize_str(str(source))
        tgt_tokens = _tokenize_str(str(target))
        if not src_tokens or not tgt_tokens:
            return True
        return src_tokens[0] == tgt_tokens[0]

    def check_trust_monotonicity(self, deltas: Sequence[int]) -> bool:
        """Return ``True`` if the cumulative trust does not decrease below -5.

        A sequence of deltas violates monotonicity if the running sum ever
        drops below the threshold ``-5``.

        Parameters
        ----------
        deltas:
            Ordered trust deltas, one per transition.
        """
        running = 0
        for d in deltas:
            running += d
            if running < -5:
                return False
        return True

    def summarize(self, errors: list[str]) -> str:
        """Format *errors* as a human-readable summary.

        Parameters
        ----------
        errors:
            Error messages from a validation call.

        Returns
        -------
        str
            Multi-line summary string.
        """
        if not errors:
            return "Validation passed — no errors."
        header = f"Validation FAILED ({len(errors)} error(s)):"
        body = "\n".join(f"  • {e}" for e in errors)
        return f"{header}\n{body}"

    def is_valid(self, transition: "JudgmentTransition") -> bool:
        """Return ``True`` if *transition* passes all checks.

        Parameters
        ----------
        transition:
            Transition to validate.
        """
        return len(self.validate(transition)) == 0


# ── ProofTrace ────────────────────────────────────────────────────────────────

@dataclass(slots=True)
class ProofTrace:
    """A complete proof represented as an ordered sequence of transitions.

    A :class:`ProofTrace` tracks a proof attempt from its initial goal through
    each rule application until the proof is complete (or abandoned).  It
    provides serialisation to a *proof certificate* and integration with the
    validator.

    Parameters
    ----------
    trace_id:
        Unique identifier for this proof trace.
    goal:
        The judgment that the proof aims to establish.
    transitions:
        Ordered list of transitions executed so far.
    initial_context:
        The proof context at the start of the trace.
    status:
        One of ``"in-progress"``, ``"complete"``, ``"failed"``.
    metadata:
        Free-form annotations (author, date, theory reference, etc.).
    """

    trace_id: str
    goal: str
    transitions: list["JudgmentTransition"]
    initial_context: dict[str, Any] = field(default_factory=dict)
    status: str = "in-progress"
    metadata: dict[str, Any] = field(default_factory=dict)

    # ── Mutation ──────────────────────────────────────────────────────────

    def append(self, transition: "JudgmentTransition") -> None:
        """Append *transition* to the trace and update :attr:`status`.

        After appending, checks :meth:`is_complete`; if the proof is now
        complete, :attr:`status` is set to ``"complete"``.

        Parameters
        ----------
        transition:
            The next proof step to record.
        """
        self.transitions.append(transition)
        if self.is_complete():
            self.status = "complete"

    # ── Inspection ────────────────────────────────────────────────────────

    def is_complete(self) -> bool:
        """Return ``True`` if the last transition's target matches :attr:`goal`.

        Returns
        -------
        bool
            ``True`` when the proof has reached its goal.
        """
        if not self.transitions:
            return False
        last_target = str(self.transitions[-1].target_judgment)
        return last_target == self.goal

    def length(self) -> int:
        """Return the number of transitions in the trace."""
        return len(self.transitions)

    def total_trust_gain(self) -> int:
        """Return the sum of all trust deltas in the trace.

        Returns
        -------
        int
            Aggregate trust change over the entire proof.
        """
        return sum(t.trust_delta for t in self.transitions)

    def rules_used(self) -> list[str]:
        """Return unique rule names in the order they first appear.

        Returns
        -------
        list[str]
            Deduplicated rule names preserving first-occurrence order.
        """
        seen: set[str] = set()
        result: list[str] = []
        for t in self.transitions:
            name = getattr(t.rule_applied, "rule_name", str(t.rule_applied))
            if name not in seen:
                seen.add(name)
                result.append(name)
        return result

    # ── Serialisation ────────────────────────────────────────────────────

    def to_proof_certificate(self) -> dict[str, Any]:
        """Serialise the trace as a proof certificate.

        A proof certificate contains all information needed to independently
        verify the proof without access to the live proof state.

        Returns
        -------
        dict[str, Any]
            Serialised certificate.
        """
        steps = []
        for i, t in enumerate(self.transitions, 1):
            rule_name = getattr(t.rule_applied, "rule_name", str(t.rule_applied))
            steps.append(
                {
                    "step": i,
                    "source": str(t.source_judgment),
                    "target": str(t.target_judgment),
                    "rule": rule_name,
                    "substitution": dict(t.substitution),
                    "trust_delta": t.trust_delta,
                    "transition_id": getattr(t, "transition_id", ""),
                }
            )
        return {
            "certificate_version": "1.0",
            "trace_id": self.trace_id,
            "goal": self.goal,
            "status": self.status,
            "steps": steps,
            "total_steps": len(steps),
            "total_trust_gain": self.total_trust_gain(),
            "rules_used": self.rules_used(),
            "initial_context": self.initial_context,
            "metadata": self.metadata,
            "generated_at": _now_iso(),
            "content_hash": _stable_hash(
                f"{self.trace_id}:{self.goal}:{len(steps)}"
            ),
        }

    # ── Validation ────────────────────────────────────────────────────────

    def verify(self, validator: "TransitionValidator") -> list[str]:
        """Validate all transitions in the trace using *validator*.

        Parameters
        ----------
        validator:
            Configured validator to use.

        Returns
        -------
        list[str]
            All validation errors found; empty means the trace is valid.
        """
        return validator.validate_sequence(self.transitions)

    # ── Summary ───────────────────────────────────────────────────────────

    def summarize(self) -> str:
        """Return a compact human-readable summary of the trace.

        Returns
        -------
        str
            Multi-line summary string.
        """
        rules = ", ".join(self.rules_used()) or "(none)"
        lines = [
            f"ProofTrace {self.trace_id}",
            f"  Goal       : {self.goal}",
            f"  Status     : {self.status}",
            f"  Steps      : {self.length()}",
            f"  Trust gain : {self.total_trust_gain():+d}",
            f"  Rules used : {rules}",
        ]
        return "\n".join(lines)

    def copilot_explain(self) -> str:
        """Produce a natural-language explanation of the proof.

        Describes each step in plain English, suitable for display in an
        interactive proof assistant interface.

        Returns
        -------
        str
            Natural-language proof narrative.
        """  # copilot - produce natural language explanation
        if not self.transitions:
            return f"The proof of '{self.goal}' has not been started yet."
        lines = [f"Proof of '{self.goal}':"]
        for i, t in enumerate(self.transitions, 1):
            rule_name = getattr(t.rule_applied, "rule_name", str(t.rule_applied))
            delta_str = f"+{t.trust_delta}" if t.trust_delta >= 0 else str(t.trust_delta)
            lines.append(
                f"  Step {i}: Apply rule '{rule_name}' to\n"
                f"    '{t.source_judgment}'\n"
                f"  yielding\n"
                f"    '{t.target_judgment}'\n"
                f"  (trust delta: {delta_str})"
            )
        if self.status == "complete":
            lines.append(f"\nConclusion: goal '{self.goal}' established in "
                         f"{self.length()} step(s).")
        else:
            lines.append(f"\nProof is {self.status}.")
        return "\n".join(lines)


# ── Public API ────────────────────────────────────────────────────────────────

__all__ = [
    # Classes
    "TransitionSchema",
    "SubstitutionAlgebra",
    "TransitionComposer",
    "TrustDeltaComputer",
    "TransitionValidator",
    "ProofTrace",
    # Helpers
    "_tokenize_str",
    "_apply_bindings",
    "_simple_unify",
    "_eval_int_expr",
]
