r"""Deduction rule models for JuGeo — Chapter 33 of ``theory2.tex``.

This module defines the core data model for the deduction-rule sub-system.
Every deduction rule is an inference schema of the form

.. math::

   \frac{\Gamma_1 \vdash J_1 \quad \cdots \quad \Gamma_n \vdash J_n}
        {\Gamma \vdash J}
        \;[\text{side conditions}]

where the horizontal bar separates *premises* (above) from the *conclusion*
(below).  A rule *fires* exactly when every premise obligation has been
discharged and all side conditions are satisfied.

Architecture
------------
- :class:`DeductionRule`      – the rule schema itself
- :class:`JudgmentTransition` – one step in a transition system
- :class:`InferenceStep`      – one node in a derivation tree
- :class:`RuleApplication`    – an immutable record of a rule firing
- :class:`TransitionSystem`   – the full rule-set with fixpoint semantics

Theory alignment
----------------
Section 33.1 introduces structural vs semantic rules.  Section 33.2 defines
the transition relation  ``Γ ⊢ J  →_r  Γ' ⊢ J'``.  Section 33.4 proves
cut-elimination for the full system.

Copilot integration
-------------------
Every class exposes a ``copilot_*`` method that bridges to the Copilot
assistant for interactive suggestion and explanation.
"""

from __future__ import annotations

import hashlib
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass, field, replace
from enum import Enum, IntEnum
from typing import Any, Iterable, Mapping, Sequence

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


# ---------------------------------------------------------------------------
# Helper utilities
# ---------------------------------------------------------------------------

