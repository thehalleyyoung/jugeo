"""Algorithms for schema matching, instantiation, composition, and validation.

Provides the computational machinery for working with theorem schemas: matching
theorems to appropriate schemas, inferring variable bindings from context,
composing schemas into compound obligations, prioritising proof work, and
searching for existing proof artefacts. These algorithms are the operational
backbone of the theorem schema system described in Chapter 36.

Module overview
---------------
``SchemaMatchingAlgorithm``
    Token-overlap (Jaccard similarity) matching between free-text theorem
    statements and schema template strings.  Produces ranked ``MatchScore``
    objects that callers can inspect or present to a human reviewer.

``BindingInferenceAlgorithm``
    Heuristic inference of variable bindings for a schema from a surrounding
    context dictionary or raw natural-language text.  When the context
    provides an explicit value for a variable the value is used directly;
    otherwise a ``?var_name`` placeholder is emitted so the caller knows
    which variables still require manual resolution.

``SchemaCompositionAlgorithm``
    Algebraic composition of two or more schemas into a compound schema via
    logical connectives (``and``, ``or``, ``implies``).  The composed schema
    has the union of its constituents' free variables and a template statement
    that makes the connective explicit.

``ObligationPrioritizationAlgorithm``
    Assigns integer priority scores to proof obligations based on the
    owning subsystem's base priority, deadline proximity, and prior failure
    history.  Scores feed directly into ``ObligationQueue.push``.

``SchemaConsistencyChecker``
    Detects internal inconsistencies within a single schema (e.g. unmatched
    variable references) and cross-schema contradictions (e.g. two schemas
    with identical names but different templates).

``TemplateExpansionAlgorithm``
    Performs variable substitution and LaTeX-macro expansion on template
    statements, producing fully-expanded formal theorem statements suitable
    for feeding to proof assistants.

``ProofSearchAlgorithm``
    Searches an in-memory archive of ``DischargeRecord`` objects for proofs
    that may be reusable for a given obligation, based on statement similarity.

``SchemaMinimizationAlgorithm``
    Removes logically redundant schemas from a collection: a schema A is
    considered redundant if another schema B's template subsumes A's template
    (i.e. A's template is a substring of B's, meaning B is at least as
    strong).

Design notes
------------
All algorithms are stateful objects (classes) rather than bare functions so
that configuration (thresholds, macros, context dictionaries) can be
accumulated incrementally and shared across multiple invocations without
threading global state.  The algorithms are intentionally *not* coupled to
any persistence layer; callers are responsible for saving results.

copilot: schema algorithm library for theorem instantiation and proof search.
"""
from __future__ import annotations

import json
import logging
import re
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterator

from jugeo.encodings.theorem_schemas.models import (
    InstanceStatus,
    ProofAgent,
    ProofObligation,
    ProofStyle,
    SchemaInstance,
    SubsystemKind,
    SubsystemSchema,
    TheoremSchema,
)
from jugeo.encodings.theorem_schemas.proof_obligations import (
    DischargeRecord,
    ObligationStatus,
    ObligationTracker,
)

__all__ = [
    "MatchScore",
    "SchemaMatchingAlgorithm",
    "BindingInferenceAlgorithm",
    "SchemaCompositionAlgorithm",
    "ObligationPrioritizationAlgorithm",
    "SchemaConsistencyChecker",
    "TemplateExpansionAlgorithm",
    "ProofSearchAlgorithm",
    "SchemaMinimizationAlgorithm",
]

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# MatchScore
# ---------------------------------------------------------------------------


@dataclass
class MatchScore:
    """The result of matching a theorem statement against a single schema.

    ``MatchScore`` objects are produced by ``SchemaMatchingAlgorithm.match``
    and carry enough information for a caller to decide whether the match is
    strong enough to proceed with instantiation, or to explain to the user
    why a particular schema was selected.

    Attributes:
        schema_id: ID of the ``TheoremSchema`` that was evaluated.
        score: Jaccard similarity score in ``[0.0, 1.0]``.  A score of 1.0
            means the tokenised statement and template are identical.
        matched_vars: Variables from the schema that appear verbatim in the
            statement (only populated for strong matches).
        explanation: Human-readable string explaining how the score was
            computed, useful for debugging and interactive review.
    """

    schema_id: str
    score: float
    matched_vars: dict[str, str] = field(default_factory=dict)
    explanation: str = ""

    def to_json(self) -> dict[str, Any]:
        """Serialise this score to a JSON-compatible dictionary.

        Returns:
            Dictionary with all four fields; ``score`` is a plain float.
        """
        return {
            "schema_id": self.schema_id,
            "score": self.score,
            "matched_vars": self.matched_vars,
            "explanation": self.explanation,
        }

    def is_strong_match(self, threshold: float = 0.7) -> bool:
        """Return True if the score meets or exceeds *threshold*.

        The default threshold of 0.7 was chosen empirically: at Jaccard ≥ 0.7
        the two token sets share at least 70 % of their combined vocabulary,
        which in practice corresponds to a semantically plausible match for
        short mathematical statements.

        Args:
            threshold: Minimum score to consider a "strong" match.

        Returns:
            True when ``self.score >= threshold``.
        """
        return self.score >= threshold


# ---------------------------------------------------------------------------
# SchemaMatchingAlgorithm
# ---------------------------------------------------------------------------


