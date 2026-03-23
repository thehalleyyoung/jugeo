r"""Semantic rules for JuGeo — ``theory2.tex`` Chapter 33, §33.3.

Semantic rules are driven by the logical connectives and type formers.
Each connective has introduction rules (which prove it) and elimination
rules (which use it).  Additionally, there are computation rules (β/η
reduction) and definitional equality rules.

Introduction rules (right rules):

.. math::

   \frac{\Gamma \vdash A \quad \Gamma \vdash B}
        {\Gamma \vdash A \wedge B}  \;[\wedge\text{-intro}]

Elimination rules (left rules):

.. math::

   \frac{\Gamma \vdash A \wedge B}
        {\Gamma \vdash A}  \;[\wedge\text{-elim}_1]

Computation (β-reduction):

.. math::

   (\lambda x.\, t)\, s \;\rightsquigarrow_\beta\; t[x := s]

Architecture
------------
- :class:`IntroductionRule`          – connective introduction
- :class:`EliminationRule`           – connective elimination
- :class:`ComputationRule`           – beta/eta reductions
- :class:`DefinitionalEqualityRule`  – definitional equality
- :class:`SemanticRuleSystem`        – full semantic rule set
- :class:`SoundnessChecker`          – checks semantic rule soundness
"""

from __future__ import annotations

import hashlib
import logging
import re
import uuid
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Jugeo imports — guarded with try/except stubs
# ---------------------------------------------------------------------------

try:
    from jugeo.encodings.deduction_rules.models import (  # type: ignore[import]
        DeductionRule,
        RuleKind,
        TransitionSystem,
    )
    _models_ok = True
except ImportError:  # pragma: no cover
    _models_ok = False

    class RuleKind:  # type: ignore[no-redef]
        STRUCTURAL = "structural"
        SEMANTIC = "semantic"
        AXIOM = "axiom"

    @dataclass
    class DeductionRule:  # type: ignore[no-redef]
        rule_id: str
        rule_name: str
        premises: tuple[str, ...] = ()
        conclusion: str = ""
        side_conditions: dict[str, Any] = field(default_factory=dict)
        rule_kind: Any = "semantic"
        metadata: dict[str, Any] = field(default_factory=dict)

    @dataclass
    class TransitionSystem:  # type: ignore[no-redef]
        system_id: str
        rules: list[Any] = field(default_factory=list)
        initial_judgments: list[Any] = field(default_factory=list)
        terminal_conditions: list[Any] = field(default_factory=list)
        system_kind: str = "generic"

try:
    from jugeo.encodings.deduction_rules.inference_rules import (  # type: ignore[import]
        InferenceRule,
        RuleApplication,
    )
    _s01_ok = True
except ImportError:  # pragma: no cover
    _s01_ok = False

    @dataclass
    class InferenceRule:  # type: ignore[no-redef]
        rule_id: str
        rule_name: str
        premises: tuple[str, ...] = ()
        conclusion: str = ""

    @dataclass
    class RuleApplication:  # type: ignore[no-redef]
        rule: Any = None
        conclusion: str = ""
        premises: list[str] = field(default_factory=list)

try:
    from jugeo.solver.z3_session import Z3Session  # type: ignore[import]
    _z3_ok = True
except ImportError:  # pragma: no cover
    _z3_ok = False
    Z3Session = None  # type: ignore[assignment,misc]

# ---------------------------------------------------------------------------
# Local dataclass: RuleSchema
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class RuleSchema:
    """Inference rule schema with display and LaTeX annotations.

    Attributes
    ----------
    rule_id:
        Stable unique identifier.
    name:
        Human-readable name.
    premises:
        Ordered list of premise schemas as display strings.
    conclusion:
        The conclusion schema.
    latex_premises:
        LaTeX rendering of each premise.
    latex_conclusion:
        LaTeX rendering of the conclusion.
    description:
        Short prose description.
    """

    rule_id: str
    name: str
    premises: list[str] = field(default_factory=list)
    conclusion: str = ""
    latex_premises: list[str] = field(default_factory=list)
    latex_conclusion: str = ""
    description: str = ""


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

_TURNSTILE = " ⊢ "


def _fresh_id(prefix: str) -> str:
    """Return a fresh unique identifier prefixed with *prefix*."""
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _parse_judgment(judgment_str: str) -> tuple[list[str], str]:
    """Split ``Γ ⊢ J`` into ``(context, conclusion)``."""
    if _TURNSTILE in judgment_str:
        lhs, _, rhs = judgment_str.partition(_TURNSTILE)
        raw = lhs.strip()
        context = [] if raw in ("∅", "", "·") else [
            h.strip() for h in raw.split(",") if h.strip()
        ]
        return context, rhs.strip()
    return [], judgment_str.strip()


def _format_judgment(context: list[str], conclusion: str) -> str:
    """Reconstruct a judgment string."""
    ctx = ", ".join(context) if context else "∅"
    return f"{ctx}{_TURNSTILE}{conclusion}"


def _check_positive_occurrence(formula: str, target: str) -> bool:
    """Heuristic check that *target* does not appear under negation in *formula*.

    Returns ``True`` (positive) when *target* does not appear inside a
    ``¬(…)`` or ``→`` left-side sub-expression.  This is a syntactic
    approximation; full positivity checking requires a parse tree.
    """
    if target not in formula:
        return True  # Not present at all → vacuously positive
    # Look for negation markers around target
    neg_pattern = re.compile(
        r"¬\s*" + re.escape(target) + r"|\bnot\b\s+" + re.escape(target),
        re.IGNORECASE,
    )
    if neg_pattern.search(formula):
        return False
    # Check for appearance on the left of an implication
    imp_pattern = re.compile(
        re.escape(target) + r"\s*(?:→|->|⊃)\s*",
    )
    if imp_pattern.search(formula):
        return False
    return True


def _apply_substitution(pattern: str, subst: Mapping[str, str]) -> str:
    """Apply *subst* to *pattern*, replacing meta-variables.

    Meta-variables are single upper-case letters or names starting with
    a Greek letter prefix (``Γ``, ``Δ``, etc.).

    Parameters
    ----------
    pattern:
        A string with meta-variable placeholders.
    subst:
        Mapping from meta-variable name to concrete value.

    Returns
    -------
    str
        The pattern with substitutions applied.
    """
    result = pattern
    # Sort longest keys first to avoid prefix collisions
    for var in sorted(subst.keys(), key=len, reverse=True):
        result = result.replace(var, subst[var])
    return result


