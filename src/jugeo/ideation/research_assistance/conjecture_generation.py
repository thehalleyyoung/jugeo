r"""Conjecture generation for JuGeo research assistance — Chapter 51, §3.

The conjecture generator proposes new mathematical conjectures by generalising
known theorems, instantiating structural templates, and scoring plausibility
against evidence gathered from the current research context.

Mathematical framing
--------------------

Let :math:`\mathcal{T}` be a template library of size :math:`|\mathcal{T}|`.
Each template :math:`\tau \in \mathcal{T}` can be instantiated against a
context :math:`\text{ctx}` to produce a conjecture statement.  The expected
coverage of :math:`n` independently generated conjectures satisfies (Thm 51.4):

.. math::

    C(n) \;\geq\; 1 - \exp\!\left(-\frac{n}{|\mathcal{T}|}\right)

Plausibility is computed as the token-overlap score between the conjecture
statement and the joined evidence corpus, normalized to :math:`[0.1, 1]`
(so that even unsupported conjectures have a non-zero prior):

.. math::

    \text{plaus}(c, E) =
        \max\!\left(0.1,\;
            \frac{|\text{tok}(c) \cap \text{tok}(E)|}
                 {|\text{tok}(c) \cup \text{tok}(E)|}
        \right)
"""

from __future__ import annotations

import logging
import re
import time
import uuid
from dataclasses import field

from jugeo.ideation.research_assistance.models import (
    ConjectureRecord,
    ConjectureStatus,
    ResearchContext,
)

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    """Clamp a float to [lo, hi]."""
    return max(lo, min(hi, float(value)))


def _tokenize(text: str) -> set[str]:
    """Return lowercase alphanumeric tokens of length >= 2."""
    return {t.lower() for t in re.split(r"[^a-zA-Z0-9]+", text) if len(t) >= 2}


def _normalize(text: str) -> str:
    """Lowercase and strip whitespace."""
    return text.lower().strip()


def _jaccard(a: set[str], b: set[str]) -> float:
    """Compute Jaccard coefficient; returns 0.0 if both sets are empty."""
    if not a and not b:
        return 0.0
    union = a | b
    return len(a & b) / len(union) if union else 0.0


# ---------------------------------------------------------------------------
# Default template library
# ---------------------------------------------------------------------------

_DEFAULT_TEMPLATES: tuple[str, ...] = (
    "For all X in Y, P(X) holds",
    "If A then B under constraint C",
    "The composition of X and Y has property Z",
    "Every morphism in X preserves Y",
    "For bounded X, the limit of Y exists",
    "The functor F preserves Z",
    "Under hypothesis H, the unique solution is X",
    "The sequence X_n converges to Y",
    "There exists a unique X satisfying Y",
    "The map from X to Y is injective",
    "The product of X and Y is commutative",
    "Every subobject of X inherits property Y",
)


# ---------------------------------------------------------------------------
# PatternAnalyzer
# ---------------------------------------------------------------------------


class PatternAnalyzer:
    """Extracts and generalises structural patterns from theorem statements.

    Patterns are created by replacing proper-noun-like tokens (capitalized or
    numeric) with the placeholder ``X``.  Analogues are statements from a pool
    that share a sufficient token overlap with the target.
    """

    _VAR_PATTERN: re.Pattern = re.compile(r"\b[A-Z][a-zA-Z]*\b")
    _NUM_PATTERN: re.Pattern = re.compile(r"\b\d+\b")

    def analyze(self, statements: list[str]) -> list[str]:
        """Return a list of unique generalized patterns from the statements."""
        patterns: list[str] = []
        seen: set[str] = set()
        for stmt in statements:
            gen = self.generalize(stmt)
            if gen not in seen:
                seen.add(gen)
                patterns.append(gen)
        return patterns

    def generalize(self, statement: str) -> str:
        """Replace capitalized identifiers with X and numbers with N."""
        result = self._VAR_PATTERN.sub("X", statement)
        result = self._NUM_PATTERN.sub("N", result)
        return result

    def analogues(self, statement: str, pool: list[str]) -> list[str]:
        """Return items from pool with >= 30% token overlap with statement."""
        target_tokens = _tokenize(statement)
        results: list[str] = []
        for candidate in pool:
            overlap = _jaccard(target_tokens, _tokenize(candidate))
            if overlap >= 0.30:
                results.append(candidate)
        return results

    def most_general_form(self, statements: list[str]) -> str:
        """Return the most heavily abstracted pattern across all statements."""
        if not statements:
            return ""
        patterns = self.analyze(statements)
        if not patterns:
            return ""
        return min(patterns, key=len)