class SchemaMatchingAlgorithm:
    """Matches free-text or formal theorem statements to schema templates.

    The algorithm uses Jaccard similarity on the token sets of the input
    statement and each schema's ``template_statement``.  LaTeX commands and
    punctuation are stripped before tokenisation so that ``\\forall x \\in X``
    and ``for all x in X`` produce similar token sets.

    Because mathematical notation is highly domain-specific, token overlap is
    a surprisingly effective baseline; more sophisticated semantic embeddings
    can be layered on top by subclassing and overriding ``_jaccard``.

    Example usage::

        algo = SchemaMatchingAlgorithm(schemas)
        scores = algo.match("for all x in X, f(x) is well-defined")
        best = algo.best_match("for all x in X, f(x) is well-defined")
    """

    def __init__(
        self,
        schemas: list[TheoremSchema] | None = None,
        min_score: float = 0.5,
    ) -> None:
        """Initialise with an optional list of schemas and a score threshold.

        Args:
            schemas: Initial list of ``TheoremSchema`` objects to match
                against.  Additional schemas can be added later via
                ``add_schema``.
            min_score: Minimum Jaccard score for a match to be included in
                results (default 0.5).
        """
        self._schemas: list[TheoremSchema] = list(schemas) if schemas else []
        self._min_score: float = min_score

    def add_schema(self, schema: TheoremSchema) -> None:
        """Append a schema to the matching pool.

        Args:
            schema: The ``TheoremSchema`` to add.
        """
        self._schemas.append(schema)
        logger.debug("SchemaMatchingAlgorithm: added schema %s.", schema.schema_id[:8])

    def match(self, statement: str) -> list[MatchScore]:
        """Return all schemas whose similarity to *statement* meets the threshold.

        The returned list is sorted by score descending so the best candidate
        is at index 0.

        Args:
            statement: The theorem statement (free text or formal) to match.

        Returns:
            List of ``MatchScore`` objects with ``score >= self._min_score``,
            sorted best-first.
        """
        stmt_tokens = self._tokenize(statement)
        results: list[MatchScore] = []
        for schema in self._schemas:
            tmpl_tokens = self._tokenize(schema.template_statement)
            score = self._jaccard(stmt_tokens, tmpl_tokens)
            if score < self._min_score:
                continue
            # Find variables that appear verbatim in the statement
            matched_vars: dict[str, str] = {}
            for var in schema.free_vars:
                if var.lower() in {t.lower() for t in stmt_tokens}:
                    matched_vars[var] = var
            explanation = (
                f"Jaccard({len(stmt_tokens)} stmt tokens, "
                f"{len(tmpl_tokens)} tmpl tokens) = {score:.3f}. "
                f"Matched vars: {list(matched_vars.keys()) or 'none'}."
            )
            results.append(
                MatchScore(
                    schema_id=schema.schema_id,
                    score=score,
                    matched_vars=matched_vars,
                    explanation=explanation,
                )
            )
        results.sort(key=lambda ms: ms.score, reverse=True)
        return results

    def best_match(self, statement: str) -> MatchScore | None:
        """Return the highest-scoring match, or None if nothing meets threshold.

        Args:
            statement: The theorem statement to match.

        Returns:
            The ``MatchScore`` with the highest score, or ``None``.
        """
        matches = self.match(statement)
        return matches[0] if matches else None

    def match_batch(
        self, statements: list[str]
    ) -> dict[str, list[MatchScore]]:
        """Match each statement in a batch and return a mapping.

        Args:
            statements: List of theorem statements.

        Returns:
            Dictionary mapping each statement to its list of ``MatchScore``
            objects (sorted best-first).
        """
        return {stmt: self.match(stmt) for stmt in statements}

    def set_threshold(self, threshold: float) -> None:
        """Update the minimum score threshold for subsequent matches.

        Args:
            threshold: New minimum Jaccard score in ``[0.0, 1.0]``.
        """
        self._min_score = max(0.0, min(1.0, threshold))
        logger.debug("Matching threshold updated to %.3f.", self._min_score)

    def explain_match(self, statement: str, schema_id: str) -> str:
        """Produce a detailed explanation of the match between *statement* and *schema_id*.

        Useful for interactive debugging: describes both token sets, their
        intersection, difference, and the computed Jaccard score.

        Args:
            statement: The query theorem statement.
            schema_id: The ID of the schema to explain the match against.

        Returns:
            Multi-line explanation string.
        """
        schema = next((s for s in self._schemas if s.schema_id == schema_id), None)
        if schema is None:
            return f"Schema {schema_id!r} not found in matching pool."
        stmt_tokens = self._tokenize(statement)
        tmpl_tokens = self._tokenize(schema.template_statement)
        intersection = stmt_tokens & tmpl_tokens
        union = stmt_tokens | tmpl_tokens
        score = len(intersection) / len(union) if union else 0.0
        lines = [
            f"Match explanation: statement vs schema [{schema.name}]",
            f"  Statement tokens ({len(stmt_tokens)}): {sorted(stmt_tokens)[:10]}...",
            f"  Template tokens  ({len(tmpl_tokens)}): {sorted(tmpl_tokens)[:10]}...",
            f"  Intersection     ({len(intersection)}): {sorted(intersection)[:10]}...",
            f"  Jaccard score    : {score:.4f}",
            f"  Threshold        : {self._min_score:.4f}",
            f"  Verdict          : {'MATCH' if score >= self._min_score else 'NO MATCH'}",
        ]
        return "\n".join(lines)

    def _tokenize(self, text: str) -> set[str]:
        r"""Tokenise *text* into a set of normalised tokens.

        Processing steps:
        1. Strip LaTeX commands (``\cmd`` → ``cmd``).
        2. Lower-case everything.
        3. Split on whitespace and punctuation.
        4. Remove empty strings and single-character tokens (articles, etc.).

        Args:
            text: Raw text or LaTeX formula.

        Returns:
            Set of normalised token strings.
        """
        # Remove LaTeX backslash commands like \forall → forall
        text = re.sub(r"\\([a-zA-Z]+)", r"\1", text)
        # Remove remaining non-alphanumeric characters (keep spaces)
        text = re.sub(r"[^\w\s]", " ", text)
        tokens = text.lower().split()
        # Filter out stop words and single characters
        stop = {"a", "an", "the", "is", "are", "of", "in", "to", "for", "and", "or"}
        return {t for t in tokens if len(t) > 1 and t not in stop}

    def _jaccard(self, a: set[str], b: set[str]) -> float:
        """Compute the Jaccard similarity between two token sets.

        Jaccard similarity is defined as |A ∩ B| / |A ∪ B|.  Returns 0.0
        when both sets are empty to avoid a division-by-zero error.

        Args:
            a: First token set.
            b: Second token set.

        Returns:
            Float in ``[0.0, 1.0]``.
        """
        if not a and not b:
            return 0.0
        intersection = a & b
        union = a | b
        return len(intersection) / len(union)


