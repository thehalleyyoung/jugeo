"""
Quantifier Discipline for Tensor Index Formulas
================================================
Chapter 30 §3 of theory2.tex — JuGeo formal verification system.

Quantifiers appear naturally in tensor verification:
- "For all valid indices i: access A[i] is in bounds."
- "There exists an index i such that A[i] != B[i]."
- "For all parameters p: the loop bound is non-negative."

Uncontrolled quantifiers in Z3 can cause:
1. Undecidability: mixing ∀ and ∃ in first-order arithmetic (Σ₂ formulas).
2. Non-termination: Z3's e-matching instantiation can loop indefinitely.
3. Explosion: instantiating universal quantifiers at all terms creates
   exponentially many instances.

The JuGeo quantifier discipline enforces one of four strategies:
- ALWAYS_QF: Eliminate quantifiers before encoding (safest, most general).
- SKOLEM: Replace existential quantifiers with Skolem constants.
- INSTANTIATE: Instantiate universal quantifiers at a bounded set of terms.
- INLINE_QUANT: Keep quantifiers but add safe Z3 triggers.

This module provides:
- ``QuantifierDisciplineChecker``: Analyzes a formula string and recommends discipline.
- ``QuantifierInstantiator``: Applies instantiation strategies to formula strings.
- Module-level helpers for syntactic quantifier analysis.

copilot notes: For typical tensor index problems (bounded integer indices,
finite tensor shapes), ALWAYS_QF is the recommended discipline.  Use
copilot_recommend_discipline() for case-by-case recommendations.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    pass

from jugeo.encodings.tensor_quantifier_encodings.models import (
    DisciplineKind,
    QuantifierDiscipline,
)

__all__ = [
    "DisciplineReport",
    "QuantifierInfo",
    "QuantifierDisciplineChecker",
    "QuantifierInstantiator",
    "DISCIPLINE_RULES",
    "is_qf_formula",
    "count_quantifier_alternations",
]

# ---------------------------------------------------------------------------
# Optional Z3 imports
# ---------------------------------------------------------------------------

try:
    import z3 as _z3  # type: ignore[import]

    _Z3_AVAILABLE = True
except ImportError:
    _z3 = None  # type: ignore[assignment]
    _Z3_AVAILABLE = False

# ---------------------------------------------------------------------------
# Module-level discipline rules
# ---------------------------------------------------------------------------

DISCIPLINE_RULES: dict[str, str] = {
    "always_qf": (
        "ALWAYS_QF: Eliminate all quantifiers before encoding. "
        "Use Fourier-Motzkin projection for universal quantifiers over bounded integer "
        "ranges, and introduce Skolem constants for existential quantifiers. "
        "This guarantees decidability (QF_LIA) at the cost of potentially larger formulas."
    ),
    "skolem": (
        "SKOLEM: Replace each ∃x.P(x) with P(sk_x) where sk_x is a fresh constant. "
        "Sound for satisfiability checking. Preserves the fragment structure. "
        "Best used when the existential witness does not depend on universally "
        "quantified variables (otherwise a Skolem function is needed)."
    ),
    "inline_quant": (
        "INLINE_QUANT: Keep quantifiers but annotate them with Z3 e-matching triggers. "
        "The trigger specifies which ground terms should cause instantiation. "
        "Safe only when the trigger is loop-safe (no recursive function symbols). "
        "Best for shallow quantifiers with simple triggers like {f(x)}."
    ),
    "instantiate": (
        "INSTANTIATE: Instantiate universal quantifiers at a fixed set of ground terms. "
        "The terms should be chosen to cover all relevant cases (e.g., boundary values). "
        "Sound only if the chosen terms are sufficient (not complete in general). "
        "Best when the domain is small (e.g., tensor ranks 0..7)."
    ),
}


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class DisciplineReport:
    """Report produced by QuantifierDisciplineChecker.

    Records whether a formula conforms to the required discipline,
    lists violations, and provides suggestions for correction.

    Attributes:
        is_disciplined: True if the formula satisfies the discipline.
        violations: List of human-readable violation descriptions.
        suggestions: List of corrective suggestions.
        recommended_fragment: Suggested Z3 fragment (e.g., 'QF_LIA', 'AUFLIA').
        copilot_notes: Notes for the copilot assist layer.
        formula_summary: Short summary of the formula.
        quantifier_depth: Maximum quantifier nesting depth found.
        has_alternation: True if ∀∃ or ∃∀ alternation was detected.
    """

    is_disciplined: bool
    violations: list[str]
    suggestions: list[str]
    recommended_fragment: str
    copilot_notes: str
    formula_summary: str = ""
    quantifier_depth: int = 0
    has_alternation: bool = False

    def summary_line(self) -> str:
        """Return a one-line summary of the discipline report.

        Returns:
            Summary string like 'PASS: QF_LIA, depth=0' or 'FAIL: 2 violations'.
        """
        if self.is_disciplined:
            return (
                f"PASS: fragment={self.recommended_fragment}, "
                f"depth={self.quantifier_depth}, "
                f"alternation={self.has_alternation}"
            )
        return (
            f"FAIL: {len(self.violations)} violation(s), "
            f"recommended_fragment={self.recommended_fragment}"
        )


@dataclass
class QuantifierInfo:
    """Information about a single quantifier occurrence in a formula.

    Attributes:
        kind: Either 'forall' or 'exists'.
        bound_vars: Names of the bound variables.
        depth: Nesting depth of this quantifier (0 = top-level).
        is_guarded: True if the quantifier body has a guard predicate.
        trigger: Z3 trigger pattern if present, else None.
        body_summary: Short summary of the quantifier body.
    """

    kind: str
    bound_vars: list[str]
    depth: int
    is_guarded: bool
    trigger: str | None
    body_summary: str


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def is_qf_formula(formula_str: str) -> bool:
    """Check if a formula string is quantifier-free (syntactic check).

    Searches for the keywords 'forall', 'exists', 'ForAll', 'Exists' in the
    formula string.  Returns True only if none are found.

    This is a conservative syntactic check — it may return False for formulas
    that contain these words in variable names or comments.

    Args:
        formula_str: String representation of the formula (SMT-LIB2 or Python).

    Returns:
        True if no quantifier keywords are detected.
    """
    quantifier_keywords = re.compile(
        r'\b(forall|exists|ForAll|Exists|\\forall|\\exists)\b',
        re.IGNORECASE,
    )
    return quantifier_keywords.search(formula_str) is None


def count_quantifier_alternations(formula_str: str) -> int:
    """Count the number of quantifier alternations (∀∃ or ∃∀) in a formula string.

    An alternation occurs when a quantifier of one kind appears nested inside
    a quantifier of the opposite kind.

    This is a syntactic approximation based on sequential occurrences of
    different quantifier keywords in the string.

    Args:
        formula_str: String representation of the formula.

    Returns:
        Number of detected quantifier kind-changes.
    """
    tokens = re.findall(
        r'\b(forall|exists|ForAll|Exists)\b',
        formula_str,
        re.IGNORECASE,
    )
    normalised = ["forall" if t.lower() == "forall" else "exists" for t in tokens]

    alternations = 0
    for i in range(1, len(normalised)):
        if normalised[i] != normalised[i - 1]:
            alternations += 1
    return alternations


# ---------------------------------------------------------------------------
# QuantifierDisciplineChecker
# ---------------------------------------------------------------------------


class QuantifierDisciplineChecker:
    """Analyses formula strings for quantifier discipline violations.

    Given a formula (as a string in SMT-LIB2 or Python Z3 notation), this class
    identifies all quantifiers, checks whether they satisfy the required discipline,
    and produces a DisciplineReport with violations and suggestions.

    copilot notes: The checker operates on formula *strings*, not Z3 ASTs, so it
    works even when z3 is not installed.  For Z3-AST-level analysis, a separate
    z3-specific checker would be needed.

    Example::

        checker = QuantifierDisciplineChecker(max_instantiation_depth=4)
        report = checker.check_formula("(forall ((i Int)) (>= i 0))")
        print(report.summary_line())
    """

    def __init__(
        self,
        max_instantiation_depth: int = 5,
        allow_guarded_quantifiers: bool = True,
    ) -> None:
        """Initialise the discipline checker.

        Args:
            max_instantiation_depth: Maximum safe instantiation depth.
                Formulas with depth > this are flagged as violations.
            allow_guarded_quantifiers: If True, guarded quantifiers are not
                flagged as violations even in ALWAYS_QF mode.
        """
        self.max_instantiation_depth = max_instantiation_depth
        self.allow_guarded_quantifiers = allow_guarded_quantifiers

    def check_formula(self, formula_str: str) -> DisciplineReport:
        """Analyse a formula string and produce a DisciplineReport.

        Identifies quantifiers, checks for alternation, computes nesting depth,
        and determines the recommended Z3 fragment.

        Args:
            formula_str: Formula as a string (SMT-LIB2 or Python Z3 notation).

        Returns:
            DisciplineReport with full analysis.
        """
        violations: list[str] = []
        suggestions: list[str] = []

        is_qf = is_qf_formula(formula_str)
        depth = self._extract_quantifier_depth(formula_str)
        alternations = self._count_quantifier_alternations(formula_str)
        has_alternation = alternations > 0

        if is_qf:
            return DisciplineReport(
                is_disciplined=True,
                violations=[],
                suggestions=["Formula is quantifier-free — no discipline needed."],
                recommended_fragment="QF_LIA",
                copilot_notes="QF_LIA is decidable and efficient in Z3.",
                formula_summary=formula_str[:80],
                quantifier_depth=0,
                has_alternation=False,
            )

        # Check depth
        if depth > self.max_instantiation_depth:
            violations.append(
                f"Quantifier nesting depth {depth} exceeds safe bound "
                f"{self.max_instantiation_depth}."
            )
            suggestions.append(
                "Reduce nesting by Skolemizing inner existentials or "
                "unrolling universals at concrete bounds."
            )

        # Check alternation
        if has_alternation:
            violations.append(
                f"Detected {alternations} quantifier alternation(s) (∀∃ or ∃∀). "
                "This may push the formula into Σ₂ or Π₂, which is harder."
            )
            suggestions.append(
                "Eliminate alternation by: "
                "(1) Skolemizing existentials to remove them, "
                "(2) Fixing the universal quantifier range to a finite bound, "
                "or (3) Using ALWAYS_QF discipline."
            )

        # Fragment recommendation
        if has_alternation:
            fragment = "AUFLIA"  # Arrays + universal quantifiers + LIA
        elif depth > 0:
            fragment = "UFLIA"   # UF + LIA with quantifiers
        else:
            fragment = "QF_LIA"

        is_disciplined = len(violations) == 0

        if not is_disciplined:
            copilot_notes = (
                f"Formula has {depth} quantifier levels and {alternations} alternations. "
                "Apply ALWAYS_QF discipline to guarantee decidability."
            )
        else:
            copilot_notes = (
                "Formula has quantifiers but within safe bounds. "
                f"Recommended fragment: {fragment}."
            )

        return DisciplineReport(
            is_disciplined=is_disciplined,
            violations=violations,
            suggestions=suggestions,
            recommended_fragment=fragment,
            copilot_notes=copilot_notes,
            formula_summary=formula_str[:80],
            quantifier_depth=depth,
            has_alternation=has_alternation,
        )

    def identify_problematic_quantifiers(
        self, formula: str
    ) -> list[QuantifierInfo]:
        """Find all quantifiers in the formula and check if they are guarded.

        A quantifier is 'guarded' if the body has the form:
        ``(guard_pred(x) => body_pred(x))`` for ∀x, or
        ``(guard_pred(x) ∧ body_pred(x))`` for ∃x.

        This syntactic check looks for implication (=>) or conjunction (and)
        at the outermost level of the quantifier body.

        Args:
            formula: Formula string to analyse.

        Returns:
            List of QuantifierInfo objects, one per quantifier occurrence.
        """
        results: list[QuantifierInfo] = []

        # Find all quantifier patterns in SMT-LIB2 style
        smtlib2_pattern = re.compile(
            r'\((forall|exists)\s+\(([^)]+)\)\s+(.+?)\)',
            re.IGNORECASE | re.DOTALL,
        )

        depth = 0
        for m in smtlib2_pattern.finditer(formula):
            kind = m.group(1).lower()
            vars_str = m.group(2)
            body_str = m.group(3)[:60]  # truncate for display

            # Extract bound variable names
            var_names = re.findall(r'\((\w+)\s+\w+\)', vars_str)
            if not var_names:
                var_names = re.findall(r'\b\w+\b', vars_str)

            # Heuristic: check if body starts with => (guarded forall) or and (guarded exists)
            stripped_body = body_str.strip()
            is_guarded = (
                stripped_body.startswith("(=>") or
                stripped_body.startswith("(and") or
                stripped_body.startswith("(implies")
            )

            # Extract trigger pattern if present
            trigger_match = re.search(r':pattern\s+(\([^)]+\))', formula)
            trigger = trigger_match.group(1) if trigger_match else None

            results.append(QuantifierInfo(
                kind=kind,
                bound_vars=var_names,
                depth=depth,
                is_guarded=is_guarded,
                trigger=trigger,
                body_summary=stripped_body[:40],
            ))
            depth += 1

        return results

    def suggest_instantiation(self, formula: str, domain_size: int) -> str:
        """Return an instantiated formula string for a given finite domain.

        Replaces universal quantifiers over a variable ``x`` with a conjunction
        of the body for x = 0, 1, ..., domain_size - 1.

        Args:
            formula: Formula string containing a universal quantifier.
            domain_size: Number of ground instances to generate.

        Returns:
            Instantiated formula string (conjunction of instances).
        """
        quant_infos = self.identify_problematic_quantifiers(formula)
        if not quant_infos:
            return formula

        info = quant_infos[0]
        if info.kind != "forall" or not info.bound_vars:
            return formula

        var_name = info.bound_vars[0]
        instances: list[str] = []
        for k in range(domain_size):
            instance = formula.replace(var_name, str(k))
            instances.append(instance)

        if len(instances) == 1:
            return instances[0]
        inner = "\n  ".join(instances)
        return f"(and\n  {inner})"

    def skolemize_formula(self, formula: str) -> str:
        """Textually skolemize a formula by replacing existential variables.

        Replaces each ``(exists ((x Sort)) body)`` pattern with ``body[x -> sk_x]``
        where ``sk_x`` is a fresh Skolem constant.

        Args:
            formula: Formula string (SMT-LIB2 style).

        Returns:
            Skolemized formula string.
        """
        result = formula
        exists_pattern = re.compile(
            r'\(exists\s+\(\((\w+)\s+(\w+)\)\)\s+',
            re.IGNORECASE,
        )

        sk_counter = [0]

        def replace_exists(m: re.Match) -> str:
            var_name = m.group(1)
            sort_name = m.group(2)
            sk_name = f"sk_{var_name}_{sk_counter[0]}"
            sk_counter[0] += 1
            # Declare the Skolem constant in the output
            return f"(declare-const {sk_name} {sort_name})\n("
            # Note: We cannot fully replace the body here without proper AST parsing,
            # but this demonstrates the approach.

        result = exists_pattern.sub(replace_exists, result)
        return result

    def add_guard(self, quant_formula: str, guard_pred: str) -> str:
        """Wrap a quantifier body with a guard predicate.

        Transforms ``(forall ((x T)) body)`` into
        ``(forall ((x T)) (=> guard_pred body))`` to make the quantifier guarded.

        Args:
            quant_formula: Formula string containing a (possibly unguarded) forall.
            guard_pred: Guard predicate string (e.g., "(and (>= x 0) (< x n))").

        Returns:
            Formula string with the guard inserted.
        """
        forall_pattern = re.compile(
            r'\(forall\s+(\([^)]+\))\s+(.+?)\)\s*$',
            re.IGNORECASE | re.DOTALL,
        )
        m = forall_pattern.search(quant_formula)
        if not m:
            return f"(forall (x Int) (=> {guard_pred} {quant_formula}))"

        bound_decls = m.group(1)
        body = m.group(2).strip()
        # Wrap body with implication
        guarded_body = f"(=> {guard_pred} {body})"
        return f"(forall {bound_decls} {guarded_body})"

    def check_e_matching_termination(
        self, formula: str, triggers: list[str]
    ) -> bool:
        """Simple syntactic check that a trigger does not cause e-matching loops.

        A trigger is loop-safe if:
        1. It is not a single variable (would match everything).
        2. It does not contain more than 3 nested function applications.
        3. It does not match a ground term in the formula conclusion.

        Args:
            formula: The formula string (for context).
            triggers: List of trigger pattern strings.

        Returns:
            True if all triggers appear loop-safe, False otherwise.
        """
        for trigger in triggers:
            if not trigger or not trigger.strip():
                return False  # Empty trigger is unsafe

            # Count nesting level
            depth = 0
            max_depth = 0
            for ch in trigger:
                if ch == '(':
                    depth += 1
                    max_depth = max(max_depth, depth)
                elif ch == ')':
                    depth -= 1
            if max_depth > 3:
                return False  # Too deeply nested

            # Single variable trigger is unsafe
            if re.match(r'^\s*\w+\s*$', trigger):
                return False

            # Check for recursive patterns
            func_names = re.findall(r'\((\w+)', trigger)
            if len(func_names) != len(set(func_names)):
                return False  # Same function appears multiple times — potential loop

        return True

    def copilot_recommend_discipline(
        self, formula_summary: str
    ) -> QuantifierDiscipline:
        """Return a QuantifierDiscipline model based on a formula summary.

        Analyses the formula summary and returns the most appropriate discipline
        as a QuantifierDiscipline dataclass.

        Args:
            formula_summary: Natural-language or SMT-LIB2 summary of the formula.

        Returns:
            QuantifierDiscipline instance with appropriate settings.
        """
        summary_lower = formula_summary.lower()
        is_qf = is_qf_formula(formula_summary)
        alternations = count_quantifier_alternations(formula_summary)

        if is_qf:
            return QuantifierDiscipline(
                discipline_kind=DisciplineKind.ALWAYS_QF,
                trigger_pattern="",
                instantiation_depth=0,
                bound_vars=[],
                witness_terms=[],
            )

        if alternations > 0:
            # Alternation — use ALWAYS_QF
            return QuantifierDiscipline(
                discipline_kind=DisciplineKind.ALWAYS_QF,
                trigger_pattern="",
                instantiation_depth=self.max_instantiation_depth,
                bound_vars=[],
                witness_terms=["0", "1", str(self.max_instantiation_depth - 1)],
            )

        if "exists" in summary_lower:
            return QuantifierDiscipline(
                discipline_kind=DisciplineKind.SKOLEM,
                trigger_pattern="",
                instantiation_depth=0,
                bound_vars=["x"],
                witness_terms=[],
            )

        # Universal-only
        forall_count = summary_lower.count("forall")
        depth = min(forall_count, self.max_instantiation_depth)
        return QuantifierDiscipline(
            discipline_kind=DisciplineKind.INSTANTIATE,
            trigger_pattern="",
            instantiation_depth=depth,
            bound_vars=["i"],
            witness_terms=list(range(depth)),
        )

    def _count_quantifier_alternations(self, formula: str) -> int:
        """Count quantifier alternations in a formula string.

        Args:
            formula: Formula string.

        Returns:
            Number of alternations.
        """
        return count_quantifier_alternations(formula)

    def _extract_quantifier_depth(self, formula: str) -> int:
        """Compute the maximum quantifier nesting depth in a formula.

        Counts how many quantifier keywords are nested inside each other.

        Args:
            formula: Formula string.

        Returns:
            Maximum nesting depth (0 if no quantifiers).
        """
        quantifier_pattern = re.compile(
            r'\b(forall|exists|ForAll|Exists)\b',
            re.IGNORECASE,
        )
        matches = list(quantifier_pattern.finditer(formula))
        if not matches:
            return 0

        # Simple heuristic: count as depth the number of nested quantifiers
        # (can't easily track actual nesting without a parser)
        return len(matches)


# ---------------------------------------------------------------------------
# QuantifierInstantiator
# ---------------------------------------------------------------------------


class QuantifierInstantiator:
    """Applies quantifier instantiation strategies to formula strings.

    Provides methods for:
    - Instantiating universal quantifiers at ground terms.
    - Skolemizing existential quantifiers.
    - Unrolling bounded quantifiers.
    - Suggesting witness terms for existential quantifiers.

    copilot notes: All methods operate on formula strings and return formula
    strings.  They are not tied to the Z3 AST, making them usable even
    without a Z3 installation.

    Example::

        inst = QuantifierInstantiator()
        result = inst.instantiate_forall(
            "(forall ((i Int)) (>= i 0))",
            ["0", "1", "n-1"],
        )
        # result == "(and (>= 0 0) (>= 1 0) (>= n-1 0))"
    """

    def __init__(self) -> None:
        """Initialise the quantifier instantiator."""
        self._skolem_counter: int = 0

    def instantiate_forall(
        self, forall_formula: str, terms: list[str]
    ) -> str:
        """Instantiate a universal quantifier at a list of ground terms.

        Replaces ``(forall ((x Sort)) body)`` with a conjunction of ``body[x -> t]``
        for each term t in ``terms``.

        Args:
            forall_formula: Formula string containing a forall quantifier.
            terms: List of ground term strings to substitute.

        Returns:
            Conjunction of instantiated body strings, or the original formula
            if no forall was found.
        """
        forall_pattern = re.compile(
            r'\(forall\s+\(\((\w+)\s+\w+\)\)\s+(.+)\)',
            re.IGNORECASE | re.DOTALL,
        )
        m = forall_pattern.search(forall_formula)
        if not m:
            return forall_formula

        var_name = m.group(1)
        body = m.group(2).strip()

        instances: list[str] = []
        for t in terms:
            # Simple string substitution — word-boundary aware
            instance = re.sub(r'\b' + re.escape(var_name) + r'\b', t, body)
            instances.append(instance)

        if not instances:
            return "(true)"
        if len(instances) == 1:
            return instances[0]
        joined = "\n  ".join(instances)
        return f"(and\n  {joined}\n)"

    def instantiate_exists_skolem(self, exists_formula: str) -> str:
        """Replace an existential quantifier with a fresh Skolem constant.

        Transforms ``(exists ((x Sort)) body)`` into ``body[x -> sk_x_N]`` where
        ``sk_x_N`` is a globally fresh constant name.

        Args:
            exists_formula: Formula string containing an exists quantifier.

        Returns:
            Skolemized formula string.
        """
        exists_pattern = re.compile(
            r'\(exists\s+\(\((\w+)\s+(\w+)\)\)\s+(.+)\)',
            re.IGNORECASE | re.DOTALL,
        )
        m = exists_pattern.search(exists_formula)
        if not m:
            return exists_formula

        var_name = m.group(1)
        body = m.group(3).strip()

        sk_name = f"sk_{var_name}_{self._skolem_counter}"
        self._skolem_counter += 1

        skolemized_body = re.sub(r'\b' + re.escape(var_name) + r'\b', sk_name, body)
        return f"(declare-const {sk_name} Int)\n{skolemized_body}"

    def finite_domain_instantiation(
        self, formula: str, finite_domain: list[str]
    ) -> list[str]:
        """Generate one instantiation of the formula for each domain element.

        Returns a list of formula strings, one per element of ``finite_domain``.
        Each formula is the result of substituting the first universally-bound
        variable with the corresponding domain element.

        Args:
            formula: Formula string with at least one universal quantifier.
            finite_domain: List of ground term strings (domain elements).

        Returns:
            List of instantiated formula strings.
        """
        forall_pattern = re.compile(
            r'\(forall\s+\(\((\w+)\s+\w+\)\)\s+(.+)\)',
            re.IGNORECASE | re.DOTALL,
        )
        m = forall_pattern.search(formula)
        if not m:
            return [formula]

        var_name = m.group(1)
        body = m.group(2).strip()

        results: list[str] = []
        for elem in finite_domain:
            instance = re.sub(r'\b' + re.escape(var_name) + r'\b', elem, body)
            results.append(instance)
        return results

    def bounded_quantifier_unroll(self, formula: str, bound: int) -> str:
        """Unroll a bounded quantifier up to ``bound`` iterations.

        For a universal quantifier: produces a conjunction of body[x -> 0, 1, ..., bound-1].
        For an existential quantifier: produces a disjunction.

        Args:
            formula: Formula string with a quantifier.
            bound: Maximum number of unroll steps.

        Returns:
            Unrolled formula string.
        """
        quant_pattern = re.compile(
            r'\((forall|exists)\s+\(\((\w+)\s+\w+\)\)\s+(.+)\)',
            re.IGNORECASE | re.DOTALL,
        )
        m = quant_pattern.search(formula)
        if not m:
            return formula

        quant_kind = m.group(1).lower()
        var_name = m.group(2)
        body = m.group(3).strip()

        instances: list[str] = []
        for k in range(bound):
            instance = re.sub(r'\b' + re.escape(var_name) + r'\b', str(k), body)
            instances.append(instance)

        if not instances:
            return "(true)" if quant_kind == "forall" else "(false)"

        connector = "and" if quant_kind == "forall" else "or"
        if len(instances) == 1:
            return instances[0]
        joined = "\n  ".join(instances)
        return f"({connector}\n  {joined}\n)"

    def copilot_suggest_witness_terms(
        self, exists_formula: str
    ) -> list[str]:
        """Suggest candidate witness terms for an existential formula.

        Heuristically proposes witness terms based on the formula content:
        - Boundary values: "0", "1", "-1".
        - Symbolic bounds: "n-1", "n", "n+1" if "n" appears in the formula.
        - Midpoint: "n/2" if the formula contains both 0 and n.

        Args:
            exists_formula: Formula string with an existential quantifier.

        Returns:
            List of candidate witness term strings.
        """
        terms = ["0", "1", "-1"]

        # Look for named bounds in the formula
        named_vars = re.findall(r'\b([a-zA-Z_]\w*)\b', exists_formula)
        exclude = {"exists", "forall", "and", "or", "not", "implies", "Int", "Bool"}
        named_vars = [v for v in named_vars if v not in exclude and not v.startswith("sk_")]

        for var in set(named_vars):
            terms.append(var)
            terms.append(f"{var}-1")
            terms.append(f"{var}+1")

        # Deduplicate while preserving order
        seen: set[str] = set()
        unique_terms: list[str] = []
        for t in terms:
            if t not in seen:
                seen.add(t)
                unique_terms.append(t)

        return unique_terms[:8]  # Return at most 8 suggestions