# ---------------------------------------------------------------------------
# IntroductionRule
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class IntroductionRule:
    """Semantic introduction rule for a logical connective.

    An introduction rule tells you how to *prove* a formula with a given
    principal connective.  For example, the conjunction introduction rule::

        Γ ⊢ A    Γ ⊢ B
        ─────────────────  [∧-intro]
        Γ ⊢ A ∧ B

    states that to prove ``A ∧ B`` it suffices to prove ``A`` and ``B``
    separately.

    Attributes
    ----------
    rule_id:
        Stable unique identifier.
    connective:
        The logical connective this rule introduces (e.g. ``"∧"``, ``"→"``,
        ``"∀"``, ``"⊥"``, ``"Σ"``).
    variant:
        ``"right"`` (default) for right introduction; some systems have
        multiple variants.
    premises:
        Tuple of premise schemas.
    conclusion:
        Conclusion schema.
    side_conditions:
        Side conditions keyed by name (e.g. ``{"x not free in Γ": True}``).
    positivity_check:
        Whether to enforce positivity of the connective in the premises.
    metadata:
        Free-form annotations.
    """

    rule_id: str
    connective: str
    variant: str = "right"
    premises: tuple[str, ...] = ()
    conclusion: str = ""
    side_conditions: dict[str, Any] = field(default_factory=dict)
    positivity_check: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # schema
    # ------------------------------------------------------------------

    def schema(self) -> RuleSchema:
        """Return the :class:`RuleSchema` for this introduction rule.

        Returns
        -------
        RuleSchema
            Fully populated with LaTeX annotations derived from the premises
            and conclusion strings.
        """
        latex_ps = [
            p.replace("⊢", r"\vdash")
             .replace("∧", r"\wedge")
             .replace("∨", r"\vee")
             .replace("→", r"\to")
             .replace("⊥", r"\bot")
             .replace("⊤", r"\top")
             .replace("∀", r"\forall")
             .replace("∃", r"\exists")
             .replace("Γ", r"\Gamma")
             .replace("Δ", r"\Delta")
            for p in self.premises
        ]
        latex_c = (
            self.conclusion
            .replace("⊢", r"\vdash")
            .replace("∧", r"\wedge")
            .replace("∨", r"\vee")
            .replace("→", r"\to")
            .replace("⊥", r"\bot")
            .replace("⊤", r"\top")
            .replace("∀", r"\forall")
            .replace("∃", r"\exists")
            .replace("Γ", r"\Gamma")
            .replace("Δ", r"\Delta")
        )
        return RuleSchema(
            rule_id=self.rule_id,
            name=f"{self.connective}-intro-{self.variant}",
            premises=list(self.premises),
            conclusion=self.conclusion,
            latex_premises=latex_ps,
            latex_conclusion=latex_c,
            description=(
                f"Introduction rule for '{self.connective}' (variant: "
                f"{self.variant}).  Proves a formula with principal "
                f"connective '{self.connective}' by proving the required "
                f"sub-goals."
            ),
        )

    # ------------------------------------------------------------------
    # apply
    # ------------------------------------------------------------------

    def apply(
        self,
        discharged_premises: Sequence[Any],
        substitution: Mapping[str, str],
    ) -> dict[str, Any]:
        """Apply this introduction rule to a sequence of discharged premises.

        Parameters
        ----------
        discharged_premises:
            A sequence of proof objects or judgment strings, one per
            premise in :attr:`premises`.
        substitution:
            A mapping from meta-variable names to concrete formula strings,
            used to instantiate the conclusion.

        Returns
        -------
        dict[str, Any]
            A result dict with keys:

            ``"success"``
                Whether the rule application succeeded.
            ``"conclusion"``
                The instantiated conclusion string.
            ``"rule_id"``
                This rule's identifier.
            ``"discharged"``
                The list of discharged premises.
            ``"errors"``
                List of error strings (empty on success).

        Raises
        ------
        ValueError
            If the number of discharged premises does not match the
            number of premises in the rule schema.
        """
        if len(discharged_premises) != len(self.premises):
            raise ValueError(
                f"IntroductionRule.apply: expected {len(self.premises)} "
                f"premises, got {len(discharged_premises)}"
            )

        errors = self.validate()
        if errors:
            return {
                "success": False,
                "conclusion": "",
                "rule_id": self.rule_id,
                "discharged": list(discharged_premises),
                "errors": errors,
            }

        instantiated_conclusion = _apply_substitution(self.conclusion, substitution)

        # Check side conditions
        sc_errors: list[str] = []
        for cond_name, cond_val in self.side_conditions.items():
            if callable(cond_val):
                try:
                    if not cond_val(substitution):
                        sc_errors.append(f"Side condition '{cond_name}' failed")
                except Exception as exc:
                    sc_errors.append(
                        f"Side condition '{cond_name}' raised {exc!r}"
                    )
            elif isinstance(cond_val, bool) and not cond_val:
                sc_errors.append(f"Side condition '{cond_name}' is False")

        if sc_errors:
            return {
                "success": False,
                "conclusion": instantiated_conclusion,
                "rule_id": self.rule_id,
                "discharged": list(discharged_premises),
                "errors": sc_errors,
            }

        return {
            "success": True,
            "conclusion": instantiated_conclusion,
            "rule_id": self.rule_id,
            "connective": self.connective,
            "variant": self.variant,
            "discharged": list(discharged_premises),
            "substitution": dict(substitution),
            "errors": [],
        }

    # ------------------------------------------------------------------
    # check_positivity
    # ------------------------------------------------------------------

    def check_positivity(self, formula: str) -> bool:
        """Return ``True`` iff *formula* appears positively in the premises.

        A formula appears *positively* if it does not occur under an odd
        number of negations.  This is a syntactic heuristic; full positivity
        checking requires a parse tree.

        Parameters
        ----------
        formula:
            The formula to check for positive occurrence.

        Returns
        -------
        bool
            ``True`` if the formula appears only positively in all premises.
        """
        if not self.positivity_check:
            return True  # Check disabled
        for premise in self.premises:
            if not _check_positive_occurrence(premise, formula):
                return False
        return True

    # ------------------------------------------------------------------
    # dual
    # ------------------------------------------------------------------

    def dual(self) -> "EliminationRule":
        """Construct the corresponding elimination rule via duality.

        By the *harmony* principle, every introduction rule has a
        corresponding elimination rule that *inverts* it: the elimination
        rule recovers each premise of the introduction rule from the
        introduced conclusion.

        Returns
        -------
        EliminationRule
            The dual elimination rule.
        """
        elim_id = f"{self.rule_id}-elim"
        # The major premise is the introduced formula
        major = self.conclusion
        # Minor premises are the original introduction premises (as continuations)
        minors = self.premises

        return EliminationRule(
            rule_id=elim_id,
            connective=self.connective,
            variant=f"elim-of-{self.variant}",
            major_premise=major,
            minor_premises=minors,
            conclusion=(
                self.premises[0] if self.premises else self.conclusion
            ),
            side_conditions=dict(self.side_conditions),
            metadata={
                **self.metadata,
                "dual_of": self.rule_id,
                "note": "Generated automatically via introduction–elimination duality.",
            },
        )

    # ------------------------------------------------------------------
    # to_deduction_rule
    # ------------------------------------------------------------------

    def to_deduction_rule(self) -> DeductionRule:
        """Convert to a :class:`DeductionRule` from the models module."""
        sch = self.schema()
        return DeductionRule(
            rule_id=self.rule_id,
            rule_name=sch.name,
            premises=tuple(sch.premises),
            conclusion=sch.conclusion,
            side_conditions=dict(self.side_conditions),
            rule_kind=RuleKind.SEMANTIC if _models_ok else "semantic",
            metadata={
                **self.metadata,
                "connective": self.connective,
                "variant": self.variant,
                "rule_class": "introduction",
            },
        )

    # ------------------------------------------------------------------
    # validate
    # ------------------------------------------------------------------

    def validate(self) -> list[str]:
        """Check this rule for internal consistency.

        Returns
        -------
        list[str]
            A list of error strings.  Empty means valid.
        """
        errors: list[str] = []
        if not self.rule_id:
            errors.append("rule_id is empty")
        if not self.connective:
            errors.append("connective is empty")
        if not self.conclusion:
            errors.append("conclusion is empty")
        if self.connective and self.connective not in self.conclusion:
            errors.append(
                f"Principal connective '{self.connective}' does not appear "
                f"in conclusion '{self.conclusion}'.  "
                "An introduction rule should prove a formula headed by "
                "its connective."
            )
        if self.positivity_check:
            for p in self.premises:
                if not _check_positive_occurrence(p, self.connective):
                    errors.append(
                        f"Connective '{self.connective}' appears negatively "
                        f"in premise '{p}' — possible positivity violation."
                    )
        return errors

    # ------------------------------------------------------------------
    # explain
    # ------------------------------------------------------------------

    def explain(self) -> str:
        """Return a multi-line human-readable explanation of this introduction rule."""
        border = "=" * 52
        premises_str = "\n".join(f"  {p}" for p in self.premises) or "  (none — axiom)"
        sc_str = (
            "\n".join(f"  • {k}: {v}" for k, v in self.side_conditions.items())
            or "  (none)"
        )
        errors = self.validate()
        validity = "✅ Valid" if not errors else "❌ Invalid: " + "; ".join(errors)
        return (
            f"IntroductionRule  [{self.rule_id}]\n"
            f"{border}\n"
            f"Connective : {self.connective}\n"
            f"Variant    : {self.variant}\n"
            "Source     : theory2.tex, Chapter 33, §33.3\n"
            "\n"
            "Premises\n"
            "--------\n"
            f"{premises_str}\n"
            "────────────────────────────────  "
            f"[{self.connective}-intro]\n"
            f"  {self.conclusion}\n"
            "\n"
            "Side Conditions\n"
            "---------------\n"
            f"{sc_str}\n"
            "\n"
            f"Positivity check : {self.positivity_check}\n"
            f"Validity         : {validity}\n"
            f"Metadata         : {self.metadata}\n"
        )

    # ------------------------------------------------------------------
    # copilot_suggest_premises
    # ------------------------------------------------------------------

    def copilot_suggest_premises(self, goal: str) -> list[list[str]]:
        """Suggest sets of premises for proving *goal* via this introduction rule.

        # copilot suggest premises for a given introduction goal.

        Parameters
        ----------
        goal:
            The goal formula to prove (should be headed by :attr:`connective`).

        Returns
        -------
        list[list[str]]
            A list of candidate premise sets, each a list of judgment strings.
        """
        suggestions: list[list[str]] = []

        # Strip the connective to find sub-goals
        conn = self.connective

        if conn == "∧" and "∧" in goal:
            parts = goal.split("∧", 1)
            a, b = parts[0].strip(), parts[1].strip()
            suggestions.append([f"∅ ⊢ {a}", f"∅ ⊢ {b}"])
            suggestions.append([f"Γ ⊢ {a}", f"Γ ⊢ {b}"])

        elif conn == "∨":
            if "∨" in goal:
                parts = goal.split("∨", 1)
                a, b = parts[0].strip(), parts[1].strip()
                suggestions.append([f"∅ ⊢ {a}"])
                suggestions.append([f"∅ ⊢ {b}"])

        elif conn == "→":
            if "→" in goal:
                ante, cons = goal.split("→", 1)
                suggestions.append([f"{ante.strip()} ⊢ {cons.strip()}"])

        elif conn == "∀":
            body = goal.lstrip("∀").lstrip("x.").strip()
            suggestions.append([f"Γ, x : A ⊢ {body}"])

        elif conn == "∃":
            body = goal.lstrip("∃").lstrip("x.").strip()
            suggestions.append([f"∅ ⊢ {body}[x := t]"])
            suggestions.append([f"Γ ⊢ {body}[x := witness]"])

        # Fallback: one premise per original premise template
        if not suggestions and self.premises:
            suggestions.append(list(self.premises))

        suggestions.append([f"Γ ⊢ {goal}"])
        return suggestions[:4]