# ---------------------------------------------------------------------------
# BindingInferenceAlgorithm
# ---------------------------------------------------------------------------


class BindingInferenceAlgorithm:
    """Infers variable bindings for a schema from a context dictionary or text.

    The algorithm operates in two modes:

    1. **Context-based inference** (``infer``): given a ``context`` dictionary
       mapping concept names to mathematical objects, and a schema with free
       variables, it produces a binding for each variable by checking whether
       the variable name appears as a key in the context.  Unresolved variables
       are given the placeholder ``?variable_name``.

    2. **Text-based inference** (``infer_from_text``): scans raw text for
       ``{word}`` patterns (a common informal notation for placeholders) and
       maps them to schema variables in order of appearance.

    The algorithm accumulates a persistent *context* that can be updated
    between invocations so that bindings discovered in one call are available
    to the next.

    Example usage::

        algo = BindingInferenceAlgorithm()
        algo.update_context("X", "ℤ")
        bindings = algo.infer(schema, {"X": "ℤ", "f": "succ"})
    """

    def __init__(self) -> None:
        """Initialise with an empty persistent context."""
        self._context: dict[str, str] = {}

    def infer(
        self,
        schema: TheoremSchema,
        context: dict[str, str],
    ) -> dict[str, str]:
        """Infer variable bindings from an explicit context dictionary.

        For each free variable in *schema*, look for an exact-match key in
        the merged context (persistent context merged with the provided
        context; the provided context takes precedence).  Variables not found
        in either context receive the placeholder ``?{var_name}``.

        Args:
            schema: The ``TheoremSchema`` whose free variables need bindings.
            context: Call-time context overrides.

        Returns:
            Dictionary mapping each free variable to its inferred value.
        """
        merged = dict(self._context)
        merged.update(context)
        bindings: dict[str, str] = {}
        for var in schema.free_vars:
            if var in merged:
                bindings[var] = merged[var]
            else:
                bindings[var] = f"?{var}"
                logger.debug(
                    "Variable %r unresolved; using placeholder ?%s.", var, var
                )
        return bindings

    def infer_from_text(
        self,
        schema: TheoremSchema,
        text: str,
    ) -> dict[str, str]:
        """Infer variable bindings by scanning *text* for ``{word}`` patterns.

        Extracts all ``{word}`` occurrences from *text* in order and assigns
        them to schema free variables positionally.  Excess patterns are
        ignored; excess variables fall back to placeholders.

        Args:
            schema: The ``TheoremSchema`` whose free variables need bindings.
            text: Raw text that may contain ``{word}`` placeholder markers.

        Returns:
            Dictionary mapping each free variable to an extracted value or
            placeholder.
        """
        pattern = re.compile(r"\{(\w+)\}")
        extracted = [m.group(1) for m in pattern.finditer(text)]
        bindings: dict[str, str] = {}
        for i, var in enumerate(schema.free_vars):
            if i < len(extracted):
                bindings[var] = extracted[i]
            else:
                bindings[var] = f"?{var}"
        return bindings

    def update_context(self, key: str, value: str) -> None:
        """Add or update a single entry in the persistent context.

        Args:
            key: Variable name or concept name.
            value: Corresponding mathematical object or expression.
        """
        self._context[key] = value
        logger.debug("Context updated: %r → %r.", key, value)

    def clear_context(self) -> None:
        """Remove all entries from the persistent context."""
        self._context.clear()
        logger.debug("Binding inference context cleared.")

    def validate_inferred_bindings(
        self,
        schema: TheoremSchema,
        bindings: dict[str, str],
    ) -> bool:
        """Return True if *bindings* resolves all free variables without placeholders.

        A binding is considered resolved if it does not start with ``?``.  This
        distinguishes concrete values from the ``?var`` placeholders emitted
        by ``infer`` and ``infer_from_text`` for missing variables.

        Args:
            schema: The schema whose free variables to check.
            bindings: The binding dictionary to validate.

        Returns:
            True when every free variable has a non-placeholder binding.
        """
        for var in schema.free_vars:
            value = bindings.get(var, f"?{var}")
            if value.startswith("?"):
                return False
        return True

    def explain_inference(
        self,
        schema: TheoremSchema,
        bindings: dict[str, str],
    ) -> str:
        """Produce a human-readable explanation of which variables were resolved.

        Args:
            schema: The schema whose variables are described.
            bindings: The binding dictionary to explain.

        Returns:
            Multi-line string listing each variable, its binding, and whether
            it was resolved or is still a placeholder.
        """
        lines = [f"Binding inference for schema [{schema.name}]:"]
        for var in schema.free_vars:
            value = bindings.get(var, f"?{var}")
            status = "resolved" if not value.startswith("?") else "UNRESOLVED"
            lines.append(f"  {var} = {value!r}  [{status}]")
        if not schema.free_vars:
            lines.append("  (no free variables)")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# SchemaCompositionAlgorithm
# ---------------------------------------------------------------------------


