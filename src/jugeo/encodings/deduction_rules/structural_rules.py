r"""Structural rules for JuGeo — ``theory2.tex`` Chapter 33, §33.2.

Structural rules are inference rules that operate on the *context* Γ without
examining the logical content of the formula being proved.  The four classical
structural rules are:

Weakening
  .. math::

     \frac{\Gamma \vdash J}{\Gamma, A \vdash J}  \;[\text{weak}]

Contraction
  .. math::

     \frac{\Gamma, A, A \vdash J}{\Gamma, A \vdash J}  \;[\text{contr}]

Exchange
  .. math::

     \frac{\Gamma, A, B, \Delta \vdash J}
          {\Gamma, B, A, \Delta \vdash J}  \;[\text{exch}]

Cut
  .. math::

     \frac{\Gamma \vdash A \quad \Gamma, A \vdash B}
          {\Gamma \vdash B}  \;[\text{cut}]

The key result (§33.4) is that cut is *admissible*: any proof using cut can be
transformed into a cut-free proof.

Architecture
------------
- :class:`WeakeningRule`        – weakening rule implementation
- :class:`ContractionRule`      – contraction rule implementation
- :class:`ExchangeRule`         – exchange rule implementation
- :class:`CutRule`              – cut rule and cut-elimination procedure
- :class:`StructuralRuleSystem` – aggregates all structural rules
- :class:`PermutationLemma`     – permutation lemmas for structural rule reordering
"""

from __future__ import annotations

import hashlib
import itertools
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any, Sequence

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
        rule_kind: Any = "structural"
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