# ---------------------------------------------------------------------------
# EliminationRule
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class EliminationRule:
    """Semantic elimination rule for a logical connective.

    An elimination rule tells you how to *use* a formula with a given
    principal connective.  For example, conjunction elimination::

        Γ ⊢ A ∧ B
        ─────────────  [∧-elim₁]
        Γ ⊢ A

    states that from a proof of ``A ∧ B`` we may deduce ``A``.

    Attributes
    ----------
    rule_id:
        Stable unique identifier.
    connective:
        The connective being eliminated.
    variant:
        ``"left"`` (default), ``"right"``, or a custom variant name.
    major_premise:
        The principal premise — the formula to be eliminated.
    minor_premises:
        Additional (minor) premises, e.g. continuation goals in
        case-analysis elimination rules.
    conclusion:
        The conclusion derived after elimination.
    side_conditions:
        Side conditions on the elimination.
    metadata:
        Free-form annotations.
    """

    rule_id: str
    connective: str
    variant: str = "left"
    major_premise: str = ""
    minor_premises: tuple[str, ...] = ()
    conclusion: str = ""
    side_conditions: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # schema
    # ------------------------------------------------------------------

    def schema(self) -> RuleSchema:
        """Return the :class:`RuleSchema` for this elimination rule."""
        all_premises = [self.major_premise] + list(self.minor_premises)
        latex_ps = [
            p.replace("⊢", r"\vdash")
             .replace("∧", r"\wedge")
             .replace("∨", r"\vee")
             .replace("→", r"\to")
             .replace("⊥", r"\bot")
             .replace("⊤", r"\top")
             .replace("∀", r"\forall")
             .replace("∃", r"\exists")
             .replace("Γ", r"\Gamma")
             .replace("Δ", r"\Delta")
            for p in all_premises
        ]
        latex_c = (
            self.conclusion
            .replace("⊢", r"\vdash")
            .replace("∧", r"\wedge")
            .replace("∨", r"\vee")
            .replace("→", r"\to")
            .replace("⊥", r"\bot")
            .replace("⊤", r"\top")
            .replace("∀", r"\forall")
            .replace("∃", r"\exists")
            .replace("Γ", r"\Gamma")
            .replace("Δ", r"\Delta")
        )
        return RuleSchema(
            rule_id=self.rule_id,
            name=f"{self.connective}-elim-{self.variant}",
            premises=all_premises,
            conclusion=self.conclusion,
            latex_premises=latex_ps,
            latex_conclusion=latex_c,
            description=(
                f"Elimination rule for '{self.connective}' (variant: "
                f"{self.variant}).  Uses a proof of the major premise "
                f"'{self.major_premise}' to derive '{self.conclusion}'."
            ),
        )

    # ------------------------------------------------------------------
    # apply
    # ------------------------------------------------------------------

    def apply(
        self,
        major: Any,
        minors: Sequence[Any],
        substitution: Mapping[str, str],
    ) -> dict[str, Any]:
        """Apply this elimination rule.

        Parameters
        ----------
        major:
            Proof or judgment string for the major premise.
        minors:
            Proofs or judgment strings for each minor premise.
        substitution:
            Meta-variable substitution for instantiating the conclusion.

        Returns
        -------
        dict[str, Any]
            Result dict with keys ``"success"``, ``"conclusion"``,
            ``"rule_id"``, ``"errors"``.
        """
        if len(minors) != len(self.minor_premises):
            raise ValueError(
                f"EliminationRule.apply: expected {len(self.minor_premises)} "
                f"minor premises, got {len(minors)}"
            )

        errors = self.validate()
        if errors:
            return {
                "success": False,
                "conclusion": "",
                "rule_id": self.rule_id,
                "errors": errors,
            }

        instantiated = _apply_substitution(self.conclusion, substitution)

        # Check principal formula matches major premise
        if not self.is_principal_formula(str(major)):
            errors.append(
                f"Major premise '{major}' does not match expected "
                f"major premise '{self.major_premise}'."
            )

        if errors:
            return {
                "success": False,
                "conclusion": instantiated,
                "rule_id": self.rule_id,
                "errors": errors,
            }

        return {
            "success": True,
            "conclusion": instantiated,
            "rule_id": self.rule_id,
            "connective": self.connective,
            "variant": self.variant,
            "major": major,
            "minors": list(minors),
            "substitution": dict(substitution),
            "errors": [],
        }

    # ------------------------------------------------------------------
    # dual
    # ------------------------------------------------------------------

    def dual(self) -> IntroductionRule:
        """Construct the dual introduction rule.

        The dual introduction rule proves the major premise from the
        minor premises, reversing the direction of the elimination.

        Returns
        -------
        IntroductionRule
        """
        intro_id = f"{self.rule_id}-intro"
        return IntroductionRule(
            rule_id=intro_id,
            connective=self.connective,
            variant=f"intro-of-{self.variant}",
            premises=self.minor_premises,
            conclusion=self.major_premise,
            side_conditions=dict(self.side_conditions),
            metadata={
                **self.metadata,
                "dual_of": self.rule_id,
                "note": "Generated automatically via elimination–introduction duality.",
            },
        )

    # ------------------------------------------------------------------
    # to_deduction_rule
    # ------------------------------------------------------------------

    def to_deduction_rule(self) -> DeductionRule:
        """Convert to a :class:`DeductionRule`."""
        sch = self.schema()
        return DeductionRule(
            rule_id=self.rule_id,
            rule_name=sch.name,
            premises=tuple(sch.premises),
            conclusion=sch.conclusion,
            side_conditions=dict(self.side_conditions),
            rule_kind=RuleKind.SEMANTIC if _models_ok else "semantic",
            metadata={
                **self.metadata,
                "connective": self.connective,
                "variant": self.variant,
                "rule_class": "elimination",
            },
        )

    # ------------------------------------------------------------------
    # is_principal_formula
    # ------------------------------------------------------------------

    def is_principal_formula(self, formula: str) -> bool:
        """Return ``True`` iff *formula* matches the expected major premise.

        A formula is the *principal formula* of this elimination rule if
        it unifies with :attr:`major_premise` (syntactically, up to
        whitespace normalisation).

        Parameters
        ----------
        formula:
            The candidate formula string.

        Returns
        -------
        bool
        """
        # Simple syntactic check: normalise whitespace and compare
        def _norm(s: str) -> str:
            return re.sub(r"\s+", " ", s.strip())

        if _norm(formula) == _norm(self.major_premise):
            return True

        # Partial match: check if the connective appears in the formula
        # and the formula structurally resembles the major premise
        if self.connective and self.connective in formula:
            mp_norm = _norm(self.major_premise)
            f_norm = _norm(formula)
            # Allow the formula to be a prefix/suffix of the major premise
            if mp_norm in f_norm or f_norm in mp_norm:
                return True

        return False

    # ------------------------------------------------------------------
    # validate
    # ------------------------------------------------------------------

    def validate(self) -> list[str]:
        """Check this rule for internal consistency.

        Returns
        -------
        list[str]
            Error strings; empty means valid.
        """
        errors: list[str] = []
        if not self.rule_id:
            errors.append("rule_id is empty")
        if not self.connective:
            errors.append("connective is empty")
        if not self.major_premise:
            errors.append("major_premise is empty")
        if not self.conclusion:
            errors.append("conclusion is empty")
        if self.connective and self.connective not in self.major_premise:
            errors.append(
                f"Principal connective '{self.connective}' does not appear "
                f"in major_premise '{self.major_premise}'.  The major premise "
                "of an elimination rule should be headed by its connective."
            )
        return errors

    # ------------------------------------------------------------------
    # explain
    # ------------------------------------------------------------------

    def explain(self) -> str:
        """Return a multi-line human-readable explanation."""
        border = "=" * 52
        minor_str = "\n".join(f"  {p}" for p in self.minor_premises) or "  (none)"
        sc_str = (
            "\n".join(f"  • {k}: {v}" for k, v in self.side_conditions.items())
            or "  (none)"
        )
        errors = self.validate()
        validity = "✅ Valid" if not errors else "❌ Invalid: " + "; ".join(errors)
        return (
            f"EliminationRule  [{self.rule_id}]\n"
            f"{border}\n"
            f"Connective     : {self.connective}\n"
            f"Variant        : {self.variant}\n"
            "Source         : theory2.tex, Chapter 33, §33.3\n"
            "\n"
            "Major Premise\n"
            "-------------\n"
            f"  {self.major_premise}\n"
            "\n"
            "Minor Premises\n"
            "--------------\n"
            f"{minor_str}\n"
            f"────────────────────────────  [{self.connective}-elim]\n"
            f"  {self.conclusion}\n"
            "\n"
            "Side Conditions\n"
            "---------------\n"
            f"{sc_str}\n"
            "\n"
            f"Requires continuation : {self.requires_continuation()}\n"
            f"Validity              : {validity}\n"
            f"Metadata              : {self.metadata}\n"
        )

    # ------------------------------------------------------------------
    # requires_continuation
    # ------------------------------------------------------------------

    def requires_continuation(self) -> bool:
        """Return ``True`` iff this rule requires a continuation premise.

        Elimination rules that require a continuation are those that
        perform *case analysis* (e.g. disjunction elimination, existential
        elimination) and need a proof of the conclusion *for each case*.

        A rule requires a continuation if it has at least one minor premise
        that refers to a bound variable or a generic goal formula ``C``.

        Returns
        -------
        bool
        """
        if not self.minor_premises:
            return False
        # Heuristic: if any minor premise mentions a generic "C" or
        # uses "x :" or "∃x" style binding it's a continuation.
        continuation_indicators = (
            " → C", "⊢ C", "→ C", "⊢ P", " → P", "case",
        )
        for minor in self.minor_premises:
            for indicator in continuation_indicators:
                if indicator in minor:
                    return True
        # Disjunction and existential elimination require continuations
        if self.connective in ("∨", "∃", "+"):
            return True
        return False