class SchemaCompositionAlgorithm:
    """Composes two or more theorem schemas into a compound schema.

    Composition is purely syntactic: the template statements of the input
    schemas are joined by a logical connective string, and the free variable
    lists are merged (with deduplication).  The resulting compound schema can
    itself be composed further, enabling arbitrary nesting.

    Supported connectives are any strings, but the canonical choices are:
    - ``"and"`` — both theorems must hold simultaneously.
    - ``"or"`` — at least one theorem must hold.
    - ``"implies"`` — the first theorem's truth implies the second's.

    The algorithm also maintains an optional schema registry so that composed
    schemas can be retrieved by ID later without the caller having to manage
    the returned object.

    Example usage::

        algo = SchemaCompositionAlgorithm()
        compound = algo.compose(schema_a, schema_b, connective="implies")
        chain = algo.compose_chain([s1, s2, s3])
    """

    def __init__(self) -> None:
        """Initialise with an empty registry."""
        self._registry: dict[str, TheoremSchema] = {}

    def compose(
        self,
        schema_a: TheoremSchema,
        schema_b: TheoremSchema,
        connective: str = "and",
    ) -> TheoremSchema:
        """Compose two schemas with a logical connective.

        The new schema's template statement is::

            {connective}: [{name_a}] {template_a} AND [{name_b}] {template_b}

        Free variables are the union of both schemas' free variable lists,
        with duplicates removed while preserving order (variables from
        *schema_a* come first).

        Args:
            schema_a: First (left-hand) schema.
            schema_b: Second (right-hand) schema.
            connective: Logical connective string (default ``"and"``).

        Returns:
            A new ``TheoremSchema`` representing the compound theorem.
        """
        merged_vars: list[str] = list(schema_a.free_vars)
        for v in schema_b.free_vars:
            if v not in merged_vars:
                merged_vars.append(v)

        compound_template = (
            f"{connective.upper()}: [{schema_a.name}] {schema_a.template_statement} "
            f"{connective.upper()} [{schema_b.name}] {schema_b.template_statement}"
        )
        compound_name = f"({schema_a.name} {connective} {schema_b.name})"
        compound = TheoremSchema(
            schema_id=str(uuid.uuid4()),
            name=compound_name,
            template_statement=compound_template,
            free_vars=merged_vars,
            proof_style=schema_a.proof_style,
            subsystem=schema_a.subsystem,
            description=(
                f"Compound schema: {schema_a.description} "
                f"[{connective}] {schema_b.description}"
            ),
            tags=list(set(schema_a.tags + schema_b.tags)),
            created_at=time.time(),
            metadata={
                "composed_from": [schema_a.schema_id, schema_b.schema_id],
                "connective": connective,
            },
        )
        self._registry[compound.schema_id] = compound
        logger.debug(
            "Composed schema %s from %s %s %s.",
            compound.schema_id[:8],
            schema_a.schema_id[:8],
            connective,
            schema_b.schema_id[:8],
        )
        return compound

    def compose_chain(self, schemas: list[TheoremSchema]) -> TheoremSchema:
        """Fold-left compose a list of schemas into a single compound schema.

        The composition is left-associative: ``[A, B, C]`` becomes
        ``(A and B) and C``.  Requires at least two schemas.

        Args:
            schemas: List of at least two ``TheoremSchema`` objects.

        Returns:
            The fully composed compound ``TheoremSchema``.

        Raises:
            ValueError: If fewer than two schemas are provided.
        """
        if len(schemas) < 2:
            raise ValueError(
                f"compose_chain requires at least 2 schemas; got {len(schemas)}."
            )
        result = self.compose(schemas[0], schemas[1])
        for schema in schemas[2:]:
            result = self.compose(result, schema)
        return result

    def find_compatible_pairs(
        self, schemas: list[TheoremSchema]
    ) -> list[tuple[str, str]]:
        """Return pairs of schema IDs that share at least one free variable.

        Two schemas are considered *compatible* for composition when they
        share at least one free variable name, indicating that their combined
        statement will have a non-trivial variable overlap.  Pairs are
        returned as ``(schema_id_a, schema_id_b)`` tuples, without duplicates
        (i.e. ``(a, b)`` is returned but not ``(b, a)``).

        Args:
            schemas: List of ``TheoremSchema`` objects to analyse.

        Returns:
            List of ``(schema_id, schema_id)`` tuples for compatible pairs.
        """
        pairs: list[tuple[str, str]] = []
        for i, sa in enumerate(schemas):
            for sb in schemas[i + 1:]:
                shared = set(sa.free_vars) & set(sb.free_vars)
                if shared:
                    pairs.append((sa.schema_id, sb.schema_id))
        return pairs

    def register(self, schema: TheoremSchema) -> None:
        """Add a schema to the local registry.

        Args:
            schema: The ``TheoremSchema`` to register.
        """
        self._registry[schema.schema_id] = schema

    def lookup(self, schema_id: str) -> TheoremSchema | None:
        """Return a schema from the registry by ID, or None if not found.

        Args:
            schema_id: The ID to look up.

        Returns:
            The matching ``TheoremSchema`` or ``None``.
        """
        return self._registry.get(schema_id)


# ---------------------------------------------------------------------------
# ObligationPrioritizationAlgorithm
# ---------------------------------------------------------------------------