# ---------------------------------------------------------------------------
# Local dataclass: RuleSchema
# (not yet exported by models.py — defined here for self-documentation)
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class RuleSchema:
    """A fully specified inference rule schema, including LaTeX rendering.

    Attributes
    ----------
    rule_id:
        Stable unique identifier derived from the rule name.
    name:
        Human-readable name (e.g. ``"weakening"``, ``"cut"``).
    premises:
        Ordered list of premise schemas as display strings.
    conclusion:
        The conclusion schema as a display string.
    latex_premises:
        LaTeX rendering of each premise.
    latex_conclusion:
        LaTeX rendering of the conclusion.
    description:
        Short prose description of the rule.
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
_CUT_MARKERS = {"[cut]", "[cut on", "[cut-rule]", "cut:", " cut "}


def _parse_judgment(judgment_str: str) -> tuple[list[str], str]:
    """Split a judgment string of the form ``Γ ⊢ J`` into context and conclusion.

    Parameters
    ----------
    judgment_str:
        A string containing a single turnstile ``⊢`` separating context from
        conclusion.  The context may be empty (representing the empty context).

    Returns
    -------
    tuple[list[str], str]
        ``(hypotheses, conclusion)`` where *hypotheses* is a list of
        stripped hypothesis strings and *conclusion* is the right-hand side.

    Examples
    --------
    >>> _parse_judgment("A, B ⊢ C")
    (['A', 'B'], 'C')
    >>> _parse_judgment("∅ ⊢ ⊤")
    ([], '⊤')
    """
    if _TURNSTILE in judgment_str:
        lhs, _, rhs = judgment_str.partition(_TURNSTILE)
        raw = lhs.strip()
        if raw in ("∅", "", "·", "·"):
            context: list[str] = []
        else:
            context = [h.strip() for h in raw.split(",") if h.strip()]
        conclusion = rhs.strip()
    else:
        context = []
        conclusion = judgment_str.strip()
    return context, conclusion


def _format_judgment(context: list[str], conclusion: str) -> str:
    """Reconstruct a judgment string from a context list and conclusion.

    Parameters
    ----------
    context:
        List of hypothesis strings.  An empty list is rendered as ``∅``.
    conclusion:
        The conclusion formula.

    Returns
    -------
    str
        ``"H1, H2, ..., Hn ⊢ conclusion"`` or ``"∅ ⊢ conclusion"``.
    """
    ctx_str = ", ".join(context) if context else "∅"
    return f"{ctx_str}{_TURNSTILE}{conclusion}"


def _is_well_formed(judgment_str: str) -> bool:
    """Return ``True`` when *judgment_str* is a syntactically valid judgment."""
    return bool(judgment_str and judgment_str.strip() and _TURNSTILE in judgment_str)


def _fresh_rule_id(prefix: str) -> str:
    """Generate a fresh rule ID with the given prefix."""
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


# ---------------------------------------------------------------------------
# WeakeningRule
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class WeakeningRule:
    """Weakening structural rule: Γ ⊢ J  ⟹  Γ, A ⊢ J.

    The weakening rule allows adding unused hypotheses to the context.  It
    is admissible in every sequent calculus: a proof of ``J`` from ``Γ``
    serves unchanged as a proof of ``J`` from any extension ``Γ, A`` because
    the new hypothesis ``A`` is never referenced.

    Attributes
    ----------
    rule_id:
        Stable unique identifier for this rule instance.
    name:
        Display name (default ``"weakening"``).
    direction:
        ``"left"`` for standard weakening on the antecedent;
        ``"right"`` for weakening on the succedent (less common).
    metadata:
        Free-form annotations for provenance and tooling.
    """

    rule_id: str
    name: str = "weakening"
    direction: str = "left"
    metadata: dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # schema
    # ------------------------------------------------------------------

    def schema(self) -> RuleSchema:
        """Return the :class:`RuleSchema` for the weakening rule.

        The schema records the formal inference figure::

            Γ ⊢ J
            ─────────────  [weak]
            Γ, A ⊢ J

        Returns
        -------
        RuleSchema
            Fully populated schema with LaTeX annotations.
        """
        return RuleSchema(
            rule_id=self.rule_id,
            name=self.name,
            premises=["Γ ⊢ J"],
            conclusion="Γ, A ⊢ J",
            latex_premises=[r"\Gamma \vdash J"],
            latex_conclusion=r"\Gamma, A \vdash J",
            description=(
                "Given a proof of J in context Γ, add any unused hypothesis A "
                "to obtain a proof of J in the extended context Γ, A.  "
                "The hypothesis A plays no role in the proof."
            ),
        )

    # ------------------------------------------------------------------
    # apply
    # ------------------------------------------------------------------

    def apply(self, judgment_str: str, new_hypothesis: str) -> str:
        """Produce the weakened judgment ``Γ, A ⊢ J``.

        Parameters
        ----------
        judgment_str:
            A well-formed judgment of the form ``Γ ⊢ J``.
        new_hypothesis:
            The formula *A* to append to the context.

        Returns
        -------
        str
            The judgment with *new_hypothesis* added.

        Raises
        ------
        ValueError
            If *judgment_str* is not a well-formed judgment.

        Examples
        --------
        >>> w = WeakeningRule(rule_id="w-1")
        >>> w.apply("A ⊢ B", "C")
        'A, C ⊢ B'
        """
        if not self.is_applicable(judgment_str):
            raise ValueError(
                f"WeakeningRule.apply: ill-formed judgment {judgment_str!r}.  "
                "Expected a string containing ' ⊢ '."
            )
        context, conclusion = _parse_judgment(judgment_str)
        hyp = new_hypothesis.strip()
        if self.direction == "left":
            new_context = context + [hyp]
        else:
            # right-weakening: add to front (less common convention)
            new_context = [hyp] + context
        return _format_judgment(new_context, conclusion)

    def apply_repeated(
        self, judgment_str: str, hypotheses: Sequence[str]
    ) -> str:
        """Apply weakening multiple times, one hypothesis at a time.

        Parameters
        ----------
        judgment_str:
            The starting well-formed judgment.
        hypotheses:
            An ordered sequence of hypotheses to add left-to-right.

        Returns
        -------
        str
            The judgment after all weakenings have been applied.

        Examples
        --------
        >>> w = WeakeningRule(rule_id="w-1")
        >>> w.apply_repeated("∅ ⊢ ⊤", ["A", "B", "C"])
        'A, B, C ⊢ ⊤'
        """
        result = judgment_str
        for hyp in hypotheses:
            result = self.apply(result, hyp)
        return result

    # ------------------------------------------------------------------
    # is_applicable
    # ------------------------------------------------------------------

    def is_applicable(self, judgment_str: str) -> bool:
        """Return ``True`` for any well-formed judgment.

        Weakening places no constraints on the formula being proved —
        it is universally applicable to every valid judgment.

        Parameters
        ----------
        judgment_str:
            The candidate judgment string.

        Returns
        -------
        bool
            ``True`` iff the string is a well-formed judgment.
        """
        return _is_well_formed(judgment_str)

    # ------------------------------------------------------------------
    # admissibility_certificate
    # ------------------------------------------------------------------

    def admissibility_certificate(self) -> dict[str, Any]:
        """Return a machine-readable certificate that weakening is admissible.

        Weakening is admissible because:

        1. Any derivation tree for ``Γ ⊢ J`` uses only hypotheses in ``Γ``.
        2. The new hypothesis ``A`` is never referenced, so the derivation
           tree is valid verbatim in the extended context ``Γ, A``.

        Returns
        -------
        dict[str, Any]
            Certificate with proof strategy, complexity, and references.
        """
        return {
            "rule": self.name,
            "rule_id": self.rule_id,
            "admissible": True,
            "proof_strategy": (
                "Structural induction on the derivation tree T of Γ ⊢ J.  "
                "Base: if T is an axiom Γ ∋ J, then J is still in Γ, A.  "
                "Inductive step: for each rule application in T, the rule "
                "applies unchanged in the extended context since the rule "
                "operates on Γ, and Γ ⊆ Γ, A."
            ),
            "complexity": "O(|T|) — linear in proof size",
            "preserves_cut_freeness": True,
            "preserves_normality": True,
            "references": [
                "Gentzen 1935, Untersuchungen über das logische Schliessen, §2.4",
                "theory2.tex §33.2 (Structural Admissibility)",
                "Troelstra & Schwichtenberg, Basic Proof Theory, ch. 3",
                "Negri & von Plato, Structural Proof Theory, §1.3",
            ],
            "certificate_id": hashlib.sha256(
                f"weakening:{self.rule_id}:admissible".encode()
            ).hexdigest()[:16],
        }

    # ------------------------------------------------------------------
    # invert
    # ------------------------------------------------------------------

    def invert(self) -> "ContractionRule | None":
        """Return a :class:`ContractionRule` that partially inverts this rule.

        Weakening adds a hypothesis; contraction removes duplicates.  They
        are not exact inverses, but contraction can undo a weakening that
        introduced a formula already present in the context.

        Returns
        -------
        ContractionRule | None
            A new ``ContractionRule`` paired with this weakening's rule_id,
            or ``None`` if this rule's ``rule_id`` is empty.
        """
        if not self.rule_id:
            return None
        return ContractionRule(
            rule_id=f"{self.rule_id}-inv",
            metadata={
                "inverse_of": self.rule_id,
                "note": (
                    "This contraction undoes a weakening that added a "
                    "hypothesis already present in the context."
                ),
            },
        )

    # ------------------------------------------------------------------
    # to_deduction_rule
    # ------------------------------------------------------------------

    def to_deduction_rule(self) -> DeductionRule:
        """Convert to a :class:`DeductionRule` from the models module.

        Returns
        -------
        DeductionRule
            A ``DeductionRule`` with ``rule_kind=RuleKind.STRUCTURAL`` and
            admissibility metadata.
        """
        sch = self.schema()
        return DeductionRule(
            rule_id=self.rule_id,
            rule_name=self.name,
            premises=tuple(sch.premises),
            conclusion=sch.conclusion,
            side_conditions={},
            rule_kind=RuleKind.STRUCTURAL if _models_ok else "structural",
            metadata={
                **self.metadata,
                "admissible": True,
                "direction": self.direction,
                "description": sch.description,
            },
        )

    # ------------------------------------------------------------------
    # explain
    # ------------------------------------------------------------------

    def explain(self) -> str:
        """Return a multi-line human-readable explanation of weakening.

        Returns
        -------
        str
            A formatted string covering the rule statement, intuition,
            admissibility, and this instance's configuration.
        """
        border = "=" * 52
        return (
            f"WeakeningRule  [{self.rule_id}]\n"
            f"{border}\n"
            "Source  : theory2.tex, Chapter 33, §33.2\n"
            "Rule ID : " + self.rule_id + "\n"
            "\n"
            "Formal Statement\n"
            "----------------\n"
            "     Γ ⊢ J\n"
            "  ────────────  [weak]\n"
            "  Γ, A ⊢ J\n"
            "\n"
            "Intuition\n"
            "---------\n"
            "  If we can prove J using only the hypotheses in Γ, then we\n"
            "  can also prove J in any richer context Γ, A.  The extra\n"
            "  hypothesis A is simply never used in the proof.\n"
            "\n"
            "Admissibility\n"
            "-------------\n"
            "  Weakening is admissible via structural induction on proofs:\n"
            "  the derivation of Γ ⊢ J is valid unchanged in Γ, A because\n"
            "  all hypothesis lookups remain satisfied.\n"
            "\n"
            f"Configuration\n"
            f"  direction : {self.direction}\n"
            f"  metadata  : {self.metadata}\n"
        )

    # ------------------------------------------------------------------
    # copilot_suggest
    # ------------------------------------------------------------------

    def copilot_suggest(self, context_str: str) -> list[str]:
        """Suggest hypotheses that could be weakened in.

        # copilot suggest hypotheses to add to the given context via weakening.
        Uses heuristics based on common patterns in type-theoretic proofs.

        Parameters
        ----------
        context_str:
            Either a full judgment ``"Γ ⊢ J"`` or a bare context string.

        Returns
        -------
        list[str]
            Up to five suggested hypotheses, most useful first.
        """
        if _TURNSTILE in context_str:
            context, conclusion = _parse_judgment(context_str)
        else:
            context, conclusion = [h.strip() for h in context_str.split(",") if h.strip()], ""

        suggestions: list[str] = []

        # Classical tautology — always safe to weaken in
        suggestions.append("⊤")

        # Negations of existing hypotheses enable classical reasoning
        for hyp in context[:2]:
            cleaned = hyp.strip()
            if cleaned and not cleaned.startswith("¬"):
                suggestions.append(f"¬{cleaned}")

        # Inhabited/nonempty witnesses — common in dependent type theory
        if context:
            suggestions.append(f"Inhabited({context[0].strip()})")

        # Standard mathematical assumptions
        suggestions.append("WellFounded(≺)")
        suggestions.append("Decidable(A)")

        # If conclusion is visible, suggest its preconditions
        if conclusion and conclusion not in context:
            suggestions.append(f"Provable({conclusion})")

        return suggestions[:5]


# ---------------------------------------------------------------------------
# ContractionRule
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class ContractionRule:
    """Contraction structural rule: Γ, A, A ⊢ J  ⟹  Γ, A ⊢ J.

    The contraction rule allows identifying duplicate occurrences of the same
    hypothesis.  It is admissible in proof systems where hypothesis use is
    unrestricted: a proof that uses A twice can always be reorganised to use
    it once by substituting any second reference with the first.

    Attributes
    ----------
    rule_id:
        Stable unique identifier.
    name:
        Display name (default ``"contraction"``).
    metadata:
        Free-form annotations.
    """

    rule_id: str
    name: str = "contraction"
    metadata: dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # schema
    # ------------------------------------------------------------------

    def schema(self) -> RuleSchema:
        """Return the :class:`RuleSchema` for contraction.

        The schema records::

            Γ, A, A ⊢ J
            ────────────  [contr]
            Γ, A ⊢ J
        """
        return RuleSchema(
            rule_id=self.rule_id,
            name=self.name,
            premises=["Γ, A, A ⊢ J"],
            conclusion="Γ, A ⊢ J",
            latex_premises=[r"\Gamma, A, A \vdash J"],
            latex_conclusion=r"\Gamma, A \vdash J",
            description=(
                "If J is derivable with hypothesis A listed twice in the "
                "context, then J is derivable with A listed only once.  "
                "Hypotheses may be freely duplicated or contracted."
            ),
        )

    # ------------------------------------------------------------------
    # apply
    # ------------------------------------------------------------------

    def apply(self, judgment_str: str) -> str:
        """Find and remove the second occurrence of the first duplicated hypothesis.

        Parameters
        ----------
        judgment_str:
            A judgment ``Γ, A, A, ... ⊢ J`` containing at least one
            duplicate hypothesis.

        Returns
        -------
        str
            The judgment with one copy of the duplicated hypothesis removed.

        Raises
        ------
        ValueError
            If no duplicate hypothesis is found.
        """
        context, conclusion = _parse_judgment(judgment_str)
        pairs = self.find_contractions(context)
        if not pairs:
            raise ValueError(
                f"ContractionRule.apply: no duplicate hypotheses found in "
                f"{judgment_str!r}.  Context: {context}"
            )
        # Remove the *second* occurrence of the first duplicated pair
        _, j = pairs[0]
        new_context = [h for k, h in enumerate(context) if k != j]
        return _format_judgment(new_context, conclusion)

    # ------------------------------------------------------------------
    # find_contractions
    # ------------------------------------------------------------------

    def find_contractions(self, context: list[str]) -> list[tuple[int, int]]:
        """Find all pairs of duplicate hypotheses in *context*.

        Parameters
        ----------
        context:
            List of hypothesis strings.

        Returns
        -------
        list[tuple[int, int]]
            Sorted list of ``(i, j)`` pairs with ``i < j`` and
            ``context[i] == context[j]``.

        Examples
        --------
        >>> c = ContractionRule(rule_id="c-1")
        >>> c.find_contractions(["A", "B", "A", "C", "B"])
        [(0, 2), (1, 4)]
        """
        pairs: list[tuple[int, int]] = []
        first_seen: dict[str, int] = {}
        for idx, hyp in enumerate(context):
            normalized = hyp.strip()
            if normalized in first_seen:
                pairs.append((first_seen[normalized], idx))
            else:
                first_seen[normalized] = idx
        return pairs

    # ------------------------------------------------------------------
    # is_applicable
    # ------------------------------------------------------------------

    def is_applicable(self, judgment_str: str) -> bool:
        """Return ``True`` iff the judgment has at least one duplicate hypothesis.

        Parameters
        ----------
        judgment_str:
            The candidate judgment string.

        Returns
        -------
        bool
        """
        if not _is_well_formed(judgment_str):
            return False
        context, _ = _parse_judgment(judgment_str)
        return bool(self.find_contractions(context))

    # ------------------------------------------------------------------
    # admissibility_certificate
    # ------------------------------------------------------------------

    def admissibility_certificate(self) -> dict[str, Any]:
        """Return a certificate that contraction is admissible.

        Contraction is admissible because:

        1. In a derivation of ``Γ, A, A ⊢ J``, every use of the *second*
           copy of ``A`` can be replaced by the *first* copy.
        2. The derivation structure and all other rule applications are
           unchanged.

        Returns
        -------
        dict[str, Any]
        """
        return {
            "rule": self.name,
            "rule_id": self.rule_id,
            "admissible": True,
            "proof_strategy": (
                "Structural induction on the derivation D of Γ, A, A ⊢ J.  "
                "Whenever a leaf references the second copy of A (by position), "
                "redirect it to the first copy.  The logical content is unchanged; "
                "all rule applications remain valid."
            ),
            "complexity": "O(|D|²) in the worst case when rule permutation is needed",
            "preserves_cut_freeness": True,
            "note": (
                "In linear-logic systems contraction is NOT admissible and "
                "must be explicitly tracked as a resource."
            ),
            "references": [
                "Gentzen 1935, §2.4",
                "theory2.tex §33.2",
                "Buss (ed.), Handbook of Proof Theory, ch. 1",
            ],
            "certificate_id": hashlib.sha256(
                f"contraction:{self.rule_id}:admissible".encode()
            ).hexdigest()[:16],
        }

    # ------------------------------------------------------------------
    # normalize_context
    # ------------------------------------------------------------------

    def normalize_context(self, context: list[str]) -> list[str]:
        """Deduplicate *context*, preserving the order of first occurrences.

        Equivalent to applying contraction exhaustively until no duplicate
        remains.

        Parameters
        ----------
        context:
            Possibly-redundant list of hypothesis strings.

        Returns
        -------
        list[str]
            Deduplicated list in original order.

        Examples
        --------
        >>> c = ContractionRule(rule_id="c-1")
        >>> c.normalize_context(["A", "B", "A", "C", "B", "D"])
        ['A', 'B', 'C', 'D']
        """
        seen: set[str] = set()
        result: list[str] = []
        for hyp in context:
            key = hyp.strip()
            if key not in seen:
                seen.add(key)
                result.append(hyp)
        return result

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
            rule_kind=RuleKind.STRUCTURAL if _models_ok else "structural",
            metadata={**self.metadata, "admissible": True},
        )

    # ------------------------------------------------------------------
    # explain
    # ------------------------------------------------------------------

    def explain(self) -> str:
        """Return a multi-line human-readable explanation of contraction."""
        border = "=" * 52
        return (
            f"ContractionRule  [{self.rule_id}]\n"
            f"{border}\n"
            "Source  : theory2.tex, Chapter 33, §33.2\n"
            "\n"
            "Formal Statement\n"
            "----------------\n"
            "  Γ, A, A ⊢ J\n"
            "  ────────────  [contr]\n"
            "  Γ, A ⊢ J\n"
            "\n"
            "Intuition\n"
            "---------\n"
            "  If J can be proved assuming A twice, it can be proved\n"
            "  assuming A once.  Resources (hypotheses) can be freely\n"
            "  duplicated in a structural (non-linear) proof system.\n"
            "\n"
            "Admissibility\n"
            "-------------\n"
            "  Contraction is admissible by induction on the derivation\n"
            "  tree: every use of the second copy of A is redirected to\n"
            "  the first.  No new proof obligations are introduced.\n"
            "\n"
            "Linear Logic Note\n"
            "-----------------\n"
            "  In linear logic, contraction is NOT admissible and must be\n"
            "  tracked explicitly via the '!' (bang) modality.\n"
            "\n"
            f"Metadata  : {self.metadata}\n"
        )


# ---------------------------------------------------------------------------
# ExchangeRule
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class ExchangeRule:
    """Exchange structural rule: swap hypotheses in the context.

    The exchange rule states that the order of hypotheses in the context is
    immaterial::

        Γ, A, B, Δ ⊢ J
        ─────────────────  [exch]
        Γ, B, A, Δ ⊢ J

    It is always admissible in proof systems that treat contexts as multisets
    (or sets).

    Attributes
    ----------
    rule_id:
        Stable unique identifier.
    name:
        Display name (default ``"exchange"``).
    allow_arbitrary_permutation:
        If ``True`` (default), any two positions may be swapped.  If
        ``False``, only adjacent transpositions ``|i - j| == 1`` are
        permitted.
    metadata:
        Free-form annotations.
    """

    rule_id: str
    name: str = "exchange"
    allow_arbitrary_permutation: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # schema
    # ------------------------------------------------------------------

    def schema(self) -> RuleSchema:
        """Return the :class:`RuleSchema` for exchange."""
        return RuleSchema(
            rule_id=self.rule_id,
            name=self.name,
            premises=["Γ, A, B, Δ ⊢ J"],
            conclusion="Γ, B, A, Δ ⊢ J",
            latex_premises=[r"\Gamma, A, B, \Delta \vdash J"],
            latex_conclusion=r"\Gamma, B, A, \Delta \vdash J",
            description=(
                "Swap two hypotheses in the context.  When "
                "allow_arbitrary_permutation is True any two positions may "
                "be exchanged; when False only adjacent transpositions are "
                "permitted (matching the standard sequent-calculus formulation)."
            ),
        )

    # ------------------------------------------------------------------
    # apply
    # ------------------------------------------------------------------

    def apply(self, context: list[str], i: int, j: int) -> list[str]:
        """Return a new context list with positions *i* and *j* swapped.

        Parameters
        ----------
        context:
            The current hypothesis list.
        i, j:
            Indices to swap (0-based).

        Returns
        -------
        list[str]
            New context after the swap.

        Raises
        ------
        ValueError
            If the swap is not applicable (invalid indices or adjacency
            constraint violated).
        """
        if not self.is_applicable(context, i, j):
            raise ValueError(
                f"ExchangeRule.apply: cannot swap positions {i} and {j} in a "
                f"context of length {len(context)}.  "
                f"allow_arbitrary_permutation={self.allow_arbitrary_permutation}"
            )
        result = list(context)
        result[i], result[j] = result[j], result[i]
        return result

    # ------------------------------------------------------------------
    # apply_permutation
    # ------------------------------------------------------------------

    def apply_permutation(
        self, context: list[str], permutation: list[int]
    ) -> list[str]:
        """Apply an arbitrary permutation to *context*.

        Parameters
        ----------
        context:
            The hypothesis list to permute.
        permutation:
            A list of length ``len(context)`` where ``permutation[i]``
            gives the *source* index in *context* that should appear at
            position *i* in the output.

        Returns
        -------
        list[str]
            Permuted context.

        Raises
        ------
        ValueError
            If *permutation* is not a valid permutation of
            ``{0, ..., len(context)-1}``.
        """
        n = len(context)
        if len(permutation) != n:
            raise ValueError(
                f"Permutation length {len(permutation)} != context length {n}"
            )
        if sorted(permutation) != list(range(n)):
            raise ValueError(
                f"Not a valid permutation of 0..{n - 1}: {permutation}"
            )
        return [context[permutation[i]] for i in range(n)]

    # ------------------------------------------------------------------
    # is_applicable
    # ------------------------------------------------------------------

    def is_applicable(self, context: list[str], i: int, j: int) -> bool:
        """Return ``True`` iff positions *i* and *j* can be swapped.

        Parameters
        ----------
        context:
            The hypothesis list.
        i, j:
            Proposed swap indices.

        Returns
        -------
        bool
        """
        n = len(context)
        if not (0 <= i < n and 0 <= j < n and i != j):
            return False
        if not self.allow_arbitrary_permutation and abs(i - j) != 1:
            return False
        return True

    # ------------------------------------------------------------------
    # sort_context
    # ------------------------------------------------------------------

    def sort_context(
        self, context: list[str]
    ) -> tuple[list[str], list[int]]:
        """Sort *context* lexicographically, returning the sorted list and permutation.

        The permutation ``perm`` satisfies:
        ``sorted_ctx[k] == context[perm[k]]`` for all ``k``.

        Parameters
        ----------
        context:
            The hypothesis list to sort.

        Returns
        -------
        tuple[list[str], list[int]]
            ``(sorted_context, permutation)`` where the permutation records
            which source position contributed to each target position.
        """
        indexed = sorted(enumerate(context), key=lambda x: (x[1], x[0]))
        perm = [src_idx for src_idx, _ in indexed]
        sorted_ctx = [val for _, val in indexed]
        return sorted_ctx, perm

    # ------------------------------------------------------------------
    # admissibility_certificate
    # ------------------------------------------------------------------

    def admissibility_certificate(self) -> dict[str, Any]:
        """Return a certificate that exchange is admissible."""
        return {
            "rule": self.name,
            "rule_id": self.rule_id,
            "admissible": True,
            "proof_strategy": (
                "Exchange is admissible because all proof rules treat contexts "
                "as multisets.  Every rule application is parameterised by the "
                "set of hypotheses, not their order.  Any permutation of the "
                "context therefore yields an equivalent judgment."
            ),
            "complexity": (
                "O(n log n) for canonical sort; O(n²) for bubble-sort "
                "decomposition into adjacent transpositions"
            ),
            "preserves_cut_freeness": True,
            "note": (
                "In ordered logic or proof-nets the order of hypotheses may "
                "carry semantic content and exchange is NOT freely admissible."
            ),
            "references": [
                "Girard, Proof Theory and Logical Complexity, vol. 1, §2",
                "theory2.tex §33.2",
                "Restall, An Introduction to Substructural Logics, ch. 2",
            ],
            "certificate_id": hashlib.sha256(
                f"exchange:{self.rule_id}:admissible".encode()
            ).hexdigest()[:16],
        }

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
            rule_kind=RuleKind.STRUCTURAL if _models_ok else "structural",
            metadata={
                **self.metadata,
                "admissible": True,
                "allow_arbitrary_permutation": self.allow_arbitrary_permutation,
            },
        )

    # ------------------------------------------------------------------
    # explain
    # ------------------------------------------------------------------

    def explain(self) -> str:
        """Return a multi-line human-readable explanation of exchange."""
        adj_note = (
            "  (Only adjacent swaps |i − j| = 1 are currently enabled.)\n"
            if not self.allow_arbitrary_permutation
            else ""
        )
        border = "=" * 52
        return (
            f"ExchangeRule  [{self.rule_id}]\n"
            f"{border}\n"
            "Source  : theory2.tex, Chapter 33, §33.2\n"
            "\n"
            "Formal Statement\n"
            "----------------\n"
            "  Γ, A, B, Δ ⊢ J\n"
            "  ─────────────────  [exch]\n"
            "  Γ, B, A, Δ ⊢ J\n"
            "\n"
            "Intuition\n"
            "---------\n"
            "  The order of hypotheses in the context is irrelevant.  Any\n"
            "  permutation of the context yields an equivalent judgment,\n"
            "  because proof rules reference hypotheses by content, not\n"
            "  by position.\n"
            "\n"
            "Admissibility\n"
            "-------------\n"
            "  Exchange is admissible because all proof rules treat contexts\n"
            "  as multisets.  The derivation is invariant under permutation.\n"
            "\n"
            f"{adj_note}"
            f"allow_arbitrary_permutation : {self.allow_arbitrary_permutation}\n"
            f"Metadata                    : {self.metadata}\n"
        )

    # ------------------------------------------------------------------
    # permutation_sequence
    # ------------------------------------------------------------------

    def permutation_sequence(
        self, source: list[str], target: list[str]
    ) -> list[tuple[int, int]] | None:
        """Find a sequence of adjacent swaps transforming *source* into *target*.

        Uses a bubble-sort-inspired algorithm.  Handles duplicate elements
        correctly by tracking by index.

        Parameters
        ----------
        source:
            The starting list.
        target:
            The desired ordering.

        Returns
        -------
        list[tuple[int, int]] | None
            A sequence of ``(i, i+1)`` adjacent-swap pairs, or ``None``
            if *target* is not a permutation of *source*.

        Examples
        --------
        >>> ex = ExchangeRule(rule_id="ex-1")
        >>> ex.permutation_sequence(["A", "B", "C"], ["C", "A", "B"])
        [(1, 2), (0, 1)]
        """
        if sorted(source) != sorted(target):
            return None

        current = list(source)
        swaps: list[tuple[int, int]] = []

        for target_pos, target_val in enumerate(target):
            # Find target_val in current[target_pos:]
            found = -1
            for search_pos in range(target_pos, len(current)):
                if current[search_pos] == target_val:
                    found = search_pos
                    break
            if found == -1:
                return None  # Should not happen if sorted() check passed
            # Bubble target_val left to target_pos
            while found > target_pos:
                current[found - 1], current[found] = (
                    current[found],
                    current[found - 1],
                )
                swaps.append((found - 1, found))
                found -= 1

        return swaps


# ---------------------------------------------------------------------------
# CutRule
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class CutRule:
    """Cut structural rule: Γ ⊢ A and Γ, A ⊢ B gives Γ ⊢ B.

    The cut rule is the sequent-calculus version of *modus ponens* /
    transitivity of entailment.  Gentzen's *Hauptsatz* (cut-elimination
    theorem, 1935) proves that every proof using cut can be converted into
    a cut-free proof, establishing the *subformula property* of the logic.

    Attributes
    ----------
    rule_id:
        Stable unique identifier.
    name:
        Display name (default ``"cut"``).
    cut_formula:
        The formula being cut on (may be empty if unspecified).
    enable_elimination:
        If ``True`` (default), :meth:`eliminate_cut` performs actual
        cut-elimination.  If ``False``, the step is annotated but not
        expanded.
    metadata:
        Free-form annotations.
    """

    rule_id: str
    name: str = "cut"
    cut_formula: str = ""
    enable_elimination: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # schema
    # ------------------------------------------------------------------

    def schema(self) -> RuleSchema:
        """Return the :class:`RuleSchema` for the cut rule."""
        return RuleSchema(
            rule_id=self.rule_id,
            name=self.name,
            premises=["Γ ⊢ A", "Γ, A ⊢ B"],
            conclusion="Γ ⊢ B",
            latex_premises=[
                r"\Gamma \vdash A",
                r"\Gamma, A \vdash B",
            ],
            latex_conclusion=r"\Gamma \vdash B",
            description=(
                "From proofs of A in Γ and B from A in Γ, derive B in Γ "
                "directly, eliminating the intermediate formula A (the "
                "cut formula)."
            ),
        )

    # ------------------------------------------------------------------
    # apply
    # ------------------------------------------------------------------

    def apply(
        self,
        left_judgment: str,
        right_judgment: str,
        cut_formula: str,
    ) -> str | None:
        """Produce the cut conclusion ``Γ ⊢ B``.

        Parameters
        ----------
        left_judgment:
            A judgment of the form ``Γ ⊢ A``.
        right_judgment:
            A judgment of the form ``Γ, A ⊢ B``.
        cut_formula:
            The formula *A* being eliminated.

        Returns
        -------
        str | None
            The combined judgment ``Γ ⊢ B``, or ``None`` if the two
            judgments are incompatible for cutting on *cut_formula*.
        """
        if not (_is_well_formed(left_judgment) and _is_well_formed(right_judgment)):
            return None

        left_ctx, left_conc = _parse_judgment(left_judgment)
        right_ctx, right_conc = _parse_judgment(right_judgment)

        cf = cut_formula.strip()

        # The left conclusion must be the cut formula
        if left_conc.strip() != cf:
            logger.debug(
                "CutRule.apply: left conclusion %r ≠ cut formula %r",
                left_conc,
                cf,
            )
            return None

        # The cut formula must appear in the right context
        right_stripped = [h.strip() for h in right_ctx]
        if cf not in right_stripped:
            logger.debug(
                "CutRule.apply: cut formula %r not in right context %r",
                cf,
                right_ctx,
            )
            return None

        # Remove the cut formula from the right context
        idx = right_stripped.index(cf)
        new_ctx = right_ctx[:idx] + right_ctx[idx + 1 :]

        # Verify that the base contexts match (order-insensitively)
        if sorted(h.strip() for h in left_ctx) != sorted(
            h.strip() for h in new_ctx
        ):
            logger.debug(
                "CutRule.apply: left context %r ≠ right context after removal %r",
                left_ctx,
                new_ctx,
            )
            return None

        return _format_judgment(left_ctx, right_conc)

    # ------------------------------------------------------------------
    # eliminate_cut
    # ------------------------------------------------------------------

    def eliminate_cut(
        self,
        left_proof: list[str],
        right_proof: list[str],
        cut_formula: str,
    ) -> list[str]:
        """Perform cut-elimination on two proof traces.

        This trace-level procedure:

        1. Annotates *left_proof* steps as ``[from left] …``.
        2. Walks *right_proof*, and wherever the *cut_formula* appears
           in a step, inlines the annotated left steps.
        3. Returns the combined cut-free trace.

        For a rigorous cut-elimination proof see the Hauptsatz certificate.

        Parameters
        ----------
        left_proof:
            Ordered list of proof steps establishing ``Γ ⊢ A``.
        right_proof:
            Ordered list of proof steps establishing ``Γ, A ⊢ B``.
        cut_formula:
            The formula *A* to eliminate.

        Returns
        -------
        list[str]
            Combined cut-free proof trace.
        """
        if not self.enable_elimination:
            return (
                left_proof
                + [f"[cut on '{cut_formula}' — elimination disabled]"]
                + right_proof
            )

        annotated_left = [f"[left: {s}]" for s in left_proof]
        result: list[str] = list(annotated_left)

        for step in right_proof:
            if cut_formula in step:
                result.append(
                    f"[cut-eliminated] step '{step}' used '{cut_formula}'; "
                    "inlining left derivation:"
                )
                for ls in annotated_left:
                    result.append(f"    ↳ {ls}")
            else:
                result.append(step)

        return result

    # ------------------------------------------------------------------
    # find_cut_formula
    # ------------------------------------------------------------------

    def find_cut_formula(
        self,
        left_conclusion: str,
        right_premises: list[str],
    ) -> str | None:
        """Identify the cut formula shared between left conclusion and right context.

        Parameters
        ----------
        left_conclusion:
            The conclusion of the left derivation.
        right_premises:
            The context (list of hypotheses) of the right derivation.

        Returns
        -------
        str | None
            The matching formula string, or ``None`` if not found.
        """
        needle = left_conclusion.strip()
        for premise in right_premises:
            if premise.strip() == needle:
                return needle
        return None

    # ------------------------------------------------------------------
    # is_applicable
    # ------------------------------------------------------------------

    def is_applicable(self, left: str, right: str) -> bool:
        """Return ``True`` iff *left* and *right* can be joined by a cut.

        Checks that the conclusion of *left* appears as a hypothesis in
        the context of *right*.

        Parameters
        ----------
        left, right:
            Candidate judgment strings.

        Returns
        -------
        bool
        """
        if not (_is_well_formed(left) and _is_well_formed(right)):
            return False
        _, left_conc = _parse_judgment(left)
        right_ctx, _ = _parse_judgment(right)
        return left_conc.strip() in [h.strip() for h in right_ctx]

    # ------------------------------------------------------------------
    # admissibility_certificate
    # ------------------------------------------------------------------

    def admissibility_certificate(self) -> dict[str, Any]:
        """Return the Hauptsatz (cut-elimination) certificate."""
        return {
            "rule": self.name,
            "rule_id": self.rule_id,
            "admissible": True,
            "theorem": "Hauptsatz — Gentzen (1935)",
            "proof_strategy": (
                "Double induction: outer on the logical complexity (grade) of "
                "the cut formula A; inner on the total height of the two "
                "sub-derivations.  Base: if A is atomic, the cut dissolves "
                "into a contraction.  Inductive step: if A is compound, "
                "permute cuts upward through inference rules until they "
                "reach introduction rules, where a key-case reduction "
                "decreases the cut grade."
            ),
            "complexity": (
                "Non-elementary in the nesting depth of cuts (tower of "
                "exponentials).  This is tight for first-order logic."
            ),
            "enable_elimination": self.enable_elimination,
            "subformula_property": True,
            "consistency_consequence": (
                "Cut-elimination implies consistency: if ⊢ ⊥ were derivable, "
                "a cut-free proof would exist, but cut-free proofs of ⊥ do "
                "not exist in any consistent system."
            ),
            "references": [
                "Gentzen 1935, Untersuchungen über das logische Schliessen",
                "theory2.tex §33.4 (Cut-Elimination)",
                "Negri & von Plato, Structural Proof Theory, ch. 6",
                "Buss, Handbook of Proof Theory, ch. 2",
            ],
            "certificate_id": hashlib.sha256(
                f"cut:{self.rule_id}:hauptsatz".encode()
            ).hexdigest()[:16],
        }

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
            rule_kind=RuleKind.STRUCTURAL if _models_ok else "structural",
            metadata={
                **self.metadata,
                "admissible": True,
                "enable_elimination": self.enable_elimination,
                "cut_formula": self.cut_formula,
            },
        )

    # ------------------------------------------------------------------
    # explain
    # ------------------------------------------------------------------

    def explain(self) -> str:
        """Return a multi-line human-readable explanation of the cut rule."""
        border = "=" * 52
        return (
            f"CutRule  [{self.rule_id}]\n"
            f"{border}\n"
            "Source  : theory2.tex, Chapter 33, §33.2 – §33.4\n"
            "\n"
            "Formal Statement\n"
            "----------------\n"
            "  Γ ⊢ A     Γ, A ⊢ B\n"
            "  ─────────────────────  [cut]\n"
            "  Γ ⊢ B\n"
            "\n"
            "Intuition\n"
            "---------\n"
            "  The cut rule is the sequent-calculus form of modus ponens\n"
            "  (transitivity of entailment).  If we can prove A, and from\n"
            "  A we can prove B, then we can prove B directly.\n"
            "\n"
            "Admissibility — Hauptsatz (Gentzen 1935)\n"
            "----------------------------------------\n"
            "  Any proof using cut can be transformed into a cut-free\n"
            "  proof via a double induction on the complexity of the cut\n"
            "  formula and the heights of the sub-derivations.  This is\n"
            "  the central metatheorem of structural proof theory.\n"
            "\n"
            "Consequences\n"
            "------------\n"
            "  • Subformula property: every formula in a cut-free proof\n"
            "    is a subformula of the end-sequent.\n"
            "  • Consistency: there is no cut-free proof of ⊥.\n"
            "  • Decidability (for propositional logic): proof search in\n"
            "    cut-free systems terminates.\n"
            "\n"
            f"Current cut formula : {self.cut_formula or '(unset)'}\n"
            f"Elimination enabled : {self.enable_elimination}\n"
            f"Metadata            : {self.metadata}\n"
        )


# ---------------------------------------------------------------------------
# StructuralRuleSystem
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class StructuralRuleSystem:
    """Aggregates the four classical structural rules into one system.

    Provides convenience wrappers around each rule and a :meth:`normalize`
    method that applies contraction and exchange to put any judgment into
    a canonical (sorted, deduplicated) form.

    Attributes
    ----------
    system_id:
        Unique identifier for this rule system instance.
    weakening:
        The :class:`WeakeningRule` component.
    contraction:
        The :class:`ContractionRule` component.
    exchange:
        The :class:`ExchangeRule` component.
    cut:
        The :class:`CutRule` component.
    enabled_rules:
        Set of rule names that are currently active.  Attempting to apply
        a disabled rule raises :class:`RuntimeError`.
    """

    system_id: str
    weakening: WeakeningRule
    contraction: ContractionRule
    exchange: ExchangeRule
    cut: CutRule
    enabled_rules: set[str] = field(
        default_factory=lambda: {"weakening", "contraction", "exchange", "cut"}
    )

    # ------------------------------------------------------------------
    # all_rules
    # ------------------------------------------------------------------

    def all_rules(self) -> list[DeductionRule]:
        """Return a :class:`DeductionRule` for each currently enabled rule.

        Returns
        -------
        list[DeductionRule]
            One entry per enabled structural rule.
        """
        mapping: dict[str, "WeakeningRule | ContractionRule | ExchangeRule | CutRule"] = {
            "weakening": self.weakening,
            "contraction": self.contraction,
            "exchange": self.exchange,
            "cut": self.cut,
        }
        return [
            rule_obj.to_deduction_rule()
            for name, rule_obj in mapping.items()
            if name in self.enabled_rules
        ]

    # ------------------------------------------------------------------
    # apply_weakening
    # ------------------------------------------------------------------

    def apply_weakening(self, judgment: str, hypothesis: str) -> str:
        """Weaken *judgment* by adding *hypothesis*.

        Raises
        ------
        RuntimeError
            If weakening is disabled in :attr:`enabled_rules`.
        """
        if "weakening" not in self.enabled_rules:
            raise RuntimeError(
                f"Weakening is disabled in system {self.system_id!r}."
            )
        return self.weakening.apply(judgment, hypothesis)

    # ------------------------------------------------------------------
    # apply_contraction
    # ------------------------------------------------------------------

    def apply_contraction(self, judgment: str) -> str:
        """Contract the first duplicate hypothesis in *judgment*.

        Raises
        ------
        RuntimeError
            If contraction is disabled.
        """
        if "contraction" not in self.enabled_rules:
            raise RuntimeError(
                f"Contraction is disabled in system {self.system_id!r}."
            )
        return self.contraction.apply(judgment)

    # ------------------------------------------------------------------
    # apply_exchange
    # ------------------------------------------------------------------

    def apply_exchange(
        self, context: list[str], i: int, j: int
    ) -> list[str]:
        """Swap positions *i* and *j* in *context*.

        Raises
        ------
        RuntimeError
            If exchange is disabled.
        """
        if "exchange" not in self.enabled_rules:
            raise RuntimeError(
                f"Exchange is disabled in system {self.system_id!r}."
            )
        return self.exchange.apply(context, i, j)

    # ------------------------------------------------------------------
    # apply_cut
    # ------------------------------------------------------------------

    def apply_cut(self, left: str, right: str, formula: str) -> str | None:
        """Apply the cut rule to *left* and *right* on *formula*.

        Raises
        ------
        RuntimeError
            If cut is disabled.
        """
        if "cut" not in self.enabled_rules:
            raise RuntimeError(
                f"Cut is disabled in system {self.system_id!r}."
            )
        return self.cut.apply(left, right, formula)

    # ------------------------------------------------------------------
    # normalize
    # ------------------------------------------------------------------

    def normalize(
        self, judgment: str, context: list[str]
    ) -> tuple[str, list[str]]:
        """Normalize *judgment* and *context* via contraction then exchange.

        Applies:
        1. :meth:`ContractionRule.normalize_context` — deduplication.
        2. :meth:`ExchangeRule.sort_context` — canonical ordering.

        Parameters
        ----------
        judgment:
            The original judgment string (used to extract the conclusion).
        context:
            The hypothesis list to normalize (may differ from the context
            embedded in *judgment*).

        Returns
        -------
        tuple[str, list[str]]
            ``(normalized_judgment, sorted_deduplicated_context)``
        """
        _, conclusion = _parse_judgment(judgment)

        # Step 1: deduplicate
        if "contraction" in self.enabled_rules:
            deduped = self.contraction.normalize_context(context)
        else:
            deduped = list(context)

        # Step 2: sort
        if "exchange" in self.enabled_rules:
            sorted_ctx, _ = self.exchange.sort_context(deduped)
        else:
            sorted_ctx = deduped

        return _format_judgment(sorted_ctx, conclusion), sorted_ctx

    # ------------------------------------------------------------------
    # verify_cut_elimination
    # ------------------------------------------------------------------

    def verify_cut_elimination(self, proof_steps: list[str]) -> bool:
        """Return ``True`` when none of *proof_steps* references a cut.

        Scans for common cut markers case-insensitively.

        Parameters
        ----------
        proof_steps:
            Ordered list of string-encoded proof steps or annotations.

        Returns
        -------
        bool
            ``True`` iff the proof is cut-free.
        """
        for step in proof_steps:
            step_lower = step.lower()
            for marker in _CUT_MARKERS:
                if marker in step_lower:
                    return False
        return True

    # ------------------------------------------------------------------
    # admissibility_report
    # ------------------------------------------------------------------

    def admissibility_report(self) -> dict[str, Any]:
        """Return a comprehensive admissibility report for all four rules.

        Returns
        -------
        dict[str, Any]
            Certificates for each rule plus a prose summary.
        """
        return {
            "system_id": self.system_id,
            "enabled_rules": sorted(self.enabled_rules),
            "weakening": self.weakening.admissibility_certificate(),
            "contraction": self.contraction.admissibility_certificate(),
            "exchange": self.exchange.admissibility_certificate(),
            "cut": self.cut.admissibility_certificate(),
            "summary": (
                "All four structural rules are admissible in this system.  "
                "Weakening, contraction, and exchange are provable by "
                "straightforward induction on proof length.  Cut-elimination "
                "(the Hauptsatz) requires a more involved double induction "
                "on cut grade and derivation height."
            ),
        }

    # ------------------------------------------------------------------
    # enable / disable
    # ------------------------------------------------------------------

    def enable(self, rule_name: str) -> None:
        """Enable the named structural rule.

        Parameters
        ----------
        rule_name:
            One of ``"weakening"``, ``"contraction"``, ``"exchange"``,
            ``"cut"``.

        Raises
        ------
        ValueError
            If *rule_name* is not a recognized structural rule.
        """
        _VALID = {"weakening", "contraction", "exchange", "cut"}
        if rule_name not in _VALID:
            raise ValueError(
                f"Unknown structural rule {rule_name!r}.  "
                f"Valid names: {sorted(_VALID)}"
            )
        self.enabled_rules.add(rule_name)
        logger.debug("StructuralRuleSystem[%s]: enabled %r", self.system_id, rule_name)

    def disable(self, rule_name: str) -> None:
        """Disable the named structural rule.

        Parameters
        ----------
        rule_name:
            The rule to disable.

        Raises
        ------
        ValueError
            If *rule_name* is not recognized.
        """
        _VALID = {"weakening", "contraction", "exchange", "cut"}
        if rule_name not in _VALID:
            raise ValueError(
                f"Unknown structural rule {rule_name!r}.  "
                f"Valid names: {sorted(_VALID)}"
            )
        self.enabled_rules.discard(rule_name)
        logger.debug("StructuralRuleSystem[%s]: disabled %r", self.system_id, rule_name)

    # ------------------------------------------------------------------
    # copilot_analyze
    # ------------------------------------------------------------------

    def copilot_analyze(self, proof_steps: list[str]) -> str:
        """Produce a structured analysis of structural rule usage in *proof_steps*.

        # copilot analyze structural rule usage and produce a diagnostic summary.

        Parameters
        ----------
        proof_steps:
            A list of string-encoded proof steps.

        Returns
        -------
        str
            A multi-line summary report.
        """
        usage: dict[str, int] = {
            "weakening": 0,
            "contraction": 0,
            "exchange": 0,
            "cut": 0,
            "other": 0,
        }
        for step in proof_steps:
            sl = step.lower()
            matched = False
            if any(kw in sl for kw in ("weak", "[w]", "weaken")):
                usage["weakening"] += 1
                matched = True
            if any(kw in sl for kw in ("contr", "[c]", "contract")):
                usage["contraction"] += 1
                matched = True
            if any(kw in sl for kw in ("exch", "swap", "[e]", "exchange")):
                usage["exchange"] += 1
                matched = True
            if any(kw in sl for kw in ("cut", "[cut]")):
                usage["cut"] += 1
                matched = True
            if not matched:
                usage["other"] += 1

        total_structural = sum(v for k, v in usage.items() if k != "other")
        cut_free = self.verify_cut_elimination(proof_steps)

        lines = [
            f"=== Structural Rule Analysis [{self.system_id}] ===",
            f"Total proof steps      : {len(proof_steps)}",
            f"Structural steps       : {total_structural}",
            f"  weakening            : {usage['weakening']}",
            f"  contraction          : {usage['contraction']}",
            f"  exchange             : {usage['exchange']}",
            f"  cut                  : {usage['cut']}",
            f"Non-structural steps   : {usage['other']}",
            f"Cut-free               : {cut_free}",
        ]

        if not cut_free:
            lines.append(
                "⚠  WARNING: Proof contains cuts.  "
                "Run CutRule.eliminate_cut to obtain a cut-free proof."
            )
        if usage["weakening"] > 5:
            lines.append(
                "ℹ  INFO: High weakening count may indicate unnecessary "
                "hypotheses in the context."
            )
        if usage["contraction"] > 0:
            lines.append(
                "ℹ  INFO: Contraction steps present.  "
                "Consider calling normalize() to pre-deduplicate."
            )

        return "\n".join(lines)


# ---------------------------------------------------------------------------
# PermutationLemma
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PermutationLemma:
    """A named lemma recording a specific permutation of hypotheses.

    Permutation lemmas track the sequence of :class:`ExchangeRule`
    applications needed to reorder a context.  They are invertible and
    composable, forming a group under composition.

    Attributes
    ----------
    lemma_id:
        Unique identifier for this lemma.
    description:
        Human-readable description.
    premise_ordering:
        The original (source) ordering of hypotheses.
    conclusion_ordering:
        The target ordering after the permutation.
    permutation:
        A tuple ``perm`` of length ``n`` where ``perm[i]`` is the index in
        *premise_ordering* that should appear at position *i* in
        *conclusion_ordering*.
    """

    lemma_id: str
    description: str
    premise_ordering: tuple[str, ...]
    conclusion_ordering: tuple[str, ...]
    permutation: tuple[int, ...]

    # ------------------------------------------------------------------
    # is_valid_permutation
    # ------------------------------------------------------------------

    def is_valid_permutation(self) -> bool:
        """Return ``True`` iff :attr:`permutation` is a valid bijection.

        Checks:

        1. Length matches ``len(premise_ordering)``.
        2. Values are exactly ``{0, …, n-1}`` (bijection).
        3. Applying the permutation to *premise_ordering* yields
           *conclusion_ordering*.

        Returns
        -------
        bool
        """
        n = len(self.premise_ordering)
        if len(self.permutation) != n or len(self.conclusion_ordering) != n:
            return False
        if sorted(self.permutation) != list(range(n)):
            return False
        applied = tuple(self.premise_ordering[self.permutation[i]] for i in range(n))
        return applied == self.conclusion_ordering

    # ------------------------------------------------------------------
    # apply
    # ------------------------------------------------------------------

    def apply(self, items: Sequence[Any]) -> list[Any]:
        """Apply this permutation to any sequence of the same length.

        Parameters
        ----------
        items:
            A sequence whose length must equal ``len(self.permutation)``.

        Returns
        -------
        list[Any]
            Permuted list.

        Raises
        ------
        ValueError
            If ``len(items) != len(self.permutation)``.
        """
        n = len(self.permutation)
        if len(items) != n:
            raise ValueError(
                f"PermutationLemma.apply: sequence length {len(items)} "
                f"≠ permutation length {n}"
            )
        return [items[self.permutation[i]] for i in range(n)]

    # ------------------------------------------------------------------
    # inverse
    # ------------------------------------------------------------------

    def inverse(self) -> "PermutationLemma":
        """Return the inverse permutation lemma.

        The inverse ``inv`` satisfies: ``inv.permutation[self.permutation[i]] == i``
        for all *i*.

        Returns
        -------
        PermutationLemma
            A new lemma with source/target swapped and the inverse permutation.
        """
        n = len(self.permutation)
        inv_perm: list[int] = [0] * n
        for fwd_src, fwd_dst in enumerate(self.permutation):
            inv_perm[fwd_dst] = fwd_src
        return PermutationLemma(
            lemma_id=f"{self.lemma_id}⁻¹",
            description=f"Inverse of '{self.lemma_id}': {self.description}",
            premise_ordering=self.conclusion_ordering,
            conclusion_ordering=self.premise_ordering,
            permutation=tuple(inv_perm),
        )

    # ------------------------------------------------------------------
    # compose
    # ------------------------------------------------------------------

    def compose(self, other: "PermutationLemma") -> "PermutationLemma":
        """Compose *self* with *other* (apply *self* first, then *other*).

        The composed permutation maps index *i* to position *j* in the
        final result, where *j* = ``other.permutation[self.permutation[i]]``.

        Parameters
        ----------
        other:
            The permutation to apply after *self*.

        Returns
        -------
        PermutationLemma
            The composed lemma.

        Raises
        ------
        ValueError
            If the conclusion ordering of *self* does not match the premise
            ordering of *other*.
        """
        if self.conclusion_ordering != other.premise_ordering:
            raise ValueError(
                f"Cannot compose: self.conclusion_ordering "
                f"{self.conclusion_ordering!r} ≠ "
                f"other.premise_ordering {other.premise_ordering!r}"
            )
        n = len(self.permutation)
        # composed[i] = source index in self.premise_ordering that ends at i
        # after applying self then other.
        # other maps self-output position → final position.
        # inv_other[i] = which self-output position ends up at position i
        inv_other: list[int] = [0] * n
        for src, dst in enumerate(other.permutation):
            inv_other[dst] = src

        composed = tuple(self.permutation[inv_other[i]] for i in range(n))

        return PermutationLemma(
            lemma_id=f"({self.lemma_id} ∘ {other.lemma_id})",
            description=(
                f"Composition: first '{self.lemma_id}', "
                f"then '{other.lemma_id}'"
            ),
            premise_ordering=self.premise_ordering,
            conclusion_ordering=other.conclusion_ordering,
            permutation=composed,
        )

    # ------------------------------------------------------------------
    # to_cycle_notation
    # ------------------------------------------------------------------

    def to_cycle_notation(self) -> str:
        """Return the permutation in standard disjoint-cycle notation.

        Fixed points (``perm[i] == i``) are omitted.

        Returns
        -------
        str
            E.g. ``"(0 2 1)"`` for the permutation ``[0, 2, 1]``.
            ``"(identity)"`` for the identity permutation.

        Examples
        --------
        >>> p = PermutationLemma("p", "", ("A","B","C"), ("B","C","A"), (2,0,1))
        >>> p.to_cycle_notation()
        '(0 2 1)'
        """
        n = len(self.permutation)
        visited = [False] * n
        cycles: list[list[int]] = []
        for start in range(n):
            if visited[start]:
                continue
            if self.permutation[start] == start:
                visited[start] = True
                continue
            cycle: list[int] = []
            cur = start
            while not visited[cur]:
                visited[cur] = True
                cycle.append(cur)
                cur = self.permutation[cur]
            if len(cycle) > 1:
                cycles.append(cycle)
        if not cycles:
            return "(identity)"
        return " ".join(
            f"({' '.join(str(x) for x in c)})" for c in cycles
        )

    # ------------------------------------------------------------------
    # verify_against_exchange
    # ------------------------------------------------------------------

    def verify_against_exchange(self, exchange: ExchangeRule) -> bool:
        """Check that this permutation is realizable by *exchange*.

        If *exchange* allows arbitrary permutations any valid permutation
        is realizable.  If only adjacent swaps are permitted we verify
        that a bubble-sort decomposition exists.

        Parameters
        ----------
        exchange:
            The :class:`ExchangeRule` to verify against.

        Returns
        -------
        bool
            ``True`` iff this lemma is realizable by *exchange*.
        """
        if not self.is_valid_permutation():
            return False
        if exchange.allow_arbitrary_permutation:
            return True
        # For adjacent-only exchange: verify via permutation_sequence
        source = list(self.premise_ordering)
        target = list(self.conclusion_ordering)
        swaps = exchange.permutation_sequence(source, target)
        return swaps is not None

    # ------------------------------------------------------------------
    # explain
    # ------------------------------------------------------------------

    def explain(self) -> str:
        """Return a multi-line human-readable explanation of this lemma."""
        border = "=" * 52
        return (
            f"PermutationLemma  [{self.lemma_id}]\n"
            f"{border}\n"
            f"Description     : {self.description}\n"
            "\n"
            "Permutation\n"
            "-----------\n"
            f"  Source  : {list(self.premise_ordering)}\n"
            f"  Target  : {list(self.conclusion_ordering)}\n"
            f"  Perm    : {list(self.permutation)}\n"
            f"  Cycles  : {self.to_cycle_notation()}\n"
            "\n"
            f"Valid?          : {self.is_valid_permutation()}\n"
        )


# ---------------------------------------------------------------------------
# Module public API
# ---------------------------------------------------------------------------

__all__ = [
    "RuleSchema",
    "WeakeningRule",
    "ContractionRule",
    "ExchangeRule",
    "CutRule",
    "StructuralRuleSystem",
    "PermutationLemma",
]