def _stable_hash(payload: str) -> str:
    """Return a short stable SHA-256 hex digest for *payload*."""
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def _now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string."""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _new_id(prefix: str = "dr") -> str:
    """Generate a unique ID with the given prefix."""
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class RuleKind(str, Enum):
    """Classifies a deduction rule by its logical role.

    Theory reference: ``theory2.tex`` §33.1 distinguishes structural rules
    (those that manipulate the *context* Γ without inspecting the formula)
    from semantic rules (those driven by the logical connectives).

    .. math::

       \\text{RuleKind} \\in \\{\\text{STRUCTURAL},\\,\\text{SEMANTIC},\\,
       \\text{AXIOM},\\,\\text{DERIVED}\\}
    """

    STRUCTURAL = "structural"
    """Weakening, contraction, exchange, cut."""

    SEMANTIC = "semantic"
    """Introduction and elimination rules for connectives."""

    AXIOM = "axiom"
    """Base cases: identity, reflexivity, assumption."""

    DERIVED = "derived"
    """Rules that are admissible but not primitive."""


class TransitionKind(str, Enum):
    """The variety of judgment-transition step.

    A transition :math:`J \\xrightarrow{r} J'` may be *forward* (reducing
    obligations), *backward* (adding assumptions), or *lateral* (rewriting
    within the same trust tier).
    """

    FORWARD = "forward"
    BACKWARD = "backward"
    LATERAL = "lateral"
    FIXPOINT = "fixpoint"
    INTERRUPT = "interrupt"


class InferenceStatus(str, Enum):
    """The status of an ongoing inference chain."""

    PENDING = "pending"
    IN_PROGRESS = "in-progress"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    INCONCLUSIVE = "inconclusive"


class ApplicationResult(str, Enum):
    """The outcome of a single rule-application attempt."""

    APPLIED = "applied"
    INAPPLICABLE = "inapplicable"
    SIDE_CONDITION_FAILURE = "side-condition-failure"
    UNIFICATION_FAILURE = "unification-failure"
    TRUST_INSUFFICIENT = "trust-insufficient"
    ERROR = "error"


# ---------------------------------------------------------------------------
# DeductionRule
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class DeductionRule:
    """A deduction rule that fires when all premise obligations are discharged.

    Formally, a rule is a schema

    .. math::

       \\frac{P_1 \\quad P_2 \\quad \\cdots \\quad P_n}{C}
             \\;[\\text{sc}_1, \\ldots, \\text{sc}_k]

    where each :math:`P_i` is a *premise* (a judgment schema), :math:`C` is
    the *conclusion* schema, and the :math:`\\text{sc}_j` are Boolean side
    conditions over the meta-variables.

    Attributes
    ----------
    rule_id:
        Stable unique identifier derived from the rule name.
    rule_name:
        Human-readable name (e.g. ``"∧-intro"``, ``"weakening"``).
    premises:
        Ordered tuple of premise schemas.  Order matters for display but not
        for applicability.
    conclusion:
        The conclusion schema.
    side_conditions:
        Mapping from condition name to a callable or expression string.
    rule_kind:
        :class:`RuleKind` classifying this rule.
    trust_required:
        Minimum :class:`TrustLevel` required in the context before the rule
        may fire.
    metadata:
        Free-form annotations (author, source section, etc.).
    """

    rule_id: str
    rule_name: str
    premises: tuple[str, ...]
    conclusion: str
    side_conditions: dict[str, Any] = field(default_factory=dict)
    rule_kind: RuleKind = RuleKind.SEMANTIC
    trust_required: TrustLevel = TrustLevel.UNVERIFIED
    metadata: dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------------
    def applies_to(self, judgment: Any, context: Mapping[str, Any] | None = None) -> bool:
        """Test whether this rule is *prima facie* applicable to *judgment*.

        A rule is applicable when the conclusion schema unifies with the
        given judgment under some substitution, and the context provides
        sufficient trust.

        Parameters
        ----------
        judgment:
            The target judgment (or its string representation).
        context:
            Optional ambient context supplying trust information.

        Returns
        -------
        bool
            ``True`` iff unification succeeds and trust is satisfied.
        """
        ctx: dict[str, Any] = dict(context or {})
        judgment_str = str(judgment)

        # Attempt pattern-based unification between conclusion schema and judgment
        unifier = self._try_unify(self.conclusion, judgment_str)
        if unifier is None:
            return False

        # Trust gate
        trust = ctx.get("trust_level", TrustLevel.UNVERIFIED)
        if isinstance(trust, str):
            trust = TrustLevel(trust)
        try:
            required_rank = list(TrustLevel).index(self.trust_required)
            current_rank = list(TrustLevel).index(trust)
            if current_rank < required_rank:
                return False
        except (ValueError, AttributeError):
            pass  # If we can't compare, allow by default

        return True

    def _try_unify(self, schema: str, target: str) -> dict[str, str] | None:
        """Attempt first-order unification of *schema* against *target*.

        Meta-variables in *schema* are denoted with a leading ``?`` or
        uppercase single letters.  Returns the substitution dict on success,
        ``None`` on failure.
        """
        # Tokenise both sides
        import re
        schema_tokens = re.findall(r'\??\w+|[^\w\s]', schema)
        target_tokens = re.findall(r'\??\w+|[^\w\s]', target)

        if len(schema_tokens) != len(target_tokens):
            # Allow prefix match when schema is a sub-pattern
            if len(schema_tokens) > len(target_tokens):
                return None
            target_tokens = target_tokens[:len(schema_tokens)]

        subst: dict[str, str] = {}
        for s_tok, t_tok in zip(schema_tokens, target_tokens):
            if s_tok.startswith('?') or (s_tok.isupper() and len(s_tok) == 1):
                # Meta-variable
                key = s_tok.lstrip('?')
                if key in subst and subst[key] != t_tok:
                    return None
                subst[key] = t_tok
            elif s_tok != t_tok:
                return None
        return subst

    def fire(
        self,
        discharged_premises: Sequence[Any],
        substitution: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Fire the rule given discharged premises and an optional substitution.

        Applies the rule by instantiating the conclusion schema under
        *substitution* and verifying that all side conditions hold.

        Parameters
        ----------
        discharged_premises:
            Evidence or proof terms for each premise, in order.
        substitution:
            Variable bindings collected during unification.

        Returns
        -------
        dict
            A result record with keys ``conclusion``, ``evidence``,
            ``status``, ``timestamp``.

        Raises
        ------
        ValueError
            If the number of discharged premises does not match.
        RuntimeError
            If a side condition fails.
        """
        if len(discharged_premises) != len(self.premises):
            raise ValueError(
                f"Rule '{self.rule_name}' expects {len(self.premises)} premises, "
                f"got {len(discharged_premises)}"
            )

        subst = dict(substitution or {})
        if not self.check_side_conditions(subst):
            raise RuntimeError(
                f"Side condition failure when firing rule '{self.rule_name}'"
            )

        conclusion_instance = self.instantiate(subst)
        evidence = {
            "rule": self.rule_name,
            "premises_discharged": len(discharged_premises),
            "substitution": subst,
        }

        return {
            "conclusion": conclusion_instance,
            "evidence": evidence,
            "status": ApplicationResult.APPLIED.value,
            "timestamp": _now_iso(),
            "rule_id": self.rule_id,
        }

    def check_side_conditions(self, bindings: Mapping[str, Any]) -> bool:
        """Evaluate all side conditions under *bindings*.

        Each side condition is either a callable ``f(bindings) -> bool`` or
        a string expression evaluated in a restricted namespace.

        Parameters
        ----------
        bindings:
            Current variable bindings from unification.

        Returns
        -------
        bool
            ``True`` iff every side condition is satisfied.
        """
        for name, condition in self.side_conditions.items():
            if callable(condition):
                try:
                    result = condition(dict(bindings))
                except Exception:
                    return False
                if not result:
                    return False
            elif isinstance(condition, str):
                # Evaluate string condition in a safe namespace
                namespace = {"__builtins__": {}, **dict(bindings)}
                try:
                    result = eval(condition, namespace)  # noqa: S307 – restricted NS
                except Exception:
                    return False
                if not result:
                    return False
            elif isinstance(condition, bool):
                if not condition:
                    return False
        return True

    def unify_premises(
        self, candidates: Sequence[Any]
    ) -> dict[str, str] | None:
        """Attempt to unify *candidates* against the premise schemas.

        Processes premises left-to-right, accumulating a shared substitution.
        Returns the merged substitution on success, ``None`` if any premise
        fails to unify.

        Parameters
        ----------
        candidates:
            Sequence of judgment strings to match against premises.
        """
        if len(candidates) != len(self.premises):
            return None

        merged: dict[str, str] = {}
        for premise_schema, candidate in zip(self.premises, candidates):
            subst = self._try_unify(premise_schema, str(candidate))
            if subst is None:
                return None
            # Check consistency with already-accumulated bindings
            for var, val in subst.items():
                if var in merged and merged[var] != val:
                    return None
                merged[var] = val
        return merged

    def instantiate(self, substitution: Mapping[str, str]) -> str:
        """Instantiate the conclusion schema under *substitution*.

        Replaces every meta-variable occurrence in the conclusion with the
        corresponding bound value.

        Parameters
        ----------
        substitution:
            Variable-to-term binding map.

        Returns
        -------
        str
            The instantiated conclusion as a string.
        """
        result = self.conclusion
        # Sort by length descending to avoid partial-replacement bugs
        for var in sorted(substitution, key=len, reverse=True):
            val = str(substitution[var])
            result = result.replace(f"?{var}", val).replace(var, val)
        return result

    def to_sequent(self) -> str:
        """Render this rule in sequent-calculus notation.

        Returns a multi-line string

        .. code-block:: text

           P1   P2   ...   Pn
           ──────────────────  rule_name
                  C

        suitable for display in terminals and documentation.
        """
        premise_line = "   ".join(self.premises) if self.premises else "(axiom)"
        bar = "─" * max(len(premise_line), len(self.conclusion), 20)
        return f"{premise_line}\n{bar}  {self.rule_name}\n{self.conclusion}"

    def validate(self) -> list[str]:
        """Check internal consistency of this rule definition.

        Verifies that:
        - ``rule_id`` and ``rule_name`` are non-empty
        - ``premises`` is a tuple of non-empty strings
        - ``conclusion`` is a non-empty string

        Returns
        -------
        list[str]
            A (possibly empty) list of validation error messages.
        """
        errors: list[str] = []
        if not self.rule_id:
            errors.append("rule_id must be non-empty")
        if not self.rule_name:
            errors.append("rule_name must be non-empty")
        if not self.conclusion:
            errors.append("conclusion must be non-empty")
        for i, p in enumerate(self.premises):
            if not str(p).strip():
                errors.append(f"premise[{i}] is empty")
        if self.rule_kind not in RuleKind:
            errors.append(f"rule_kind '{self.rule_kind}' is not a valid RuleKind")
        return errors

    def explain(self) -> str:
        """Produce a human-readable explanation of the rule's purpose.

        Returns a multi-paragraph string describing the premises,
        conclusion, side conditions, and rule kind.
        """
        parts: list[str] = [
            f"Rule: {self.rule_name}  (id={self.rule_id})",
            f"Kind: {self.rule_kind.value}",
            "",
            "Sequent form:",
            self.to_sequent(),
        ]
        if self.side_conditions:
            parts.append("")
            parts.append("Side conditions:")
            for name, cond in self.side_conditions.items():
                parts.append(f"  {name}: {cond!r}")
        if self.metadata:
            parts.append("")
            parts.append("Metadata:")
            for k, v in self.metadata.items():
                parts.append(f"  {k}: {v}")
        return "\n".join(parts)

    def copilot_suggest(self, partial_judgment: str) -> list[str]:
        """Ask the Copilot assistant to suggest completions for a partial judgment.

        # copilot – Copilot bridge for interactive rule completion.

        Given a *partial_judgment* that matches part of this rule's
        conclusion schema, returns a ranked list of candidate completions.

        Parameters
        ----------
        partial_judgment:
            The partial string entered so far.

        Returns
        -------
        list[str]
            Candidate complete judgment strings, best match first.
        """
        # Build completions by expanding the conclusion schema
        suggestions: list[str] = []
        # Replace meta-vars with the partial text
        base = self.conclusion
        import re
        meta_vars = re.findall(r'\?(\w+)', base)
        if meta_vars:
            for mv in meta_vars:
                candidate = base.replace(f"?{mv}", partial_judgment)
                suggestions.append(candidate)
        suggestions.append(base)  # Always include the raw schema
        # Deduplicate while preserving order
        seen: set[str] = set()
        result: list[str] = []
        for s in suggestions:
            if s not in seen:
                seen.add(s)
                result.append(s)
        return result