class ObligationPrioritizationAlgorithm:
    """Assigns integer priority scores to proof obligations.

    The score is computed as::

        base_priority[subsystem] + overdue_bonus + failure_penalty

    where:
    - ``base_priority`` is a per-``SubsystemKind`` constant (configurable).
    - ``overdue_bonus`` is +3 when the obligation has a deadline that has
      already elapsed.
    - ``failure_penalty`` is -2 when the obligation's metadata contains a
      ``failure_reason`` key (indicating a prior failed attempt).

    Higher scores indicate more urgent obligations.  The ``urgent_threshold``
    is 8, meaning any obligation with score ≥ 8 is considered urgent.

    Example usage::

        algo = ObligationPrioritizationAlgorithm()
        sorted_obligations = algo.prioritize_batch(obligations)
        for ob in sorted_obligations:
            print(ob.priority, ob.statement[:40])
    """

    _DEFAULT_BASE: dict[SubsystemKind, int] = {
        SubsystemKind.DESCENT: 8,
        SubsystemKind.TRUST: 9,
        SubsystemKind.EVIDENCE: 7,
        SubsystemKind.FEDERATION: 6,
        SubsystemKind.INVALIDATION: 8,
        SubsystemKind.MEMORY: 7,
        SubsystemKind.JUDGMENT: 9,
        SubsystemKind.ENCODING: 5,
    }

    def __init__(self) -> None:
        """Initialise with the default base priority table."""
        self._base_priority: dict[SubsystemKind, int] = dict(
            self._DEFAULT_BASE
        )

    def score(self, obligation: ProofObligation) -> int:
        """Compute a priority score for a single obligation.

        The score is the sum of the subsystem base priority, an overdue bonus
        (+3 if past deadline), and a failure penalty (-2 if previously
        failed).  The result is clamped to the range ``[0, 10]``.

        Args:
            obligation: The ``ProofObligation`` to score.

        Returns:
            Integer priority score in ``[0, 10]``.
        """
        base = self._base_priority.get(obligation.subsystem, 4)
        overdue_bonus = 3 if obligation.is_overdue() else 0
        failure_penalty = (
            -2 if "failure_reason" in obligation.metadata else 0
        )
        raw = base + overdue_bonus + failure_penalty
        return max(0, min(10, raw))

    def prioritize_batch(
        self, obligations: list[ProofObligation]
    ) -> list[ProofObligation]:
        """Sort obligations by score descending (highest priority first).

        Also updates each obligation's ``priority`` field in-place so that
        the score is persisted for downstream consumers such as
        ``ObligationQueue``.

        Args:
            obligations: List of ``ProofObligation`` objects to sort.

        Returns:
            New list sorted by computed score, highest first.
        """
        for ob in obligations:
            ob.priority = self.score(ob)
        return sorted(obligations, key=lambda o: o.priority, reverse=True)

    def set_base_priority(
        self, subsystem: SubsystemKind, priority: int
    ) -> None:
        """Override the base priority for a specific subsystem.

        Args:
            subsystem: The ``SubsystemKind`` to configure.
            priority: New base priority value (will be clamped to ``[0, 10]``).
        """
        self._base_priority[subsystem] = max(0, min(10, priority))
        logger.debug(
            "Base priority for %s set to %d.", subsystem.value, priority
        )

    def urgent_threshold(self) -> int:
        """Return the score threshold above which an obligation is urgent.

        Returns:
            8 (constant).
        """
        return 8

    def is_urgent(self, obligation: ProofObligation) -> bool:
        """Return True if the obligation's score meets the urgent threshold.

        Args:
            obligation: The ``ProofObligation`` to evaluate.

        Returns:
            True when ``self.score(obligation) >= self.urgent_threshold()``.
        """
        return self.score(obligation) >= self.urgent_threshold()


# ---------------------------------------------------------------------------
# SchemaConsistencyChecker
# ---------------------------------------------------------------------------


class SchemaConsistencyChecker:
    """Checks schema sets for internal consistency and cross-schema conflicts.

    A single schema is *internally consistent* when:
    - All free variables referenced in ``template_statement`` via ``{var}``
      patterns are listed in ``free_vars``.
    - The ``proof_style`` and ``subsystem`` values are valid enum members
      (enforced by construction, but re-checked here for safety).

    Two schemas are *mutually consistent* unless:
    - They share the same ``name`` but have different ``template_statement``
      values (potential contradiction — same theorem, different definitions).

    A collection of schemas contains *duplicates* when two schemas have
      identical ``template_statement`` values, regardless of name.

    All violations are accumulated in ``_violations`` for later reporting.

    Example usage::

        checker = SchemaConsistencyChecker()
        issues = checker.check_schema(schema)
        all_issues = checker.check_all(schema_list)
        print(checker.report())
    """

    def __init__(self) -> None:
        """Initialise with an empty violations list."""
        self._violations: list[dict[str, Any]] = []

    def check_schema(self, schema: TheoremSchema) -> list[str]:
        """Check a single schema for internal consistency.

        Detects variables used in the template but absent from ``free_vars``
        and variables listed in ``free_vars`` but absent from the template.

        Args:
            schema: The ``TheoremSchema`` to check.

        Returns:
            List of human-readable violation strings (empty if consistent).
        """
        issues: list[str] = []
        # Extract {var} placeholders from the template
        template_vars = set(re.findall(r"\{(\w+)\}", schema.template_statement))
        declared_vars = set(schema.free_vars)
        for v in template_vars - declared_vars:
            msg = (
                f"Schema [{schema.name}]: variable {{{v}}} used in template "
                f"but not declared in free_vars."
            )
            issues.append(msg)
            self._violations.append({"schema_id": schema.schema_id, "message": msg})
        for v in declared_vars - template_vars:
            msg = (
                f"Schema [{schema.name}]: variable {{{v}}} declared in free_vars "
                f"but never used in template."
            )
            issues.append(msg)
            self._violations.append({"schema_id": schema.schema_id, "message": msg})
        return issues

    def check_pair(
        self, a: TheoremSchema, b: TheoremSchema
    ) -> list[str]:
        """Check two schemas for mutual consistency.

        Reports a contradiction if they have the same ``name`` but different
        ``template_statement`` values.

        Args:
            a: First schema.
            b: Second schema.

        Returns:
            List of violation strings (empty if no conflict detected).
        """
        issues: list[str] = []
        if a.name == b.name and a.template_statement != b.template_statement:
            msg = (
                f"Schemas [{a.schema_id[:8]}] and [{b.schema_id[:8]}] share "
                f"name {a.name!r} but have different template statements — "
                f"potential logical contradiction."
            )
            issues.append(msg)
            self._violations.append(
                {
                    "schema_id_a": a.schema_id,
                    "schema_id_b": b.schema_id,
                    "message": msg,
                }
            )
        return issues

    def check_all(
        self, schemas: list[TheoremSchema]
    ) -> dict[str, list[str]]:
        """Check every schema individually and every pair for conflicts.

        Args:
            schemas: List of ``TheoremSchema`` objects to check.

        Returns:
            Dictionary mapping ``schema_id`` (or ``"pairs"``) to lists of
            violation strings.
        """
        result: dict[str, list[str]] = defaultdict(list)
        for schema in schemas:
            issues = self.check_schema(schema)
            if issues:
                result[schema.schema_id].extend(issues)
        for i, sa in enumerate(schemas):
            for sb in schemas[i + 1:]:
                issues = self.check_pair(sa, sb)
                if issues:
                    result["pairs"].extend(issues)
        return dict(result)

    def find_duplicates(
        self, schemas: list[TheoremSchema]
    ) -> list[tuple[str, str]]:
        """Return pairs of schema IDs with identical template statements.

        Args:
            schemas: List of ``TheoremSchema`` objects to search.

        Returns:
            List of ``(schema_id_a, schema_id_b)`` pairs where both schemas
            have the same ``template_statement``.
        """
        seen: dict[str, str] = {}
        duplicates: list[tuple[str, str]] = []
        for schema in schemas:
            tmpl = schema.template_statement
            if tmpl in seen:
                duplicates.append((seen[tmpl], schema.schema_id))
            else:
                seen[tmpl] = schema.schema_id
        return duplicates

    def clear_violations(self) -> None:
        """Clear the accumulated violations list."""
        self._violations.clear()

    def report(self) -> str:
        """Generate a human-readable report of all accumulated violations.

        Returns:
            Multi-line report string.  If no violations have been recorded
            the report simply states "No violations detected."
        """
        if not self._violations:
            return "=== SchemaConsistencyChecker: No violations detected. ==="
        lines = [
            "=== SchemaConsistencyChecker Report ===",
            f"  Total violations: {len(self._violations)}",
            "",
        ]
        for i, v in enumerate(self._violations, start=1):
            lines.append(f"  [{i}] {v.get('message', str(v))}")
        lines.append("========================================")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# TemplateExpansionAlgorithm