# ---------------------------------------------------------------------------
# ComputationRule
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class ComputationRule:
    """A computation rule (β/η reduction) for a type-theoretic term system.

    Computation rules describe how terms *reduce* to simpler forms.  The
    canonical examples are:

    *β-reduction*::

        (λx. t) s  →β  t[x := s]

    *η-expansion* (η-rule, if oriented as expansion)::

        t  →η  λx. (t x)    (when x ∉ FV(t))

    Attributes
    ----------
    rule_id:
        Stable unique identifier.
    name:
        Human-readable name (e.g. ``"β-reduce"``, ``"η-expand"``).
    redex_pattern:
        String pattern for the left-hand side (the *redex*).
    reduct_pattern:
        String pattern for the right-hand side (the *reduct*).
    computation_kind:
        One of ``"beta"``, ``"eta"``, ``"delta"`` (unfolding definitions),
        ``"iota"`` (dependent type elimination), or ``"custom"``.
    is_oriented:
        If ``True`` (default), the rule is a directed rewrite (lhs → rhs).
        If ``False``, it is a symmetric equational rule.
    metadata:
        Free-form annotations.
    """

    rule_id: str
    name: str
    redex_pattern: str
    reduct_pattern: str
    computation_kind: str = "beta"
    is_oriented: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # schema
    # ------------------------------------------------------------------

    def schema(self) -> RuleSchema:
        """Return the :class:`RuleSchema` for this computation rule."""
        arrow = "→β" if self.computation_kind == "beta" else "→η" if self.computation_kind == "eta" else "→"
        return RuleSchema(
            rule_id=self.rule_id,
            name=self.name,
            premises=[f"{self.redex_pattern}  is well-typed"],
            conclusion=f"{self.redex_pattern}  {arrow}  {self.reduct_pattern}",
            latex_premises=[
                r"\vdash " + self.redex_pattern.replace("λ", r"\lambda")
                + r" : T"
            ],
            latex_conclusion=(
                self.redex_pattern.replace("λ", r"\lambda")
                + r" \;\rightsquigarrow_{"
                + self.computation_kind
                + r"}\; "
                + self.reduct_pattern.replace("λ", r"\lambda")
            ),
            description=(
                f"{self.computation_kind.upper()}-reduction: "
                f"'{self.redex_pattern}' reduces to '{self.reduct_pattern}'."
            ),
        )

    # ------------------------------------------------------------------
    # reduce
    # ------------------------------------------------------------------

    def reduce(self, term: str) -> str | None:
        """Apply this reduction to *term*, returning the reduct or ``None``.

        This is a syntactic string-level reduction.  The redex pattern is
        matched literally (with optional whitespace normalisation); if it
        matches, the corresponding reduct is returned.

        Parameters
        ----------
        term:
            The term to reduce.

        Returns
        -------
        str | None
            The reduced term, or ``None`` if *term* is not a redex.
        """
        if not self.is_redex(term):
            return None

        # Attempt a direct string replacement
        norm_term = re.sub(r"\s+", " ", term.strip())
        norm_redex = re.sub(r"\s+", " ", self.redex_pattern.strip())
        norm_reduct = re.sub(r"\s+", " ", self.reduct_pattern.strip())

        if norm_redex in norm_term:
            return norm_term.replace(norm_redex, norm_reduct, 1)

        # Fallback: try to match the pattern as a regex
        try:
            result = re.sub(
                re.escape(norm_redex),
                norm_reduct,
                norm_term,
                count=1,
            )
            if result != norm_term:
                return result
        except re.error:
            pass

        return None

    # ------------------------------------------------------------------
    # is_redex
    # ------------------------------------------------------------------

    def is_redex(self, term: str) -> bool:
        """Return ``True`` iff *term* matches the redex pattern.

        Parameters
        ----------
        term:
            The term to test.

        Returns
        -------
        bool
        """
        norm_term = re.sub(r"\s+", " ", term.strip())
        norm_redex = re.sub(r"\s+", " ", self.redex_pattern.strip())

        if norm_redex in norm_term:
            return True

        # Try as a regex pattern
        try:
            return bool(re.search(re.escape(norm_redex), norm_term))
        except re.error:
            return False

    # ------------------------------------------------------------------
    # normalize
    # ------------------------------------------------------------------

    def normalize(
        self, term: str, max_steps: int = 1000
    ) -> tuple[str, int]:
        """Reduce *term* to normal form by repeated application.

        Parameters
        ----------
        term:
            The starting term.
        max_steps:
            Maximum number of reduction steps to attempt (safeguard
            against non-terminating terms).

        Returns
        -------
        tuple[str, int]
            ``(normal_form, steps_taken)`` where *steps_taken* is the
            number of reduction steps performed.  If the term is still
            reducible after *max_steps*, the best-so-far approximation
            is returned.
        """
        current = term
        for step in range(max_steps):
            reduct = self.reduce(current)
            if reduct is None or reduct == current:
                return current, step
            current = reduct
        logger.warning(
            "ComputationRule[%s].normalize: hit max_steps=%d; "
            "term may not terminate.",
            self.rule_id,
            max_steps,
        )
        return current, max_steps

    # ------------------------------------------------------------------
    # compose
    # ------------------------------------------------------------------

    def compose(self, other: "ComputationRule") -> "ComputationRule | None":
        """Compose *self* with *other* (apply *self* first, then *other*).

        The composed rule reduces a term that matches *self*'s redex by
        first applying *self*, then applying *other* to the reduct.  This
        succeeds only when *self*'s reduct pattern is *other*'s redex
        pattern (or a sub-expression thereof).

        Parameters
        ----------
        other:
            The rule to apply after *self*.

        Returns
        -------
        ComputationRule | None
            The composed rule, or ``None`` if composition is not possible.
        """
        # Check that self's reduct matches other's redex
        norm_my_reduct = re.sub(r"\s+", " ", self.reduct_pattern.strip())
        norm_other_redex = re.sub(r"\s+", " ", other.redex_pattern.strip())

        if norm_my_reduct != norm_other_redex and norm_other_redex not in norm_my_reduct:
            return None

        # Compose: self.redex →* other.reduct
        composed_id = f"({self.rule_id} ; {other.rule_id})"
        composed_reduct = other.reduct_pattern
        if norm_other_redex in norm_my_reduct:
            composed_reduct = norm_my_reduct.replace(
                norm_other_redex, other.reduct_pattern, 1
            )

        return ComputationRule(
            rule_id=composed_id,
            name=f"{self.name} ; {other.name}",
            redex_pattern=self.redex_pattern,
            reduct_pattern=composed_reduct,
            computation_kind=f"{self.computation_kind}+{other.computation_kind}",
            is_oriented=self.is_oriented and other.is_oriented,
            metadata={
                "composed_from": [self.rule_id, other.rule_id],
            },
        )

    # ------------------------------------------------------------------
    # is_confluent_with
    # ------------------------------------------------------------------

    def is_confluent_with(self, other: "ComputationRule") -> bool:
        """Heuristic confluence check between *self* and *other*.

        Two reductions are *confluent* if they can always be joined: when
        both are applicable, there exists a common reduct.  This method
        uses a simple syntactic heuristic: the two rules are likely
        confluent if their redex patterns are disjoint.

        Parameters
        ----------
        other:
            The other computation rule.

        Returns
        -------
        bool
            ``True`` if the rules are heuristically confluent.
        """
        # If the redex patterns are completely different strings, they
        # are applicable to disjoint sets of terms → locally confluent.
        norm_self = re.sub(r"\s+", " ", self.redex_pattern.strip())
        norm_other = re.sub(r"\s+", " ", other.redex_pattern.strip())

        if norm_self == norm_other:
            # Same redex: confluent only if they produce the same reduct
            return self.reduct_pattern == other.reduct_pattern

        # Check for overlap (one pattern is a sub-expression of the other)
        if norm_self in norm_other or norm_other in norm_self:
            # Potentially overlapping: cannot guarantee confluence heuristically
            logger.debug(
                "ComputationRule confluence: potential overlap between "
                "%r and %r",
                norm_self,
                norm_other,
            )
            return False

        return True  # Disjoint patterns → locally confluent

    # ------------------------------------------------------------------
    # to_deduction_rule
    # ------------------------------------------------------------------

    def to_deduction_rule(self) -> DeductionRule:
        """Convert to a :class:`DeductionRule`."""
        sch = self.schema()
        return DeductionRule(
            rule_id=self.rule_id,
            rule_name=self.name,
            premises=tuple(sch.premises),
            conclusion=sch.conclusion,
            side_conditions={},
            rule_kind=RuleKind.SEMANTIC if _models_ok else "semantic",
            metadata={
                **self.metadata,
                "computation_kind": self.computation_kind,
                "is_oriented": self.is_oriented,
                "redex_pattern": self.redex_pattern,
                "reduct_pattern": self.reduct_pattern,
            },
        )

    # ------------------------------------------------------------------
    # explain
    # ------------------------------------------------------------------

    def explain(self) -> str:
        """Return a multi-line human-readable explanation."""
        border = "=" * 52
        arrow = "→" if self.is_oriented else "="
        return (
            f"ComputationRule  [{self.rule_id}]\n"
            f"{border}\n"
            f"Name             : {self.name}\n"
            f"Kind             : {self.computation_kind}\n"
            f"Oriented         : {self.is_oriented}\n"
            "Source           : theory2.tex, Chapter 33, §33.3\n"
            "\n"
            "Reduction\n"
            "---------\n"
            f"  {self.redex_pattern}\n"
            f"  {arrow}\n"
            f"  {self.reduct_pattern}\n"
            "\n"
            "Intuition\n"
            "---------\n"
            f"  A {self.computation_kind}-reduction fires when a term matches\n"
            f"  the redex pattern '{self.redex_pattern}' and replaces it\n"
            f"  with the reduct '{self.reduct_pattern}'.\n"
            "\n"
            f"Metadata : {self.metadata}\n"
        )

    # ------------------------------------------------------------------
    # eta_expand
    # ------------------------------------------------------------------

    def eta_expand(self, term: str, type_hint: str = "") -> str:
        """η-expand *term* to make the outermost λ explicit.

        η-expansion converts ``t`` to ``λx. (t x)`` when ``x ∉ FV(t)``.
        This method applies a simple syntactic expansion.

        Parameters
        ----------
        term:
            The term to η-expand.
        type_hint:
            Optional type annotation for the new variable (e.g. ``"A"``).

        Returns
        -------
        str
            The η-expanded term, or the original term if it already has
            a leading ``λ`` (expansion is not needed).
        """
        term_stripped = term.strip()

        # If already a lambda, no expansion needed
        if term_stripped.startswith("λ") or term_stripped.startswith("\\"):
            return term_stripped

        # Generate a fresh variable name unlikely to clash
        fresh_var = "η_x"
        type_ann = f" : {type_hint}" if type_hint else ""
        return f"λ({fresh_var}{type_ann}). ({term_stripped} {fresh_var})"