# ---------------------------------------------------------------------------
# ConjectureEvaluator
# ---------------------------------------------------------------------------


class ConjectureEvaluator:
    """Evaluates the plausibility and novelty of :class:`ConjectureRecord` instances.

    Plausibility is measured by token overlap with supporting evidence.
    Novelty is measured by the maximum inverse overlap against an existing
    conjecture pool — a conjecture that is very similar to known conjectures
    scores low on novelty.
    """

    def evaluate(self, conjecture: ConjectureRecord) -> float:
        """Return an overall quality score in [0, 1] for the conjecture."""
        plaus = self.plausibility(
            conjecture.statement, conjecture.supporting_evidence
        )
        penalty = 0.1 * len(conjecture.falsification_attempts)
        return _clamp(plaus - penalty)

    def plausibility(self, statement: str, evidence: list[str]) -> float:
        """Return the plausibility of a statement given an evidence list.

        Returns 0.5 if no evidence is available.  Otherwise returns the
        Jaccard overlap of the statement tokens against the joined evidence,
        clamped to [0.1, 1.0].
        """
        if not evidence:
            return 0.5
        evidence_text = " ".join(evidence)
        overlap = _jaccard(_tokenize(statement), _tokenize(evidence_text))
        return _clamp(max(0.1, overlap))

    def novelty(self, statement: str, existing: list[str]) -> float:
        """Return novelty score in [0, 1]: 1.0 means completely novel."""
        if not existing:
            return 1.0
        stmt_tokens = _tokenize(statement)
        max_overlap = max(
            _jaccard(stmt_tokens, _tokenize(ex)) for ex in existing
        )
        return _clamp(1.0 - max_overlap)

    def combined_score(
        self,
        conjecture: ConjectureRecord,
        existing_statements: list[str],
    ) -> float:
        """Return a combined plausibility-novelty score."""
        plaus = self.plausibility(
            conjecture.statement, conjecture.supporting_evidence
        )
        nov = self.novelty(conjecture.statement, existing_statements)
        return _clamp(0.6 * plaus + 0.4 * nov)


# ---------------------------------------------------------------------------
# ConjecturePruner
# ---------------------------------------------------------------------------


class ConjecturePruner:
    """Prunes dominated conjectures from a pool, keeping the best max_keep.

    A conjecture :math:`a` is *dominated by* :math:`b` if :math:`b` has
    strictly higher confidence and at least as much supporting evidence.
    Falsified conjectures are always removed before pruning.
    """

    def prune(
        self,
        conjectures: list[ConjectureRecord],
        max_keep: int,
    ) -> list[ConjectureRecord]:
        """Return at most max_keep non-falsified conjectures by confidence desc."""
        active = [
            c for c in conjectures if c.status != ConjectureStatus.FALSIFIED
        ]
        active.sort(key=lambda c: c.confidence, reverse=True)
        return active[:max_keep]

    def dominated_by(
        self,
        a: ConjectureRecord,
        b: ConjectureRecord,
    ) -> bool:
        """Return True if b strictly dominates a in confidence and evidence."""
        return (
            b.confidence > a.confidence
            and len(b.supporting_evidence) >= len(a.supporting_evidence)
        )

    def remove_dominated(
        self,
        conjectures: list[ConjectureRecord],
    ) -> list[ConjectureRecord]:
        """Remove any conjecture that is dominated by at least one other."""
        result: list[ConjectureRecord] = []
        for a in conjectures:
            if not any(self.dominated_by(a, b) for b in conjectures if b is not a):
                result.append(a)
        return result


# ---------------------------------------------------------------------------
# GenerationHistory
# ---------------------------------------------------------------------------


class GenerationHistory:
    """Time-stamped record of all generated conjectures within a session.

    The history supports querying by time window to retrieve recently
    generated conjectures for audit or display purposes.
    """

    def __init__(self) -> None:
        self._records: list[ConjectureRecord] = []
        self._timestamps: list[float] = []

    def record(self, conjecture: ConjectureRecord) -> None:
        """Add a conjecture to the history with the current timestamp."""
        self._records.append(conjecture)
        self._timestamps.append(time.time())
        _log.debug(
            "GenerationHistory: recorded conjecture %s",
            conjecture.conjecture_id,
        )

    def all(self) -> list[ConjectureRecord]:
        """Return all recorded conjectures in generation order."""
        return list(self._records)

    def since(self, timestamp: float) -> list[ConjectureRecord]:
        """Return conjectures recorded at or after the given Unix timestamp."""
        return [
            c
            for c, ts in zip(self._records, self._timestamps)
            if ts >= timestamp
        ]

    def count(self) -> int:
        """Return the total number of recorded conjectures."""
        return len(self._records)

    def last_n(self, n: int) -> list[ConjectureRecord]:
        """Return the n most recently recorded conjectures."""
        return list(self._records[-n:])