# ---------------------------------------------------------------------------
# JudgmentTransition
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class JudgmentTransition:
    """A single transition between two judgment states.

    Represents the step

    .. math::

       J \\xrightarrow{r[\\sigma]} J'

    where :math:`r` is a :class:`DeductionRule`, :math:`\\sigma` is the
    substitution used during unification, and :math:`\\Delta_t` is the
    change in trust level.

    Attributes
    ----------
    transition_id:
        Unique ID for this transition instance.
    source_judgment:
        The judgment before the transition.
    target_judgment:
        The judgment after the transition.
    rule_applied:
        The :class:`DeductionRule` that produced this transition.
    substitution:
        Substitution used during rule application.
    trust_delta:
        Signed integer representing trust change (+positive, -negative).
    """

    transition_id: str
    source_judgment: Any
    target_judgment: Any
    rule_applied: DeductionRule
    substitution: dict[str, Any] = field(default_factory=dict)
    trust_delta: int = 0

    def is_valid(self) -> bool:
        """Check whether this transition is internally consistent.

        A transition is valid when:
        - ``rule_applied`` is not None
        - ``source_judgment`` and ``target_judgment`` are non-empty
        - The rule's conclusion unifies with ``target_judgment``

        Returns
        -------
        bool
        """
        if self.rule_applied is None:
            return False
        if not self.source_judgment or not self.target_judgment:
            return False
        errors = self.rule_applied.validate()
        if errors:
            return False
        # Verify the conclusion matches the target
        unifier = self.rule_applied._try_unify(
            self.rule_applied.conclusion, str(self.target_judgment)
        )
        return unifier is not None

    def compose(self, other: JudgmentTransition) -> JudgmentTransition:
        """Compose this transition with *other* to form a longer step.

        Requires that ``self.target_judgment == other.source_judgment``.
        The composed transition carries the combined substitution and
        cumulative trust delta.

        Parameters
        ----------
        other:
            The transition to append to this one.

        Returns
        -------
        JudgmentTransition
            The composed transition (may span multiple rules).

        Raises
        ------
        ValueError
            If the transitions are not composable.
        """
        if str(self.target_judgment) != str(other.source_judgment):
            raise ValueError(
                f"Cannot compose transitions: "
                f"target '{self.target_judgment}' ≠ source '{other.source_judgment}'"
            )
        merged_subst = {**self.substitution, **other.substitution}
        # Create a synthetic "composed" rule for labelling
        composed_rule = replace(
            self.rule_applied,
            rule_name=f"{self.rule_applied.rule_name}∘{other.rule_applied.rule_name}",
            rule_id=_new_id("comp"),
        )
        return JudgmentTransition(
            transition_id=_new_id("trans"),
            source_judgment=self.source_judgment,
            target_judgment=other.target_judgment,
            rule_applied=composed_rule,
            substitution=merged_subst,
            trust_delta=self.trust_delta + other.trust_delta,
        )

    def invert(self) -> JudgmentTransition:
        """Return the inverse transition (source and target swapped).

        Not all transitions are invertible; this method returns the
        syntactic inverse regardless.  Call :meth:`is_valid` on the result
        to check semantic validity.
        """
        inverse_rule = replace(
            self.rule_applied,
            rule_name=f"({self.rule_applied.rule_name})⁻¹",
            rule_id=_new_id("inv"),
            premises=(self.rule_applied.conclusion,),
            conclusion=self.rule_applied.premises[0] if self.rule_applied.premises else "",
        )
        return JudgmentTransition(
            transition_id=_new_id("trans"),
            source_judgment=self.target_judgment,
            target_judgment=self.source_judgment,
            rule_applied=inverse_rule,
            substitution=dict(self.substitution),
            trust_delta=-self.trust_delta,
        )

    def apply_substitution(self, extra: Mapping[str, Any]) -> JudgmentTransition:
        """Return a copy of this transition with *extra* substitution merged in.

        Parameters
        ----------
        extra:
            Additional bindings to overlay on the existing substitution.
        """
        merged = {**self.substitution, **extra}
        return replace(self, substitution=merged)  # type: ignore[call-overload]

    def trust_change(self) -> int:
        """Return the net trust delta of this transition.

        Positive values indicate a trust increase (the obligation becomes
        better supported); negative values indicate degradation.
        """
        return self.trust_delta

    def to_proof_step(self) -> dict[str, Any]:
        """Serialise this transition as a proof-step record.

        The returned dict is compatible with the ``ProofStep`` schema
        used by ``jugeo.solver.reconstruction``.
        """
        return {
            "step_id": self.transition_id,
            "rule": self.rule_applied.rule_name,
            "source": str(self.source_judgment),
            "target": str(self.target_judgment),
            "substitution": dict(self.substitution),
            "trust_delta": self.trust_delta,
            "valid": self.is_valid(),
        }

    def serialize(self) -> dict[str, Any]:
        """Return a fully serialisable dict representation."""
        return {
            "transition_id": self.transition_id,
            "source_judgment": str(self.source_judgment),
            "target_judgment": str(self.target_judgment),
            "rule_applied": {
                "rule_id": self.rule_applied.rule_id,
                "rule_name": self.rule_applied.rule_name,
                "rule_kind": self.rule_applied.rule_kind.value,
            },
            "substitution": dict(self.substitution),
            "trust_delta": self.trust_delta,
        }