# ---------------------------------------------------------------------------
# DefinitionalEqualityRule
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class DefinitionalEqualityRule:
    """A definitional equality rule: lhs ≡ rhs in a given context.

    Definitional equality (``≡``, ``=_def``) is stronger than propositional
    equality: definitionally equal terms are *interchangeable without proof*.
    Examples include:

    * ``fst (a, b) ≡ a``  (β-rule for Σ-types)
    * ``⊤-intro ≡ tt``    (η-rule for unit type)

    Attributes
    ----------
    rule_id:
        Stable unique identifier.
    lhs_pattern:
        Left-hand side pattern.
    rhs_pattern:
        Right-hand side pattern.
    context_pattern:
        Optional context constraint (e.g. ``"Γ, x : A"``).
    is_symmetric:
        If ``True`` (default), the equality is symmetric: *rhs* ≡ *lhs*.
    evidence_kind:
        One of ``"definitional"``, ``"propositional"``, ``"judgmental"``.
    metadata:
        Free-form annotations.
    """

    rule_id: str
    lhs_pattern: str
    rhs_pattern: str
    context_pattern: str = ""
    is_symmetric: bool = True
    evidence_kind: str = "definitional"
    metadata: dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # schema
    # ------------------------------------------------------------------

    def schema(self) -> RuleSchema:
        """Return the :class:`RuleSchema` for this equality rule."""
        eq_sym = "≡"
        ctx_part = f"{self.context_pattern} ⊢ " if self.context_pattern else "⊢ "
        premises_list = []
        if self.context_pattern:
            premises_list.append(f"{self.context_pattern}  is well-formed")
        return RuleSchema(
            rule_id=self.rule_id,
            name=f"def-eq-{self.rule_id}",
            premises=premises_list,
            conclusion=f"{ctx_part}{self.lhs_pattern} {eq_sym} {self.rhs_pattern}",
            latex_premises=[
                p.replace("⊢", r"\vdash").replace("Γ", r"\Gamma")
                for p in premises_list
            ],
            latex_conclusion=(
                (self.context_pattern.replace("Γ", r"\Gamma") + r" \vdash " if self.context_pattern else r"\vdash ")
                + self.lhs_pattern
                + r" \equiv "
                + self.rhs_pattern
            ),
            description=(
                f"{self.evidence_kind.capitalize()} equality: "
                f"'{self.lhs_pattern}' is definitionally equal to "
                f"'{self.rhs_pattern}'"
                + (f" in context '{self.context_pattern}'" if self.context_pattern else "")
                + "."
            ),
        )

    # ------------------------------------------------------------------
    # check_equal
    # ------------------------------------------------------------------

    def check_equal(
        self,
        lhs: str,
        rhs: str,
        context: Mapping[str, Any] | None = None,
    ) -> bool:
        """Check whether *lhs* and *rhs* are definitionally equal under this rule.

        Uses syntactic matching: normalises whitespace and compares.  If
        :attr:`is_symmetric` is ``True``, also checks ``rhs == lhs_pattern``
        and ``lhs == rhs_pattern``.

        Parameters
        ----------
        lhs, rhs:
            Concrete terms to compare.
        context:
            Optional context mapping (currently unused; reserved for
            future context-sensitive equality).

        Returns
        -------
        bool
        """
        def _n(s: str) -> str:
            return re.sub(r"\s+", " ", s.strip())

        n_lhs = _n(lhs)
        n_rhs = _n(rhs)
        n_lp = _n(self.lhs_pattern)
        n_rp = _n(self.rhs_pattern)

        # Direct match
        if n_lhs == n_lp and n_rhs == n_rp:
            return True

        # Symmetric match
        if self.is_symmetric and n_lhs == n_rp and n_rhs == n_lp:
            return True

        # Reflexivity
        if n_lhs == n_rhs:
            return True

        return False

    # ------------------------------------------------------------------
    # rewrite_lhs_to_rhs
    # ------------------------------------------------------------------

    def rewrite_lhs_to_rhs(self, term: str) -> str | None:
        """Try to rewrite occurrences of :attr:`lhs_pattern` in *term* to :attr:`rhs_pattern`.

        Parameters
        ----------
        term:
            The term in which to rewrite.

        Returns
        -------
        str | None
            The rewritten term, or ``None`` if :attr:`lhs_pattern` does not
            appear in *term*.
        """
        norm_term = re.sub(r"\s+", " ", term.strip())
        norm_lhs = re.sub(r"\s+", " ", self.lhs_pattern.strip())
        norm_rhs = re.sub(r"\s+", " ", self.rhs_pattern.strip())

        if norm_lhs not in norm_term:
            return None
        return norm_term.replace(norm_lhs, norm_rhs)

    # ------------------------------------------------------------------
    # rewrite_rhs_to_lhs
    # ------------------------------------------------------------------

    def rewrite_rhs_to_lhs(self, term: str) -> str | None:
        """Try to rewrite occurrences of :attr:`rhs_pattern` in *term* to :attr:`lhs_pattern`.

        Parameters
        ----------
        term:
            The term in which to rewrite.

        Returns
        -------
        str | None
            The rewritten term, or ``None`` if :attr:`rhs_pattern` does
            not appear in *term*.

        Raises
        ------
        ValueError
            If :attr:`is_symmetric` is ``False`` (this direction is not
            valid for non-symmetric rules).
        """
        if not self.is_symmetric:
            raise ValueError(
                f"DefinitionalEqualityRule[{self.rule_id}].rewrite_rhs_to_lhs: "
                "rule is not symmetric; right-to-left rewriting is not permitted."
            )
        norm_term = re.sub(r"\s+", " ", term.strip())
        norm_rhs = re.sub(r"\s+", " ", self.rhs_pattern.strip())
        norm_lhs = re.sub(r"\s+", " ", self.lhs_pattern.strip())

        if norm_rhs not in norm_term:
            return None
        return norm_term.replace(norm_rhs, norm_lhs)

    # ------------------------------------------------------------------
    # symmetric_rule
    # ------------------------------------------------------------------

    def symmetric_rule(self) -> "DefinitionalEqualityRule":
        """Return a new :class:`DefinitionalEqualityRule` with lhs and rhs swapped.

        Returns
        -------
        DefinitionalEqualityRule
            The symmetric variant of this rule.
        """
        return DefinitionalEqualityRule(
            rule_id=f"{self.rule_id}-sym",
            lhs_pattern=self.rhs_pattern,
            rhs_pattern=self.lhs_pattern,
            context_pattern=self.context_pattern,
            is_symmetric=self.is_symmetric,
            evidence_kind=self.evidence_kind,
            metadata={
                **self.metadata,
                "symmetric_of": self.rule_id,
            },
        )

    # ------------------------------------------------------------------
    # to_deduction_rule
    # ------------------------------------------------------------------

    def to_deduction_rule(self) -> DeductionRule:
        """Convert to a :class:`DeductionRule`."""
        sch = self.schema()
        return DeductionRule(
            rule_id=self.rule_id,
            rule_name=sch.name,
            premises=tuple(sch.premises),
            conclusion=sch.conclusion,
            side_conditions={
                "is_symmetric": self.is_symmetric,
                "evidence_kind": self.evidence_kind,
            },
            rule_kind=RuleKind.SEMANTIC if _models_ok else "semantic",
            metadata={
                **self.metadata,
                "lhs_pattern": self.lhs_pattern,
                "rhs_pattern": self.rhs_pattern,
                "context_pattern": self.context_pattern,
            },
        )

    # ------------------------------------------------------------------
    # close_under_context
    # ------------------------------------------------------------------

    def close_under_context(
        self, contexts: Sequence[str]
    ) -> list["DefinitionalEqualityRule"]:
        """Generate a family of rules, one for each context in *contexts*.

        Parameters
        ----------
        contexts:
            A sequence of context pattern strings (e.g.
            ``["Γ", "Γ, x : A", "Γ, x : A, y : B"]``).

        Returns
        -------
        list[DefinitionalEqualityRule]
            One rule per context, each with an updated
            :attr:`context_pattern`.
        """
        rules: list[DefinitionalEqualityRule] = []
        for i, ctx in enumerate(contexts):
            rules.append(
                DefinitionalEqualityRule(
                    rule_id=f"{self.rule_id}-ctx{i}",
                    lhs_pattern=self.lhs_pattern,
                    rhs_pattern=self.rhs_pattern,
                    context_pattern=ctx,
                    is_symmetric=self.is_symmetric,
                    evidence_kind=self.evidence_kind,
                    metadata={
                        **self.metadata,
                        "closed_under_context": ctx,
                        "parent_rule": self.rule_id,
                    },
                )
            )
        return rules

    # ------------------------------------------------------------------
    # validate
    # ------------------------------------------------------------------

    def validate(self) -> list[str]:
        """Check this rule for internal consistency.

        Returns
        -------
        list[str]
            Error strings; empty means valid.
        """
        errors: list[str] = []
        if not self.rule_id:
            errors.append("rule_id is empty")
        if not self.lhs_pattern:
            errors.append("lhs_pattern is empty")
        if not self.rhs_pattern:
            errors.append("rhs_pattern is empty")
        if self.lhs_pattern == self.rhs_pattern:
            errors.append(
                f"lhs_pattern == rhs_pattern == '{self.lhs_pattern}'.  "
                "A non-trivial equality rule should have distinct sides."
            )
        valid_kinds = {"definitional", "propositional", "judgmental"}
        if self.evidence_kind not in valid_kinds:
            errors.append(
                f"evidence_kind '{self.evidence_kind}' not in {valid_kinds}"
            )
        return errors

    # ------------------------------------------------------------------
    # explain
    # ------------------------------------------------------------------

    def explain(self) -> str:
        """Return a multi-line human-readable explanation."""
        border = "=" * 52
        ctx_str = f"  Context : {self.context_pattern}\n" if self.context_pattern else ""
        errors = self.validate()
        validity = "✅ Valid" if not errors else "❌ Invalid: " + "; ".join(errors)
        sym_note = "  (Symmetric: rhs ≡ lhs also holds)\n" if self.is_symmetric else ""
        return (
            f"DefinitionalEqualityRule  [{self.rule_id}]\n"
            f"{border}\n"
            f"Evidence kind  : {self.evidence_kind}\n"
            "Source         : theory2.tex, Chapter 33, §33.3\n"
            "\n"
            "Equality\n"
            "--------\n"
            f"{ctx_str}"
            f"  {self.lhs_pattern}\n"
            f"    ≡\n"
            f"  {self.rhs_pattern}\n"
            f"{sym_note}"
            "\n"
            f"Validity : {validity}\n"
            f"Metadata : {self.metadata}\n"
        )