# ---------------------------------------------------------------------------
# ConjectureGenerator
# ---------------------------------------------------------------------------


class ConjectureGenerator:
    """Main conjecture generation engine.

    The generator instantiates structural templates against the current
    research context, scores each candidate for plausibility, prunes the
    result set, and records all generated conjectures in its history.

    Attributes:
        _evaluator: Evaluates plausibility and novelty.
        _pruner: Prunes dominated or low-confidence conjectures.
        _history: Records all generated conjectures.
        _templates: The template library as a tuple of strings.
    """

    def __init__(
        self,
        evaluator: ConjectureEvaluator | None = None,
        pruner: ConjecturePruner | None = None,
    ) -> None:
        self._evaluator = evaluator or ConjectureEvaluator()
        self._pruner = pruner or ConjecturePruner()
        self._history = GenerationHistory()
        self._templates: tuple[str, ...] = _DEFAULT_TEMPLATES

    def generate(
        self,
        context: ResearchContext,
        n: int = 5,
    ) -> list[ConjectureRecord]:
        """Generate up to n conjectures from the context and template library.

        Each template is instantiated, scored, and a ConjectureRecord is
        created.  The pool is pruned to n before returning and each conjecture
        is recorded in the history (Thm 51.4).
        """
        templates = self._template_conjectures(context)
        candidates: list[ConjectureRecord] = []
        for tmpl in templates:
            stmt = self._instantiate(tmpl, context)
            if not stmt.strip():
                continue
            conf = self._evaluator.plausibility(stmt, [context.purpose])
            record = ConjectureRecord(
                conjecture_id=str(uuid.uuid4()),
                statement=stmt,
                confidence=conf,
            )
            if context.purpose:
                record.add_evidence(f"Derived from purpose: {context.purpose[:60]}")
            candidates.append(record)

        pruned = self._pruner.prune(candidates, max_keep=n)
        for c in pruned:
            self._history.record(c)

        _log.debug(
            "ConjectureGenerator: generated %d conjectures from %d templates",
            len(pruned),
            len(templates),
        )
        return pruned

    def generate_from_pattern(
        self,
        pattern: str,
        context: ResearchContext,
    ) -> list[ConjectureRecord]:
        """Instantiate a single pattern string against the context.

        Returns a list containing exactly one :class:`ConjectureRecord`.
        """
        stmt = self._instantiate(pattern, context)
        conf = self._evaluator.plausibility(stmt, [context.purpose])
        record = ConjectureRecord(
            conjecture_id=str(uuid.uuid4()),
            statement=stmt,
            confidence=conf,
        )
        self._history.record(record)
        return [record]

    def score_plausibility(self, conjecture: ConjectureRecord) -> float:
        """Delegate plausibility evaluation to the internal evaluator."""
        return self._evaluator.evaluate(conjecture)

    def history(self) -> GenerationHistory:
        """Return the generation history for this generator instance."""
        return self._history

    def _template_conjectures(self, context: ResearchContext) -> list[str]:
        """Return the list of template strings to instantiate."""
        return list(self._templates)

    def _instantiate(self, template: str, context: ResearchContext) -> str:
        """Replace placeholder tokens in a template with context content.

        Extracts the first significant (length >= 3) tokens from the current
        theorem and purpose, then substitutes X, Y, Z, A, B, C, F, H
        in order.
        """
        theorem_tokens = [t for t in _tokenize(context.current_theorem) if len(t) >= 3]
        purpose_tokens = [t for t in _tokenize(context.purpose) if len(t) >= 3]
        combined = theorem_tokens + purpose_tokens

        if not combined:
            return template

        placeholders = ("X", "Y", "Z", "A", "B", "C", "F", "H", "P", "Q")
        result = template
        for i, placeholder in enumerate(placeholders):
            if placeholder not in result:
                continue
            token = combined[i % len(combined)]
            result = re.sub(r"\b" + re.escape(placeholder) + r"\b", token, result)

        return result

    def add_template(self, template: str) -> None:
        """Register an additional template for future generation calls."""
        if template not in self._templates:
            self._templates = self._templates + (template,)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    "ConjectureEvaluator",
    "ConjectureGenerator",
    "ConjecturePruner",
    "GenerationHistory",
    "PatternAnalyzer",
]