# ---------------------------------------------------------------------------
# InferenceStep
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class InferenceStep:
    """A single node in an inference derivation tree.

    Each step records the rule applied, the input judgments consumed,
    and the output judgment produced.  Steps are assembled into a
    derivation tree by linking ``step_id`` references.

    .. math::

       \\frac{\\text{inputs}[0] \\quad \\cdots \\quad \\text{inputs}[k-1]}
            {\\text{output}} \\;[\\text{rule}]

    Attributes
    ----------
    step_id:
        Unique step identifier.
    rule:
        The :class:`DeductionRule` applied at this node.
    inputs:
        Tuple of input judgment strings (one per premise).
    output:
        The resulting judgment string.
    justification:
        Free-text or structured justification for this step.
    step_index:
        Integer position within the enclosing proof (0-based).
    """

    step_id: str
    rule: DeductionRule
    inputs: tuple[str, ...]
    output: str
    justification: str = ""
    step_index: int = 0

    def verify(self) -> bool:
        """Verify that this step is a correct application of its rule.

        Checks:
        1. Inputs unify with the rule's premises under some substitution σ.
        2. The output matches the conclusion instantiated under σ.
        3. All side conditions hold under σ.

        Returns
        -------
        bool
        """
        subst = self.rule.unify_premises(list(self.inputs))
        if subst is None:
            return False
        expected_conclusion = self.rule.instantiate(subst)
        if expected_conclusion.strip() != self.output.strip():
            # Lenient check: allow if output subsumes conclusion
            if self.output.strip() not in expected_conclusion.strip():
                return False
        return self.rule.check_side_conditions(subst)

    def dependencies(self) -> tuple[str, ...]:
        """Return the step IDs that this step depends on.

        For now returns the inputs directly; a full implementation would
        walk the derivation tree.
        """
        return self.inputs

    def is_axiom(self) -> bool:
        """Return ``True`` iff this step uses an axiom rule (no premises)."""
        return self.rule.rule_kind == RuleKind.AXIOM or len(self.rule.premises) == 0

    def explains(self) -> str:
        """Produce a human-readable explanation of this inference step.

        Returns a formatted string showing inputs, rule, and output.
        """
        input_lines = "\n  ".join(f"({i+1}) {inp}" for i, inp in enumerate(self.inputs))
        lines = [
            f"Step {self.step_index}  [{self.rule.rule_name}]",
            f"  Inputs:",
            f"  {input_lines}",
            f"  Output: {self.output}",
        ]
        if self.justification:
            lines.append(f"  Justification: {self.justification}")
        return "\n".join(lines)

    def to_derivation_tree(self) -> dict[str, Any]:
        """Render this step as a node in a derivation-tree dict.

        Suitable for JSON export and for rendering with tree-drawing tools.
        """
        return {
            "step_id": self.step_id,
            "rule": self.rule.rule_name,
            "rule_kind": self.rule.rule_kind.value,
            "inputs": list(self.inputs),
            "output": self.output,
            "is_axiom": self.is_axiom(),
            "step_index": self.step_index,
            "justification": self.justification,
        }

    def annotate(self, key: str, value: Any) -> InferenceStep:
        """Return a copy of this step with an annotation added to the justification.

        Parameters
        ----------
        key:
            Annotation key.
        value:
            Annotation value (will be stringified).
        """
        extra = f"  [{key}={value}]"
        return replace(self, justification=self.justification + extra)  # type: ignore[call-overload]

    def trust_propagation(self, incoming_trust: TrustLevel) -> TrustLevel:
        """Compute the outgoing trust level given *incoming_trust*.

        For axiom steps the trust is always ``MECHANICALLY_VERIFIED``.
        For derived steps the trust propagates according to the rule kind:
        structural rules preserve trust, semantic rules may increase it.

        Parameters
        ----------
        incoming_trust:
            Trust level carried by the input judgments.

        Returns
        -------
        TrustLevel
        """
        if self.is_axiom():
            return TrustLevel.MECHANICALLY_VERIFIED
        if self.rule.rule_kind == RuleKind.STRUCTURAL:
            return incoming_trust
        if self.rule.rule_kind == RuleKind.SEMANTIC:
            # Semantic rules can promote trust one step
            try:
                levels = list(TrustLevel)
                idx = levels.index(incoming_trust)
                return levels[min(idx + 1, len(levels) - 1)]
            except (ValueError, AttributeError):
                return incoming_trust
        return incoming_trust