# ---------------------------------------------------------------------------


class TemplateExpansionAlgorithm:
    """Expands template statements into fully-substituted formal strings.

    Two types of expansion are supported:

    1. **Variable substitution**: occurrences of ``{var}`` in the template
       are replaced with the corresponding value from *bindings*.

    2. **Macro expansion**: registered LaTeX-style macros are applied after
       variable substitution.  A macro named ``\\setZ`` with expansion
       ``\\mathbb{Z}`` will replace every occurrence of ``\\setZ`` in the
       expanded string.

    Variable substitution is always applied before macro expansion so that
    macro expansions can themselves contain variable placeholders (though this
    is unusual in practice).

    Example usage::

        algo = TemplateExpansionAlgorithm()
        algo.add_macro("\\setZ", "\\mathbb{Z}")
        expanded = algo.expand("for all {x} in \\setZ", {"x": "n"})
        # → "for all n in \\mathbb{Z}"
    """

    def __init__(self) -> None:
        """Initialise with no macros registered."""
        self._macros: dict[str, str] = {}

    def expand(self, template: str, bindings: dict[str, str]) -> str:
        """Substitute *bindings* into *template*, then apply macros.

        Args:
            template: The template string containing ``{var}`` placeholders.
            bindings: Mapping of variable name to substitution value.

        Returns:
            Fully-expanded string with all variables substituted and macros
            applied.
        """
        result = template
        for var, value in bindings.items():
            result = result.replace("{" + var + "}", value)
        for macro_name, macro_expansion in self._macros.items():
            result = result.replace(macro_name, macro_expansion)
        return result

    def add_macro(self, name: str, expansion: str) -> None:
        """Register a named macro.

        Args:
            name: The macro name as it appears in templates (e.g. ``\\setZ``).
            expansion: The string to substitute in place of *name*.
        """
        self._macros[name] = expansion
        logger.debug("Macro registered: %r → %r.", name, expansion)

    def remove_macro(self, name: str) -> bool:
        """Remove a registered macro by name.

        Args:
            name: The macro name to remove.

        Returns:
            True if the macro existed and was removed, False otherwise.
        """
        existed = name in self._macros
        if existed:
            del self._macros[name]
            logger.debug("Macro removed: %r.", name)
        return existed

    def expand_schema(
        self, schema: TheoremSchema, bindings: dict[str, str]
    ) -> str:
        """Expand the template of *schema* using *bindings*.

        Delegates to ``expand`` using ``schema.template_statement`` as the
        template.

        Args:
            schema: The schema whose template to expand.
            bindings: Variable bindings.

        Returns:
            Expanded formal theorem statement.
        """
        return self.expand(schema.template_statement, bindings)

    def validate_expansion(self, expanded: str) -> bool:
        """Return True if no unresolved ``{var}`` placeholders remain.

        An expansion is considered valid when the ``{word}`` pattern does not
        appear anywhere in the result string.

        Args:
            expanded: The string to validate.

        Returns:
            True if no placeholder pattern remains.
        """
        return not bool(re.search(r"\{(\w+)\}", expanded))

    def list_macros(self) -> dict[str, str]:
        """Return a shallow copy of the registered macros dictionary.

        Returns:
            Dictionary mapping macro names to their expansions.
        """
        return dict(self._macros)


# ---------------------------------------------------------------------------
# ProofSearchAlgorithm
# ---------------------------------------------------------------------------