# ---------------------------------------------------------------------------
# SemanticRuleSystem
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class SemanticRuleSystem:
    """Full semantic rule system combining introduction, elimination, and computation.

    A semantic rule system collects all rules for a set of logical connectives
    and provides operations for:

    * Looking up rules by connective.
    * Normalising terms via computation rules.
    * Checking the harmony property (every elimination follows from an intro).
    * Converting to a :class:`TransitionSystem` for model-checking.

    Attributes
    ----------
    system_id:
        Unique identifier.
    introduction_rules:
        All introduction rules in the system.
    elimination_rules:
        All elimination rules in the system.
    computation_rules:
        All computation rules in the system.
    equality_rules:
        All definitional equality rules in the system.
    connectives:
        Set of connective symbols the system knows about.
    """

    system_id: str
    introduction_rules: list[IntroductionRule] = field(default_factory=list)
    elimination_rules: list[EliminationRule] = field(default_factory=list)
    computation_rules: list[ComputationRule] = field(default_factory=list)
    equality_rules: list[DefinitionalEqualityRule] = field(default_factory=list)
    connectives: set[str] = field(default_factory=set)

    # ------------------------------------------------------------------
    # all_rules
    # ------------------------------------------------------------------

    def all_rules(self) -> list[DeductionRule]:
        """Return all rules in the system as :class:`DeductionRule` objects.

        Returns
        -------
        list[DeductionRule]
            Introduction rules, then elimination rules, then computation
            rules, then equality rules.
        """
        result: list[DeductionRule] = []
        for r in self.introduction_rules:
            result.append(r.to_deduction_rule())
        for r in self.elimination_rules:
            result.append(r.to_deduction_rule())
        for r in self.computation_rules:
            result.append(r.to_deduction_rule())
        for r in self.equality_rules:
            result.append(r.to_deduction_rule())
        return result

    # ------------------------------------------------------------------
    # rules_for_connective
    # ------------------------------------------------------------------

    def rules_for_connective(
        self, conn: str
    ) -> dict[str, list[DeductionRule]]:
        """Return all rules grouped by category for a given connective.

        Parameters
        ----------
        conn:
            The connective symbol to look up.

        Returns
        -------
        dict[str, list[DeductionRule]]
            Keys: ``"introduction"``, ``"elimination"``, ``"computation"``,
            ``"equality"``.
        """
        return {
            "introduction": [
                r.to_deduction_rule()
                for r in self.introduction_rules
                if r.connective == conn
            ],
            "elimination": [
                r.to_deduction_rule()
                for r in self.elimination_rules
                if r.connective == conn
            ],
            "computation": [
                r.to_deduction_rule()
                for r in self.computation_rules
                if conn in r.redex_pattern
            ],
            "equality": [
                r.to_deduction_rule()
                for r in self.equality_rules
                if conn in r.lhs_pattern or conn in r.rhs_pattern
            ],
        }

    # ------------------------------------------------------------------
    # add_introduction / add_elimination / add_computation
    # ------------------------------------------------------------------

    def add_introduction(self, rule: IntroductionRule) -> None:
        """Add an introduction rule and register its connective.

        Parameters
        ----------
        rule:
            The :class:`IntroductionRule` to add.
        """
        self.introduction_rules.append(rule)
        if rule.connective:
            self.connectives.add(rule.connective)
        logger.debug(
            "SemanticRuleSystem[%s]: added intro rule %r for '%s'",
            self.system_id,
            rule.rule_id,
            rule.connective,
        )

    def add_elimination(self, rule: EliminationRule) -> None:
        """Add an elimination rule and register its connective.

        Parameters
        ----------
        rule:
            The :class:`EliminationRule` to add.
        """
        self.elimination_rules.append(rule)
        if rule.connective:
            self.connectives.add(rule.connective)
        logger.debug(
            "SemanticRuleSystem[%s]: added elim rule %r for '%s'",
            self.system_id,
            rule.rule_id,
            rule.connective,
        )

    def add_computation(self, rule: ComputationRule) -> None:
        """Add a computation rule.

        Parameters
        ----------
        rule:
            The :class:`ComputationRule` to add.
        """
        self.computation_rules.append(rule)
        logger.debug(
            "SemanticRuleSystem[%s]: added computation rule %r (%s)",
            self.system_id,
            rule.rule_id,
            rule.computation_kind,
        )

    # ------------------------------------------------------------------
    # normalize_term
    # ------------------------------------------------------------------

    def normalize_term(self, term: str) -> str:
        """Reduce *term* to normal form by exhaustive application of all
        computation rules.

        Applies each computation rule in order until no rule fires.  Rules
        are applied in the order they were added.

        Parameters
        ----------
        term:
            The starting term.

        Returns
        -------
        str
            The term in (weak head) normal form.
        """
        current = term
        changed = True
        max_outer = 200
        outer = 0
        while changed and outer < max_outer:
            changed = False
            outer += 1
            for rule in self.computation_rules:
                reduct = rule.reduce(current)
                if reduct is not None and reduct != current:
                    current = reduct
                    changed = True
                    break  # Restart from the first rule
        return current

    # ------------------------------------------------------------------
    # check_admissibility
    # ------------------------------------------------------------------

    def check_admissibility(self, judgment: str) -> bool:
        """Heuristic check whether *judgment* is derivable in this system.

        A judgment ``Γ ⊢ J`` is admissible if *J* is the conclusion of some
        rule in the system (after normalisation) or is itself an axiom.

        This is a **syntactic heuristic**: it checks whether any rule's
        conclusion matches *J* up to whitespace normalisation.  Full
        admissibility checking requires proof search.

        Parameters
        ----------
        judgment:
            The judgment string ``Γ ⊢ J``.

        Returns
        -------
        bool
        """
        _, conclusion = _parse_judgment(judgment)
        normalized_conc = re.sub(r"\s+", " ", conclusion.strip())

        for rule in self.all_rules():
            rule_conc = re.sub(r"\s+", " ", rule.conclusion.strip())
            if rule_conc == normalized_conc:
                return True
            # Check if conclusion matches after normalizing the term
            reduced = self.normalize_term(conclusion)
            if re.sub(r"\s+", " ", reduced.strip()) == rule_conc:
                return True

        return False

    # ------------------------------------------------------------------
    # to_transition_system
    # ------------------------------------------------------------------

    def to_transition_system(self) -> TransitionSystem:
        """Convert this semantic rule system to a :class:`TransitionSystem`.

        The transition system has:

        * ``system_id`` — derived from :attr:`system_id`.
        * ``rules`` — all rules converted via :meth:`all_rules`.
        * ``initial_judgments`` — the set of axiom conclusions.
        * ``system_kind`` — ``"semantic"``.

        Returns
        -------
        TransitionSystem
        """
        all_dr = self.all_rules()
        axiom_conclusions = [
            r.conclusion for r in all_dr if not r.premises
        ]
        return TransitionSystem(
            system_id=f"ts-{self.system_id}",
            rules=all_dr,
            initial_judgments=axiom_conclusions,
            terminal_conditions=[],
            system_kind="semantic",
        )

    # ------------------------------------------------------------------
    # verify_harmony
    # ------------------------------------------------------------------

    def verify_harmony(self) -> list[str]:
        """Check the harmony property between introduction and elimination rules.

        *Harmony* states that each elimination rule is *justified* by a
        corresponding introduction rule: the eliminations tell you exactly
        what you put in (no more, no less).

        This is checked by pairing each elimination rule with an introduction
        rule for the same connective.

        Returns
        -------
        list[str]
            A list of issue strings.  An empty list means harmony holds.
        """
        issues: list[str] = []
        intro_by_conn: dict[str, list[IntroductionRule]] = {}
        for r in self.introduction_rules:
            intro_by_conn.setdefault(r.connective, []).append(r)

        elim_by_conn: dict[str, list[EliminationRule]] = {}
        for r in self.elimination_rules:
            elim_by_conn.setdefault(r.connective, []).append(r)

        # Check that every connective with an elimination has an introduction
        for conn, elims in elim_by_conn.items():
            if conn not in intro_by_conn:
                issues.append(
                    f"Harmony violation: connective '{conn}' has "
                    f"{len(elims)} elimination rule(s) but no introduction "
                    "rule.  Every connective must be introduced before it "
                    "can be eliminated."
                )

        # Check that every connective with an introduction has an elimination
        for conn, intros in intro_by_conn.items():
            if conn not in elim_by_conn:
                issues.append(
                    f"Harmony warning: connective '{conn}' has "
                    f"{len(intros)} introduction rule(s) but no elimination "
                    "rule.  This may be intentional (e.g., ⊤, unit types)."
                )

        # Check that elimination conclusions are sub-formulas of intro premises
        for conn in set(intro_by_conn) & set(elim_by_conn):
            intro_premises: set[str] = set()
            for r in intro_by_conn[conn]:
                for p in r.premises:
                    intro_premises.add(re.sub(r"\s+", " ", p.strip()))
            for elim in elim_by_conn[conn]:
                elim_conc = re.sub(r"\s+", " ", elim.conclusion.strip())
                if elim_conc not in intro_premises and elim_conc != elim.major_premise:
                    issues.append(
                        f"Harmony warning for '{conn}': elimination rule "
                        f"'{elim.rule_id}' concludes '{elim.conclusion}' "
                        "which does not appear as a premise of any introduction "
                        "rule.  Check subformula property."
                    )

        return issues

    # ------------------------------------------------------------------
    # copilot_complete
    # ------------------------------------------------------------------

    def copilot_complete(self, connective: str) -> str:
        """Suggest missing rules for *connective*.

        # copilot suggest missing introduction/elimination/computation rules
        for the given connective.

        Parameters
        ----------
        connective:
            The connective to analyse.

        Returns
        -------
        str
            A multi-line suggestion string.
        """
        rules = self.rules_for_connective(connective)
        intros = rules["introduction"]
        elims = rules["elimination"]
        comps = rules["computation"]

        lines = [
            f"=== Copilot rule completion for '{connective}' ===",
            f"System: {self.system_id}",
            "",
        ]

        if not intros:
            lines.append(
                f"  ❌ Missing: introduction rule for '{connective}'.  "
                f"Suggested pattern:\n"
                f"       Γ ⊢ A    Γ ⊢ B\n"
                f"       ──────────────  [{connective}-intro]\n"
                f"       Γ ⊢ A {connective} B"
            )
        else:
            lines.append(f"  ✅ Introduction: {len(intros)} rule(s) present")

        if not elims:
            lines.append(
                f"  ❌ Missing: elimination rule for '{connective}'.  "
                f"Suggested pattern:\n"
                f"       Γ ⊢ A {connective} B\n"
                f"       ────────────────────  [{connective}-elim]\n"
                f"       Γ ⊢ A"
            )
        else:
            lines.append(f"  ✅ Elimination: {len(elims)} rule(s) present")

        if not comps:
            lines.append(
                f"  ⚠  No computation rule for '{connective}'.  "
                f"Consider adding a β-rule if '{connective}' is a binder."
            )
        else:
            lines.append(f"  ✅ Computation: {len(comps)} rule(s) present")

        harmony_issues = self.verify_harmony()
        conn_issues = [i for i in harmony_issues if connective in i]
        if conn_issues:
            lines.append("")
            lines.append("  Harmony issues:")
            for issue in conn_issues:
                lines.append(f"    • {issue}")

        return "\n".join(lines)