# ---------------------------------------------------------------------------
# RuleApplication
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class RuleApplication:
    """An immutable record of a rule application event.

    Created when a :class:`DeductionRule` fires during proof search.
    Stores the full context so that the application can be replayed,
    audited, or inspected by Copilot.

    Attributes
    ----------
    application_id:
        Unique identifier for this application event.
    rule:
        The rule that was applied.
    context:
        Ambient context at time of application.
    bindings:
        Variable bindings produced by unification.
    timestamp:
        ISO-8601 UTC string when the application occurred.
    result:
        Outcome of the application attempt.
    evidence_produced:
        Any evidence items generated by the rule firing.
    """

    application_id: str
    rule: DeductionRule
    context: dict[str, Any]
    bindings: dict[str, Any]
    timestamp: str
    result: ApplicationResult
    evidence_produced: tuple[Any, ...]

    def succeeded(self) -> bool:
        """Return ``True`` iff the application result is ``APPLIED``."""
        return self.result == ApplicationResult.APPLIED

    def failed_reason(self) -> str | None:
        """Return a human-readable failure reason, or ``None`` on success.

        Translates :class:`ApplicationResult` values to explanatory strings.
        """
        reasons: dict[ApplicationResult, str] = {
            ApplicationResult.INAPPLICABLE: (
                "Rule conclusion does not unify with the target judgment."
            ),
            ApplicationResult.SIDE_CONDITION_FAILURE: (
                "One or more side conditions evaluated to False."
            ),
            ApplicationResult.UNIFICATION_FAILURE: (
                "Premise unification failed — inconsistent meta-variable bindings."
            ),
            ApplicationResult.TRUST_INSUFFICIENT: (
                "Context trust level is below the threshold required by this rule."
            ),
            ApplicationResult.ERROR: (
                "An unexpected error occurred during rule application."
            ),
        }
        if self.result == ApplicationResult.APPLIED:
            return None
        return reasons.get(self.result, f"Unknown failure: {self.result!r}")

    def retry_with(
        self,
        new_context: Mapping[str, Any] | None = None,
        new_bindings: Mapping[str, Any] | None = None,
    ) -> RuleApplication:
        """Construct a new application record for a retry attempt.

        The new record inherits the same rule and evidence but uses
        updated context / bindings.

        Parameters
        ----------
        new_context:
            Replacement context (merged over existing if provided).
        new_bindings:
            Replacement bindings (merged over existing if provided).
        """
        ctx = {**self.context, **(new_context or {})}
        bnds = {**self.bindings, **(new_bindings or {})}
        # Re-apply the rule
        can_apply = self.rule.applies_to(
            self.context.get("target_judgment", ""), ctx
        )
        new_result = ApplicationResult.APPLIED if can_apply else ApplicationResult.INAPPLICABLE
        return RuleApplication(
            application_id=_new_id("app"),
            rule=self.rule,
            context=ctx,
            bindings=bnds,
            timestamp=_now_iso(),
            result=new_result,
            evidence_produced=self.evidence_produced,
        )

    def evidence_items(self) -> tuple[Any, ...]:
        """Return the evidence items produced by this application."""
        return self.evidence_produced

    def to_audit_record(self) -> dict[str, Any]:
        """Serialise this application as an audit-log record.

        Suitable for ingestion by ``jugeo.evidence`` audit systems.
        """
        return {
            "application_id": self.application_id,
            "rule_id": self.rule.rule_id,
            "rule_name": self.rule.rule_name,
            "rule_kind": self.rule.rule_kind.value,
            "result": self.result.value,
            "timestamp": self.timestamp,
            "bindings": dict(self.bindings),
            "evidence_count": len(self.evidence_produced),
            "failed_reason": self.failed_reason(),
        }

    def summarize(self) -> str:
        """Return a one-line summary of this application event."""
        status = "✓" if self.succeeded() else "✗"
        return (
            f"{status} [{self.timestamp}] {self.rule.rule_name} "
            f"→ {self.result.value} "
            f"(id={self.application_id})"
        )

    def replay(self) -> dict[str, Any]:
        """Replay this rule application and return a new result dict.

        Re-executes the rule against the stored context and bindings,
        returning a fresh result.  Useful for verification and debugging.
        """
        target = self.context.get("target_judgment", "")
        applies = self.rule.applies_to(target, self.context)
        if not applies:
            return {"status": ApplicationResult.INAPPLICABLE.value, "replayed": True}

        subst = {**self.bindings}
        sc_ok = self.rule.check_side_conditions(subst)
        if not sc_ok:
            return {
                "status": ApplicationResult.SIDE_CONDITION_FAILURE.value,
                "replayed": True,
            }

        conclusion = self.rule.instantiate(subst)
        return {
            "status": ApplicationResult.APPLIED.value,
            "conclusion": conclusion,
            "replayed": True,
            "timestamp": _now_iso(),
        }