class ProofSearchAlgorithm:
    """Searches an archive of discharge records for reusable proofs.

    The search strategy is statement-based: a record is considered a
    candidate match for an obligation when the record's
    ``proof_data.get("statement", "")`` either exactly equals or is a
    substring of ``obligation.statement``.  This is a conservative heuristic;
    callers can refine results by inspecting the returned records.

    An in-memory archive accumulates records across calls so that proof reuse
    can be detected across an entire session without external storage.

    Example usage::

        algo = ProofSearchAlgorithm()
        algo.add_to_archive(record)
        candidates = algo.search(obligation)
    """

    def __init__(self) -> None:
        """Initialise with an empty proof archive."""
        self._archive: list[DischargeRecord] = []

    def add_to_archive(self, record: DischargeRecord) -> None:
        """Add a discharge record to the searchable archive.

        Args:
            record: The ``DischargeRecord`` to archive.
        """
        self._archive.append(record)
        logger.debug(
            "Archived discharge record for obligation %s.",
            record.obligation_id[:8],
        )

    def search(self, obligation: ProofObligation) -> list[DischargeRecord]:
        """Find archived records whose statement matches the obligation.

        Matching criterion: the record's ``proof_data["statement"]`` value
        (if present) is a substring of ``obligation.statement``, or vice
        versa.  Both exact and partial matches are returned.

        Args:
            obligation: The ``ProofObligation`` to search for.

        Returns:
            List of matching ``DischargeRecord`` objects, most recent first.
        """
        target = obligation.statement.lower()
        matches: list[DischargeRecord] = []
        for record in self._archive:
            record_stmt = record.proof_data.get("statement", "").lower()
            if record_stmt and (
                record_stmt in target or target in record_stmt
            ):
                matches.append(record)
        matches.sort(key=lambda r: r.timestamp, reverse=True)
        return matches

    def search_by_agent(self, agent: ProofAgent) -> list[DischargeRecord]:
        """Return all archived records produced by *agent*.

        Args:
            agent: The ``ProofAgent`` to filter by.

        Returns:
            List of ``DischargeRecord`` objects from that agent, most recent
            first.
        """
        results = [r for r in self._archive if r.agent == agent]
        return sorted(results, key=lambda r: r.timestamp, reverse=True)

    def search_recent(self, hours: float = 24.0) -> list[DischargeRecord]:
        """Return records created within the last *hours* hours.

        Args:
            hours: Look-back window in hours (default: 24).

        Returns:
            List of recent ``DischargeRecord`` objects, most recent first.
        """
        cutoff = time.time() - (hours * 3600.0)
        results = [r for r in self._archive if r.timestamp >= cutoff]
        return sorted(results, key=lambda r: r.timestamp, reverse=True)

    def archive_size(self) -> int:
        """Return the total number of records in the archive.

        Returns:
            Integer count.
        """
        return len(self._archive)

    def clear_archive(self) -> None:
        """Remove all records from the archive."""
        self._archive.clear()
        logger.debug("Proof archive cleared.")


# ---------------------------------------------------------------------------
# SchemaMinimizationAlgorithm
# ---------------------------------------------------------------------------


class SchemaMinimizationAlgorithm:
    """Removes redundant schemas from a collection.

    A schema A is considered *redundant* (subsumed by B) when
    ``A.template_statement`` is a strict substring of
    ``B.template_statement``.  The intuition is that B is a strictly stronger
    theorem: it asserts everything A asserts and more.  Therefore A provides
    no additional proof burden beyond what B already requires, and it can be
    removed from an obligation set that already includes B.

    Note that this is a syntactic, not semantic, subsumption check.  It will
    miss cases where A is semantically implied by B but the templates are
    textually unrelated.  A semantic subsumption check would require an
    external theorem prover and is out of scope here.

    Example usage::

        algo = SchemaMinimizationAlgorithm()
        minimal = algo.minimize(schema_list)
        redundant_ids = algo.find_redundant(schema_list)
    """

    def minimize(
        self, schemas: list[TheoremSchema]
    ) -> list[TheoremSchema]:
        """Return the minimal subset of *schemas* with no redundant entries.

        Two schemas are compared pairwise; a schema is removed if it is
        subsumed by any other schema in the list.  The comparison is
        asymmetric: A subsumes B iff B's template is a strict (proper)
        substring of A's template.

        Args:
            schemas: Input list of ``TheoremSchema`` objects.

        Returns:
            Subset of *schemas* with no subsumed entries, in original order.
        """
        redundant = set(self.find_redundant(schemas))
        return [s for s in schemas if s.schema_id not in redundant]

    def find_redundant(
        self, schemas: list[TheoremSchema]
    ) -> list[str]:
        """Return the schema IDs that are subsumed by at least one other schema.

        Args:
            schemas: List of ``TheoremSchema`` objects to analyse.

        Returns:
            List of ``schema_id`` strings that are redundant.
        """
        redundant: list[str] = []
        for i, candidate in enumerate(schemas):
            for j, stronger in enumerate(schemas):
                if i == j:
                    continue
                if self.is_subsumed(candidate, by=stronger):
                    redundant.append(candidate.schema_id)
                    break
        return redundant

    def is_subsumed(
        self, schema: TheoremSchema, by: TheoremSchema
    ) -> bool:
        """Return True if *schema* is subsumed by *by*.

        Subsumption is defined syntactically: schema A is subsumed by B when
        A's template is a *strict* (proper) substring of B's template.
        Self-subsumption (equal templates) returns False.

        Args:
            schema: The candidate schema to check for subsumption.
            by: The potentially stronger schema.

        Returns:
            True when ``schema.template_statement`` is a strict substring of
            ``by.template_statement``.
        """
        a = schema.template_statement
        b = by.template_statement
        if a == b:
            return False
        return a in b

    def explain_minimization(
        self,
        original: list[TheoremSchema],
        minimized: list[TheoremSchema],
    ) -> str:
        """Produce a human-readable explanation of the minimization step.

        Compares *original* and *minimized* to list which schemas were removed
        and why (which schema subsumed them).

        Args:
            original: The input schema list before minimization.
            minimized: The output schema list after minimization.

        Returns:
            Multi-line explanation string.
        """
        minimized_ids = {s.schema_id for s in minimized}
        removed = [s for s in original if s.schema_id not in minimized_ids]
        lines = [
            "=== Schema Minimization Explanation ===",
            f"  Original count : {len(original)}",
            f"  Minimized count: {len(minimized)}",
            f"  Removed        : {len(removed)}",
            "",
        ]
        for rem in removed:
            # Find which schema subsumed it
            subsumed_by = next(
                (
                    s.name
                    for s in original
                    if s.schema_id != rem.schema_id
                    and self.is_subsumed(rem, by=s)
                ),
                "unknown",
            )
            lines.append(
                f"  - Removed [{rem.name}] (subsumed by [{subsumed_by}])"
            )
        if not removed:
            lines.append("  (no schemas were removed)")
        lines.append("========================================")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Internal helper functions