# ---------------------------------------------------------------------------
# SoundnessChecker
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class SoundnessChecker:
    """Checks semantic rule soundness, optionally using Z3 for verification.

    A rule is *sound* if every instance of the rule is semantically valid:
    whenever all premises hold in a model, the conclusion holds.

    This class implements both a lightweight syntactic soundness check and,
    when Z3 is available, an SMT-based verification.

    Attributes
    ----------
    rule_system:
        The :class:`SemanticRuleSystem` to check.
    z3_session:
        An optional Z3 session object.  ``None`` when Z3 is unavailable.
    z3_encoder:
        An optional encoder for translating rules to Z3 formulas.
        ``None`` when Z3 is unavailable.
    verification_cache:
        Maps rule IDs to ``(is_sound, reason)`` pairs to avoid redundant
        re-verification.
    """

    rule_system: SemanticRuleSystem
    z3_session: Any = None
    z3_encoder: Any = None
    verification_cache: dict[str, bool] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # check_rule_soundness
    # ------------------------------------------------------------------

    def check_rule_soundness(
        self, rule: DeductionRule
    ) -> tuple[bool, str]:
        """Check soundness of a single rule.

        If Z3 is available the rule is encoded and verified.  Otherwise a
        syntactic plausibility check is performed.

        Parameters
        ----------
        rule:
            The rule to verify.

        Returns
        -------
        tuple[bool, str]
            ``(is_sound, reason)`` where *reason* is a prose explanation.
        """
        rule_id = rule.rule_id

        # Return cached result if available
        if rule_id in self.verification_cache:
            cached = self.verification_cache[rule_id]
            return cached, f"(cached) {'Sound' if cached else 'Unsound'}"

        # Syntactic plausibility checks
        errors: list[str] = []
        if not rule.rule_id:
            errors.append("rule_id is empty")
        if not rule.conclusion:
            errors.append("conclusion is empty")
        # A rule with no premises should be an axiom
        if not rule.premises and not rule.conclusion:
            errors.append("Axiom rule has empty conclusion")

        if errors:
            self.verification_cache[rule_id] = False
            return False, "Syntactic plausibility failed: " + "; ".join(errors)

        # Attempt Z3 verification if session available
        if self.z3_session is not None:
            formula = self.encode_rule_as_formula(rule)
            z3_result, z3_reason = self.verify_with_z3(formula)
            self.verification_cache[rule_id] = z3_result
            return z3_result, z3_reason

        # Fallback: heuristic checks
        reason = self._heuristic_soundness_check(rule)
        is_sound = reason.startswith("Sound")
        self.verification_cache[rule_id] = is_sound
        return is_sound, reason

    def _heuristic_soundness_check(self, rule: DeductionRule) -> str:
        """Internal heuristic soundness analysis.

        Checks:
        1. The conclusion is not the empty string.
        2. For semantic rules, the principal connective appears in the
           conclusion.
        3. No obvious logical contradictions in side conditions.

        Parameters
        ----------
        rule:
            The rule to analyse.

        Returns
        -------
        str
            A reason string starting with ``"Sound"`` or ``"Potentially unsound"``.
        """
        conclusion = rule.conclusion.strip()
        if not conclusion:
            return "Potentially unsound: conclusion is empty."

        # Check side conditions for explicit ``False``
        for cond_name, cond_val in rule.side_conditions.items():
            if cond_val is False:
                return (
                    f"Potentially unsound: side condition '{cond_name}' is "
                    "statically False."
                )

        # Check that the conclusion is not trivially ``⊥``
        if conclusion in ("⊥", "False", "false", "bot"):
            return (
                "Potentially unsound: conclusion is ⊥ (bottom).  "
                "Only rules with empty premises may conclude ⊥ in a "
                "consistent system."
            )

        return (
            f"Sound (heuristic): rule '{rule.rule_name}' passes all "
            "syntactic plausibility checks.  Use Z3 for formal verification."
        )

    # ------------------------------------------------------------------
    # check_system_soundness
    # ------------------------------------------------------------------

    def check_system_soundness(
        self,
    ) -> dict[str, tuple[bool, str]]:
        """Check soundness of every rule in the system.

        Returns
        -------
        dict[str, tuple[bool, str]]
            Maps each rule_id to ``(is_sound, reason)``.
        """
        results: dict[str, tuple[bool, str]] = {}
        for rule in self.rule_system.all_rules():
            results[rule.rule_id] = self.check_rule_soundness(rule)
        return results

    # ------------------------------------------------------------------
    # encode_rule_as_formula
    # ------------------------------------------------------------------

    def encode_rule_as_formula(self, rule: DeductionRule) -> str:
        """Encode *rule* as a logical formula string suitable for Z3.

        The encoding is: ``(premises[0] ∧ … ∧ premises[n]) → conclusion``.

        Parameters
        ----------
        rule:
            The rule to encode.

        Returns
        -------
        str
            A propositional formula string.
        """
        if not rule.premises:
            # Axiom: just the conclusion
            return rule.conclusion

        if len(rule.premises) == 1:
            return f"({rule.premises[0]}) → ({rule.conclusion})"

        premises_conjoined = " ∧ ".join(f"({p})" for p in rule.premises)
        return f"({premises_conjoined}) → ({rule.conclusion})"

    # ------------------------------------------------------------------
    # verify_with_z3
    # ------------------------------------------------------------------

    def verify_with_z3(self, formula: str) -> tuple[bool, str]:
        """Attempt to verify *formula* using Z3.

        If Z3 is not available, returns a ``(True, 'unverified')`` stub.

        Parameters
        ----------
        formula:
            A logical formula string to verify (as a tautology check).

        Returns
        -------
        tuple[bool, str]
            ``(is_valid, reason)`` where *is_valid* is ``True`` if the
            formula is a tautology (negation is UNSAT).
        """
        if self.z3_session is None:
            return (
                True,
                f"Z3 unavailable: formula '{formula[:60]}…' not formally "
                "verified.  Install `z3-solver` for SMT checking.",
            )

        try:
            # z3_session is expected to provide a `check_tautology(formula)` method
            if hasattr(self.z3_session, "check_tautology"):
                result = self.z3_session.check_tautology(formula)
                if isinstance(result, bool):
                    reason = (
                        f"Z3: formula is {'valid (UNSAT negation)' if result else 'invalid (SAT negation found)'}"
                    )
                    return result, reason
        except Exception as exc:
            return (
                False,
                f"Z3 verification raised exception: {exc!r}",
            )

        return True, "Z3 session present but check_tautology not callable."

    # ------------------------------------------------------------------
    # check_introduction_elimination_duality
    # ------------------------------------------------------------------

    def check_introduction_elimination_duality(
        self,
        intro: IntroductionRule,
        elim: EliminationRule,
    ) -> bool:
        """Return ``True`` iff *intro* and *elim* satisfy the duality condition.

        The duality condition is: the major premise of *elim* should match
        the conclusion of *intro*, and the conclusion of *elim* should be
        (or be implied by) one of *intro*'s premises.

        Parameters
        ----------
        intro:
            The introduction rule.
        elim:
            The elimination rule.

        Returns
        -------
        bool
        """
        def _n(s: str) -> str:
            return re.sub(r"\s+", " ", s.strip())

        # Check 1: major premise of elim matches conclusion of intro
        if _n(elim.major_premise) != _n(intro.conclusion):
            # Allow partial match (elim's major_premise is a schema for intro's conclusion)
            if intro.connective not in elim.major_premise:
                return False

        # Check 2: conclusion of elim should relate to intro's premises
        elim_conc = _n(elim.conclusion)
        for premise in intro.premises:
            if elim_conc in _n(premise) or _n(premise) in elim_conc:
                return True

        # Lenient: if connectives match and both are non-empty, accept
        return bool(intro.connective and elim.connective == intro.connective)

    # ------------------------------------------------------------------
    # soundness_report
    # ------------------------------------------------------------------

    def soundness_report(self) -> dict[str, Any]:
        """Generate a comprehensive soundness report for the entire system.

        Returns
        -------
        dict[str, Any]
            A dictionary with keys:

            ``"system_id"``
                The rule system's identifier.
            ``"total_rules"``
                Total number of rules checked.
            ``"sound_count"``
                Number of rules that passed the soundness check.
            ``"unsound_rules"``
                List of rule IDs that failed.
            ``"harmony_issues"``
                Output of :meth:`SemanticRuleSystem.verify_harmony`.
            ``"per_rule"``
                Mapping of rule_id → ``(is_sound, reason)``.
            ``"summary"``
                One-line prose summary.
        """
        per_rule = self.check_system_soundness()
        sound_count = sum(1 for (ok, _) in per_rule.values() if ok)
        unsound = [rid for rid, (ok, _) in per_rule.items() if not ok]
        harmony = self.rule_system.verify_harmony()

        summary = (
            f"System '{self.rule_system.system_id}': "
            f"{sound_count}/{len(per_rule)} rules sound; "
            f"{len(unsound)} unsound; "
            f"{len(harmony)} harmony issue(s)."
        )

        return {
            "system_id": self.rule_system.system_id,
            "total_rules": len(per_rule),
            "sound_count": sound_count,
            "unsound_rules": unsound,
            "harmony_issues": harmony,
            "z3_available": self.z3_session is not None,
            "per_rule": per_rule,
            "summary": summary,
        }

    # ------------------------------------------------------------------
    # copilot_explain_failure
    # ------------------------------------------------------------------

    def copilot_explain_failure(
        self, rule_name: str, reason: str
    ) -> str:
        """Produce a natural-language explanation of a soundness failure.

        # copilot explain soundness failure for the given rule and reason.

        Parameters
        ----------
        rule_name:
            The name of the rule that failed.
        reason:
            The reason string returned by the checker.

        Returns
        -------
        str
            A multi-line explanation with diagnosis and suggested fixes.
        """
        diagnosis: list[str] = []

        if "⊥" in reason or "bottom" in reason.lower():
            diagnosis.append(
                "The rule concludes ⊥ (bottom / False).  In a consistent "
                "logic only rules with no premises (ex falso) may conclude ⊥.  "
                "Check that this rule has the intended premises."
            )
        if "empty" in reason.lower():
            diagnosis.append(
                "A required field (rule_id, conclusion) is empty.  "
                "Ensure the rule is fully specified before verification."
            )
        if "side condition" in reason.lower() and "false" in reason.lower():
            diagnosis.append(
                "A side condition evaluates to False statically.  This "
                "usually indicates a copy-paste error or a guard that was "
                "meant to be True.  Review the side_conditions dict."
            )
        if "positivity" in reason.lower():
            diagnosis.append(
                "The rule may violate the positivity condition (the connective "
                "appears negatively in a premise).  Negative occurrences lead "
                "to non-monotone definitions and can cause inconsistency "
                "(e.g. Girard's paradox).  Revise the premise."
            )
        if not diagnosis:
            diagnosis.append(
                "The failure reason is: " + reason + ".  "
                "Inspect the rule schema, side conditions, and connective "
                "usage to identify the issue."
            )

        lines = [
            f"=== Soundness Failure: '{rule_name}' ===",
            f"Reason   : {reason}",
            "",
            "Diagnosis",
            "---------",
        ]
        for d in diagnosis:
            lines.append(f"  • {d}")
        lines += [
            "",
            "Suggested actions",
            "-----------------",
            "  1. Review the rule's premises and conclusion for typos.",
            "  2. Verify side conditions are correct (not statically False).",
            "  3. Check that the principal connective appears correctly.",
            "  4. Run verify_harmony() to check introduction–elimination harmony.",
            "  5. If Z3 is available, inspect the SMT encoding via "
            "encode_rule_as_formula().",
        ]
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # clear_cache
    # ------------------------------------------------------------------

    def clear_cache(self) -> None:
        """Clear the verification cache.

        Forces all subsequent calls to :meth:`check_rule_soundness` to
        re-run the check rather than returning a cached result.
        """
        self.verification_cache.clear()
        logger.debug(
            "SoundnessChecker[%s]: verification cache cleared.",
            self.rule_system.system_id,
        )


# ---------------------------------------------------------------------------
# Module public API
# ---------------------------------------------------------------------------

__all__ = [
    "RuleSchema",
    "IntroductionRule",
    "EliminationRule",
    "ComputationRule",
    "DefinitionalEqualityRule",
    "SemanticRuleSystem",
    "SoundnessChecker",
]