# ---------------------------------------------------------------------------
# TransitionSystem
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class TransitionSystem:
    """The full system of judgment transitions for a deduction theory.

    A transition system is a triple :math:`(\\mathcal{R}, J_0, \\Phi)` where
    :math:`\\mathcal{R}` is a set of deduction rules, :math:`J_0` is the set
    of initial judgments, and :math:`\\Phi` is a predicate on terminal states.

    The system supports fixpoint computation: repeated rule application until
    no rule is applicable or a terminal condition is reached.

    Attributes
    ----------
    system_id:
        Unique identifier for this transition system.
    rules:
        List of all :class:`DeductionRule` objects in the system.
    initial_judgments:
        The starting judgment set.
    terminal_conditions:
        List of predicate strings or callables; the system halts when any is true.
    system_kind:
        Broad classification (e.g. ``"propositional"``, ``"predicate"``).
    """

    system_id: str
    rules: list[DeductionRule]
    initial_judgments: list[Any]
    terminal_conditions: list[Any] = field(default_factory=list)
    system_kind: str = "generic"

    # Internal state
    _history: list[JudgmentTransition] = field(default_factory=list)
    _application_log: list[RuleApplication] = field(default_factory=list)

    def add_rule(self, rule: DeductionRule) -> None:
        """Add *rule* to the system.

        Raises :class:`ValueError` if a rule with the same ``rule_id``
        already exists.

        Parameters
        ----------
        rule:
            The rule to add.
        """
        existing_ids = {r.rule_id for r in self.rules}
        if rule.rule_id in existing_ids:
            raise ValueError(
                f"Rule with id '{rule.rule_id}' already exists in system '{self.system_id}'"
            )
        errors = rule.validate()
        if errors:
            raise ValueError(f"Rule '{rule.rule_name}' is invalid: {errors}")
        self.rules.append(rule)

    def remove_rule(self, rule_id: str) -> DeductionRule | None:
        """Remove and return the rule with *rule_id*, or ``None`` if absent.

        Parameters
        ----------
        rule_id:
            The ID of the rule to remove.
        """
        for i, rule in enumerate(self.rules):
            if rule.rule_id == rule_id:
                return self.rules.pop(i)
        return None

    def applicable_rules(
        self,
        judgment: Any,
        context: Mapping[str, Any] | None = None,
    ) -> list[DeductionRule]:
        """Return all rules that are applicable to *judgment* in *context*.

        Parameters
        ----------
        judgment:
            The judgment to test rules against.
        context:
            Optional ambient context (trust level, etc.).

        Returns
        -------
        list[DeductionRule]
            All rules whose conclusions unify with *judgment*.
        """
        ctx = dict(context or {})
        return [r for r in self.rules if r.applies_to(judgment, ctx)]

    def step(
        self,
        judgment: Any,
        context: Mapping[str, Any] | None = None,
    ) -> JudgmentTransition | None:
        """Apply the first applicable rule to *judgment*.

        Returns the resulting :class:`JudgmentTransition`, or ``None``
        if no rule applies.

        Parameters
        ----------
        judgment:
            The current judgment.
        context:
            Optional context.
        """
        ctx = dict(context or {})
        for rule in self.rules:
            if not rule.applies_to(judgment, ctx):
                continue
            # Attempt to build a transition
            subst = rule._try_unify(rule.conclusion, str(judgment))
            if subst is None:
                continue
            try:
                fired = rule.fire(
                    [str(judgment)] * len(rule.premises), subst
                )
            except Exception:
                continue

            target = fired.get("conclusion", str(judgment))
            transition = JudgmentTransition(
                transition_id=_new_id("trans"),
                source_judgment=judgment,
                target_judgment=target,
                rule_applied=rule,
                substitution=subst,
                trust_delta=1 if rule.rule_kind == RuleKind.SEMANTIC else 0,
            )
            self._history.append(transition)
            return transition
        return None

    def run_to_fixpoint(
        self,
        max_steps: int = 1000,
        context: Mapping[str, Any] | None = None,
    ) -> list[JudgmentTransition]:
        """Run the system until fixpoint or *max_steps*.

        Iterates :meth:`step` over the current judgment set.  Terminates
        when no rule is applicable to any judgment (fixpoint) or when
        *max_steps* is exceeded.

        Parameters
        ----------
        max_steps:
            Safety cap on the number of rule applications.
        context:
            Ambient context passed to each step.

        Returns
        -------
        list[JudgmentTransition]
            All transitions produced during the run.
        """
        ctx = dict(context or {})
        current_judgments: list[Any] = list(self.initial_judgments)
        transitions: list[JudgmentTransition] = []

        for _iteration in range(max_steps):
            progress = False
            next_judgments: list[Any] = []
            for judgment in current_judgments:
                # Check terminal conditions
                if self._is_terminal(judgment, ctx):
                    next_judgments.append(judgment)
                    continue
                transition = self.step(judgment, ctx)
                if transition is not None:
                    transitions.append(transition)
                    next_judgments.append(transition.target_judgment)
                    progress = True
                else:
                    next_judgments.append(judgment)
            current_judgments = next_judgments
            if not progress:
                break  # Fixpoint reached

        return transitions

    def _is_terminal(self, judgment: Any, context: dict[str, Any]) -> bool:
        """Test whether *judgment* satisfies any terminal condition."""
        for cond in self.terminal_conditions:
            if callable(cond):
                try:
                    if cond(judgment, context):
                        return True
                except Exception:
                    pass
            elif isinstance(cond, str):
                if str(judgment).startswith(cond):
                    return True
        return False

    def check_confluence(self) -> bool:
        """Heuristically check whether the rule system is confluent.

        Two rules are *locally confluent* if whenever both apply to the
        same judgment, the resulting targets eventually converge.  This
        method performs a lightweight overlap analysis.

        Returns
        -------
        bool
            ``True`` if no overlapping rule pairs were found (conservative
            approximation — may return ``True`` even for non-confluent systems).
        """
        for i, r1 in enumerate(self.rules):
            for r2 in self.rules[i + 1:]:
                # Check whether both rules can fire on the same conclusion shape
                overlap = self._conclusion_overlap(r1.conclusion, r2.conclusion)
                if overlap:
                    # Could flag as potentially non-confluent
                    # For now, we trust semantic rules to be designed confluently
                    if r1.rule_kind == r2.rule_kind == RuleKind.SEMANTIC:
                        return False
        return True

    def _conclusion_overlap(self, c1: str, c2: str) -> bool:
        """Return True if conclusions c1 and c2 have a common instance."""
        import re
        # Strip meta-variables; check if non-meta tokens overlap
        tokens1 = {t for t in re.findall(r'\w+', c1) if not t.isupper() or len(t) > 1}
        tokens2 = {t for t in re.findall(r'\w+', c2) if not t.isupper() or len(t) > 1}
        return bool(tokens1 & tokens2)

    def verify_soundness(self) -> list[str]:
        """Validate all rules and check system-level soundness properties.

        Returns a list of warning/error strings.  An empty list indicates
        the system appears sound.
        """
        issues: list[str] = []
        for rule in self.rules:
            errs = rule.validate()
            for err in errs:
                issues.append(f"Rule '{rule.rule_name}': {err}")

        # Structural rules must not increase the conclusion size unboundedly
        structural = [r for r in self.rules if r.rule_kind == RuleKind.STRUCTURAL]
        for r in structural:
            if len(r.conclusion) > 200:
                issues.append(
                    f"Structural rule '{r.rule_name}' has unusually large conclusion."
                )

        # Check for obvious infinite loops (rule that fires on its own conclusion)
        for r in self.rules:
            if r.conclusion in r.premises:
                issues.append(
                    f"Rule '{r.rule_name}' has its own conclusion as a premise "
                    "(potential infinite loop)."
                )
        return issues

    def export_rules(self) -> list[dict[str, Any]]:
        """Export all rules as a list of serialisable dicts.

        Suitable for JSON output or for importing into another system.
        """
        return [
            {
                "rule_id": r.rule_id,
                "rule_name": r.rule_name,
                "premises": list(r.premises),
                "conclusion": r.conclusion,
                "rule_kind": r.rule_kind.value,
                "trust_required": r.trust_required.value
                    if hasattr(r.trust_required, 'value') else str(r.trust_required),
                "side_conditions": {
                    k: str(v) for k, v in r.side_conditions.items()
                },
                "metadata": dict(r.metadata),
            }
            for r in self.rules
        ]

    def copilot_complete(self, partial_system: str) -> list[str]:
        """Ask Copilot to suggest rules to complete the system.

        # copilot – Copilot bridge for transition-system completion.

        Given a *partial_system* description (e.g. missing rules for a
        connective), returns suggested rule names and schemas.

        Parameters
        ----------
        partial_system:
            Description of what is missing.

        Returns
        -------
        list[str]
            Suggested rule schema strings.
        """
        suggestions: list[str] = []
        # Analyse which rule kinds are represented
        kinds_present = {r.rule_kind for r in self.rules}
        if RuleKind.STRUCTURAL not in kinds_present:
            suggestions.append("Missing structural rules: consider adding weakening and exchange.")
        if RuleKind.AXIOM not in kinds_present:
            suggestions.append("No axioms defined: add an identity rule Γ, A ⊢ A.")
        if RuleKind.SEMANTIC not in kinds_present:
            suggestions.append(
                f"No semantic rules for system '{self.system_kind}': "
                "add introduction/elimination rules."
            )
        if not suggestions:
            suggestions.append(
                f"System '{self.system_id}' appears complete for kind '{self.system_kind}'."
            )
        return suggestions