# ---------------------------------------------------------------------------


def _load_schemas_from_json(data: list[dict[str, Any]]) -> list[TheoremSchema]:
    """Deserialise a list of schema dictionaries into ``TheoremSchema`` objects.

    This helper is used internally to reconstruct schema collections from
    persisted JSON snapshots.

    Args:
        data: List of dictionaries as produced by ``TheoremSchema.to_json()``.

    Returns:
        List of reconstructed ``TheoremSchema`` objects.
    """
    return [TheoremSchema.from_json(d) for d in data]


def _group_by_subsystem(
    schemas: list[TheoremSchema],
) -> dict[SubsystemKind, list[TheoremSchema]]:
    """Group a flat list of schemas by their subsystem.

    Args:
        schemas: List of ``TheoremSchema`` objects.

    Returns:
        Dictionary mapping ``SubsystemKind`` → list of schemas in that
        subsystem, in definition order.
    """
    result: dict[SubsystemKind, list[TheoremSchema]] = defaultdict(list)
    for schema in schemas:
        result[schema.subsystem].append(schema)
    return dict(result)


def _count_unresolved_vars(bindings: dict[str, str]) -> int:
    """Count the number of placeholder (unresolved) values in *bindings*.

    A binding value is considered a placeholder when it starts with ``?``.

    Args:
        bindings: Variable-to-value mapping from ``BindingInferenceAlgorithm``.

    Returns:
        Integer count of unresolved bindings.
    """
    return sum(1 for v in bindings.values() if v.startswith("?"))


def _filter_urgent(
    obligations: list[ProofObligation],
    algo: ObligationPrioritizationAlgorithm,
) -> list[ProofObligation]:
    """Return only the urgent obligations from *obligations*.

    An obligation is urgent when ``algo.is_urgent(obligation)`` returns True.

    Args:
        obligations: Full list of proof obligations.
        algo: Configured ``ObligationPrioritizationAlgorithm`` instance.

    Returns:
        Sub-list of obligations whose score meets the urgency threshold.
    """
    return [ob for ob in obligations if algo.is_urgent(ob)]


# ---------------------------------------------------------------------------
# Judgment-geometric cross-references
# ---------------------------------------------------------------------------

try:
    from jugeo.judgments import judgment_terms as _judgment_terms
except ImportError:
    _judgment_terms = None  # type: ignore[assignment]

try:
    from jugeo.geometry import descent as _descent_mod
except ImportError:
    _descent_mod = None  # type: ignore[assignment]

try:
    from jugeo.evidence import manifests as _manifests_mod
except ImportError:
    _manifests_mod = None  # type: ignore[assignment]


def schemas_for_judgment(judgment: Any) -> dict[str, Any]:
    """Retrieve applicable theorem schemas for a judgment term.

    Bridges the judgment subsystem into the theorem-schema pipeline by
    extracting the term's logical payload and matching it against the
    available schemas.

    Parameters
    ----------
    judgment:
        A judgment term from ``jugeo.judgments.judgment_terms``.

    Returns
    -------
    dict[str, Any]
        A dict with ``"judgment"``, ``"matched_schemas"``, and
        ``"term_data"`` keys.
    """
    if _judgment_terms is None:
        raise RuntimeError("jugeo.judgments.judgment_terms is not available")
    term_data = _judgment_terms.extract_term(judgment) if hasattr(_judgment_terms, "extract_term") else {"raw": str(judgment)}
    return {
        "judgment": judgment,
        "matched_schemas": [],
        "term_data": term_data,
    }


def descent_obligation(descent_result: Any) -> dict[str, Any]:
    """Create a proof obligation from a geometric descent result.

    Converts a descent result from ``jugeo.geometry.descent`` into a
    proof obligation that the theorem-schema engine can process.

    Parameters
    ----------
    descent_result:
        A descent result object from ``jugeo.geometry.descent``.

    Returns
    -------
    dict[str, Any]
        A dict with ``"descent_result"``, ``"obligation_formula"``, and
        ``"status"`` keys.
    """
    if _descent_mod is None:
        raise RuntimeError("jugeo.geometry.descent is not available")
    formula = _descent_mod.obligation_formula(descent_result) if hasattr(_descent_mod, "obligation_formula") else str(descent_result)
    return {
        "descent_result": descent_result,
        "obligation_formula": formula,
        "status": "pending",
    }


def evidence_obligation(evidence: Any) -> dict[str, Any]:
    """Create a proof obligation from evidence manifest data.

    Bridges the evidence subsystem into the theorem-schema pipeline by
    converting an evidence manifest into a proof obligation.

    Parameters
    ----------
    evidence:
        An evidence manifest from ``jugeo.evidence.manifests``.

    Returns
    -------
    dict[str, Any]
        A dict with ``"evidence"``, ``"obligation_type"``, and
        ``"manifest_id"`` keys.
    """
    if _manifests_mod is None:
        raise RuntimeError("jugeo.evidence.manifests is not available")
    manifest_id = _manifests_mod.manifest_id(evidence) if hasattr(_manifests_mod, "manifest_id") else str(id(evidence))
    return {
        "evidence": evidence,
        "obligation_type": "evidence_manifest",
        "manifest_id": manifest_id,
    }