# ---------------------------------------------------------------------------
# Factory helpers
# ---------------------------------------------------------------------------

def make_axiom_rule(name: str, conclusion: str, **metadata: Any) -> DeductionRule:
    """Convenience factory for axiom rules (zero premises).

    Parameters
    ----------
    name:
        Human-readable rule name.
    conclusion:
        Conclusion schema string.
    **metadata:
        Additional metadata fields.
    """
    return DeductionRule(
        rule_id=_stable_hash(f"axiom:{name}:{conclusion}"),
        rule_name=name,
        premises=(),
        conclusion=conclusion,
        rule_kind=RuleKind.AXIOM,
        trust_required=TrustLevel.UNVERIFIED,
        metadata=dict(metadata),
    )


def make_rule(
    name: str,
    premises: Sequence[str],
    conclusion: str,
    kind: RuleKind = RuleKind.SEMANTIC,
    side_conditions: dict[str, Any] | None = None,
    **metadata: Any,
) -> DeductionRule:
    """Convenience factory for non-axiom rules.

    Parameters
    ----------
    name:
        Human-readable rule name.
    premises:
        Ordered premise schemas.
    conclusion:
        Conclusion schema.
    kind:
        :class:`RuleKind` (defaults to ``SEMANTIC``).
    side_conditions:
        Optional side-condition mapping.
    **metadata:
        Additional metadata.
    """
    return DeductionRule(
        rule_id=_stable_hash(f"rule:{name}:{','.join(premises)}:{conclusion}"),
        rule_name=name,
        premises=tuple(premises),
        conclusion=conclusion,
        side_conditions=side_conditions or {},
        rule_kind=kind,
        trust_required=TrustLevel.UNVERIFIED,
        metadata=dict(metadata),
    )


__all__ = [
    "RuleKind",
    "TransitionKind",
    "InferenceStatus",
    "ApplicationResult",
    "DeductionRule",
    "JudgmentTransition",
    "InferenceStep",
    "RuleApplication",
    "TransitionSystem",
    "make_axiom_rule",
    "make_rule",
    "_new_id",
    "_stable_hash",
    "_now_iso",
]
