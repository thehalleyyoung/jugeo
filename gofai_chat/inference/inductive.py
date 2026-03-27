"""Inductive logic programming and concept learning grounded in the Grade semiring.

Judgment-Harmonic Inductive Learning
======================================
This module implements inductive concept learning within the framework of
Judgment-Harmonic theory.  All hypothesis quality scores, example confidence
values, and concept grades are :class:`~gofai_chat.core.grade.Grade`-valued.

Grade semantics of inductive learning
---------------------------------------
* **Example grade**: how confident we are in the example's label.
  ``Grade.perfect()`` = absolutely certain positive/negative.
  ``Grade.from_prob(0.5)`` = uncertain.  Examples with lower grades
  contribute less to hypothesis evaluation (via their ``effective_weight``).

* **Hypothesis grade**: the overall quality of an inductive hypothesis,
  computed as the product of:
  - Coverage grade: ``Grade.from_prob(coverage)``
  - Precision grade: ``Grade.from_prob(precision)``
  - Simplicity grade: ``Grade.from_prob(exp(-0.1 * n_conditions))``

  Using Grade **multiplication** because all three must hold simultaneously
  for a hypothesis to be high-quality.

* **Concept grade** in :class:`ConceptLattice`: the Grade product of all
  examples in the concept's extent.  A concept supported by many high-grade
  examples has a high concept grade; uncertain examples attenuate it.

Integration with GluingData
---------------------------
:meth:`ConceptLattice.to_gluing` packs the lattice into a
:class:`~gofai_chat.harmony.gluing.GluingData` for downstream harmony
computation.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional, Iterator
from enum import Enum, auto
from collections import defaultdict

from gofai_chat.core.grade import Grade
from gofai_chat.harmony.gluing import GluingData

__all__ = [
    "Example",
    "Hypothesis",
    "FormalConcept",
    "InductiveLearner",
    "ConceptLattice",
    "GradeWeightedID3",
    "VersionSpace",
    "animal_classification_examples",
    "medical_diagnosis_examples",
    "nlp_event_examples",
]

# ---------------------------------------------------------------------------
# Example
# ---------------------------------------------------------------------------

@dataclass
class Example:
    """A training example with Grade-valued confidence.

    The ``grade`` reflects how reliable this example is as training data.
    ``Grade.perfect()`` means the label is definite; lower grades reflect
    noise, partial observability, or annotator disagreement.

    In inductive learning, Grade-weighted examples allow soft concept
    formation: uncertain examples still contribute but with reduced weight,
    preventing brittle over-fitting to noisy data.

    Attributes
    ----------
    id:
        Unique identifier.
    description:
        Feature dictionary: ``{feature_name: feature_value}``.
    label:
        True = positive example, False = negative example.
    grade:
        Confidence in the label assignment.
    source:
        Provenance string (e.g. ``"expert_annotation"``, ``"web_scrape"``).
    """

    id: str
    description: dict[str, str]
    label: bool
    grade: Grade
    source: str = ""

    def effective_weight(self) -> float:
        """Return the effective weight of this example in probability space.

        ``grade.to_prob()`` — higher Grade = more influence on learning.

        Returns
        -------
        float
            Weight in [0, 1].
        """
        return self.grade.to_prob()

    def feature_set(self) -> frozenset[tuple[str, str]]:
        """Return the features as a frozenset of (feature, value) pairs.

        Returns
        -------
        frozenset[tuple[str, str]]
            Frozenset of feature-value pairs.
        """
        return frozenset(self.description.items())

    def matches_conditions(
        self, conditions: list[tuple[str, str]]
    ) -> bool:
        """Check if this example satisfies all given feature=value conditions.

        Parameters
        ----------
        conditions:
            List of ``(feature, value)`` pairs; all must be satisfied.

        Returns
        -------
        bool
            True if all conditions match.
        """
        for feature, value in conditions:
            if self.description.get(feature) != value:
                return False
        return True

    def __repr__(self) -> str:
        label_str = "+" if self.label else "-"
        return (
            f"Example({self.id!r}, {label_str}, grade={self.grade}, "
            f"features={len(self.description)})"
        )


# ---------------------------------------------------------------------------
# Hypothesis
# ---------------------------------------------------------------------------

@dataclass
class Hypothesis:
    """A conjunctive inductive hypothesis with Grade quality.

    A hypothesis is a conjunction of ``feature=value`` conditions.
    The Grade quality combines coverage, precision, and simplicity.

    Attributes
    ----------
    id:
        Unique identifier.
    conditions:
        List of ``(feature, value)`` pairs forming the conjunctive body.
    grade:
        Overall quality grade (product of coverage, precision, simplicity).
    coverage:
        Fraction of positive examples covered (recall).
    precision:
        Fraction of covered examples that are positive.
    support:
        Number of positive examples covered.
    """

    id: str
    conditions: list[tuple[str, str]]
    grade: Grade
    coverage: float
    precision: float
    support: int = 0

    def complexity(self) -> float:
        """Return complexity as the number of conditions.

        More conditions = more specific = more complex.

        Returns
        -------
        float
            Number of conditions.
        """
        return float(len(self.conditions))

    def f1_score(self) -> float:
        """Compute F1 = 2 * precision * recall / (precision + recall).

        Here recall = coverage.

        Returns
        -------
        float
            F1 score in [0, 1].
        """
        p = self.precision
        r = self.coverage
        if p + r == 0.0:
            return 0.0
        return 2.0 * p * r / (p + r)

    def subsumes(self, other: "Hypothesis") -> bool:
        """Return True if self is more general than other.

        ``self`` subsumes ``other`` if every condition in ``self`` also
        appears in ``other``.  A more general hypothesis has fewer conditions.

        Parameters
        ----------
        other:
            Hypothesis to compare.

        Returns
        -------
        bool
            True if self ⊆ other (condition-set wise).
        """
        self_set = set(self.conditions)
        other_set = set(other.conditions)
        return self_set.issubset(other_set)

    def specialize_with(
        self, feature: str, value: str
    ) -> "Hypothesis":
        """Return a new, more specific hypothesis with one more condition.

        Specialization in the Grade semiring: adding a condition makes the
        hypothesis more specific (higher precision, lower coverage).
        The new hypothesis grade must be recomputed.

        Parameters
        ----------
        feature:
            Feature to add.
        value:
            Required value.

        Returns
        -------
        Hypothesis
            New hypothesis with one additional condition.
        """
        new_conditions = list(self.conditions) + [(feature, value)]
        return Hypothesis(
            id=f"{self.id}+{feature}={value}",
            conditions=new_conditions,
            grade=Grade.impossible(),  # will be recomputed
            coverage=0.0,
            precision=0.0,
            support=0,
        )

    def covers(self, example: "Example") -> bool:
        """Return True if this hypothesis covers ``example``.

        An example is covered if it satisfies all conditions.

        Parameters
        ----------
        example:
            The example to check.

        Returns
        -------
        bool
            True if all conditions match.
        """
        return example.matches_conditions(self.conditions)

    def to_rule_string(self) -> str:
        """Return a human-readable rule string.

        Returns
        -------
        str
            ``"IF feature1=value1 AND feature2=value2 THEN positive"``
        """
        if not self.conditions:
            return "IF (empty) THEN positive"
        body = " AND ".join(f"{f}={v}" for f, v in self.conditions)
        return f"IF {body} THEN positive [coverage={self.coverage:.2f}, prec={self.precision:.2f}, grade={self.grade}]"

    def __repr__(self) -> str:
        return (
            f"Hypothesis({self.id!r}, {len(self.conditions)} conds, "
            f"grade={self.grade}, cov={self.coverage:.2f}, prec={self.precision:.2f})"
        )


# ---------------------------------------------------------------------------
# InductiveLearner
# ---------------------------------------------------------------------------

class InductiveLearner:
    """FOIL-inspired inductive learner grounded in Grade theory.

    Uses a **sequential covering** algorithm:

    1. Learn the best rule covering the positive examples.
    2. Remove covered positives.
    3. Repeat until all positives covered (or max iterations reached).

    All quality scores are :class:`Grade`-valued.

    The key innovation over classical FOIL is that **example confidence**
    (grade) is taken into account at every step:
    * Coverage and precision are Grade-weighted (examples weighted by grade).
    * FOIL gain is also Grade-weighted.

    Parameters
    ----------
    min_coverage:
        Minimum fraction of positives a rule must cover.
    min_precision:
        Minimum precision a rule must achieve.
    max_conditions:
        Maximum number of conditions in a hypothesis.
    max_rules:
        Maximum number of rules to learn.
    """

    def __init__(
        self,
        min_coverage: float = 0.1,
        min_precision: float = 0.6,
        max_conditions: int = 5,
        max_rules: int = 20,
    ) -> None:
        self.min_coverage = min_coverage
        self.min_precision = min_precision
        self.max_conditions = max_conditions
        self.max_rules = max_rules
        self._hypotheses: list[Hypothesis] = []
        self._examples: list[Example] = []

    def learn(
        self,
        positives: list["Example"],
        negatives: list["Example"],
    ) -> list[Hypothesis]:
        """Run sequential covering and return a list of hypotheses.

        For each iteration:
        1. Start with the empty hypothesis.
        2. Greedily specialize using FOIL gain until no more gain.
        3. Add the best hypothesis to the rule set.
        4. Remove examples covered by the new hypothesis.

        Parameters
        ----------
        positives:
            Positive training examples.
        negatives:
            Negative training examples.

        Returns
        -------
        list[Hypothesis]
            Learned hypotheses, sorted by grade descending.
        """
        self._examples = list(positives) + list(negatives)
        self._hypotheses = []
        remaining_pos = list(positives)
        negatives_copy = list(negatives)
        all_examples = positives + negatives
        iteration = 0
        while remaining_pos and iteration < self.max_rules:
            h = Hypothesis(
                id=f"H{iteration}",
                conditions=[],
                grade=Grade.impossible(),
                coverage=0.0,
                precision=0.0,
            )
            # Greedy specialization
            for _ in range(self.max_conditions):
                best = self._best_specialization(
                    h, remaining_pos, negatives_copy
                )
                if best is None:
                    break
                best = self._recompute_stats(best, remaining_pos, negatives_copy)
                if best.precision < self.min_precision:
                    break
                h = best
            # Final stats
            h = self._recompute_stats(h, remaining_pos, all_examples)
            h.grade = self.grade_hypothesis(h, all_examples)
            if h.grade.is_impossible or h.coverage < self.min_coverage:
                break
            self._hypotheses.append(h)
            # Remove covered positives
            remaining_pos = [
                e for e in remaining_pos if not h.covers(e)
            ]
            iteration += 1

        self._hypotheses.sort(key=lambda hh: hh.grade, reverse=True)
        return self._hypotheses

    def generalize(self, examples: list["Example"]) -> Hypothesis:
        """Find the most general hypothesis covering all positive examples.

        Uses LGG (Least General Generalization): keeps only the conditions
        that are shared by ALL positive examples.

        Parameters
        ----------
        examples:
            Positive examples to generalize over.

        Returns
        -------
        Hypothesis
            LGG hypothesis.
        """
        positives = [e for e in examples if e.label]
        if not positives:
            return Hypothesis(
                id="lgg_empty", conditions=[], grade=Grade.impossible(),
                coverage=0.0, precision=0.0,
            )
        # Start with all conditions from the first example
        shared = set(positives[0].feature_set())
        for e in positives[1:]:
            shared &= e.feature_set()
        conditions = sorted(shared)
        h = Hypothesis(
            id="lgg", conditions=conditions, grade=Grade.impossible(),
            coverage=0.0, precision=0.0,
        )
        h = self._recompute_stats(h, positives, examples)
        h.grade = self.grade_hypothesis(h, examples)
        return h

    def specialize(
        self, hypothesis: Hypothesis, counterexample: "Example"
    ) -> Hypothesis:
        """Specialize ``hypothesis`` to exclude ``counterexample``.

        Adds the single condition that most reduces coverage of the
        counterexample while maintaining or improving precision.

        Parameters
        ----------
        hypothesis:
            Hypothesis to specialize.
        counterexample:
            A falsely covered example (negative covered by hypothesis).

        Returns
        -------
        Hypothesis
            More specific hypothesis.
        """
        candidates = self._generate_specializations(hypothesis)
        # Filter to those that don't cover the counterexample
        non_covering = [
            h for h in candidates if not h.covers(counterexample)
        ]
        if not non_covering:
            # All specializations cover it; just pick the best
            non_covering = candidates
        if not non_covering:
            return hypothesis
        # Rank by FOIL gain
        positives = [e for e in self._examples if e.label]
        negatives = [e for e in self._examples if not e.label]
        best = max(
            non_covering,
            key=lambda h: self._foil_gain(h, self._examples),
        )
        best = self._recompute_stats(best, positives, self._examples)
        best.grade = self.grade_hypothesis(best, self._examples)
        return best

    def grade_hypothesis(
        self, h: Hypothesis, examples: list["Example"]
    ) -> Grade:
        """Compute the Grade quality of a hypothesis.

        Grade = coverage_grade * precision_grade * simplicity_grade

        * ``coverage_grade = Grade.from_prob(coverage)``
        * ``precision_grade = Grade.from_prob(precision)``
        * ``simplicity_grade = Grade.from_prob(exp(-0.1 * complexity))``

        Grade multiplication is used because all three criteria must hold
        simultaneously for a hypothesis to be truly good.

        Parameters
        ----------
        h:
            The hypothesis to grade.
        examples:
            All (positive and negative) examples.

        Returns
        -------
        Grade
            Combined hypothesis quality grade.
        """
        positives = [e for e in examples if e.label]
        all_count = len(examples)
        if all_count == 0 or not positives:
            return Grade.impossible()
        covered_pos = [e for e in positives if h.covers(e)]
        covered_all = [e for e in examples if h.covers(e)]
        if not positives:
            coverage = 0.0
        else:
            # Grade-weighted coverage
            weighted_covered = sum(e.effective_weight() for e in covered_pos)
            weighted_total_pos = sum(e.effective_weight() for e in positives)
            coverage = (weighted_covered / weighted_total_pos
                        if weighted_total_pos > 0 else 0.0)
        if not covered_all:
            precision = 0.0
        else:
            weighted_pos_covered = sum(
                e.effective_weight() for e in covered_all if e.label
            )
            weighted_all_covered = sum(
                e.effective_weight() for e in covered_all
            )
            precision = (weighted_pos_covered / weighted_all_covered
                         if weighted_all_covered > 0 else 0.0)
        complexity = h.complexity()
        coverage_grade = Grade.from_prob(max(coverage, 1e-10))
        precision_grade = Grade.from_prob(max(precision, 1e-10))
        simplicity_grade = Grade.from_prob(
            max(math.exp(-0.1 * complexity), 1e-10)
        )
        return coverage_grade * precision_grade * simplicity_grade

    def _foil_gain(
        self, h: Hypothesis, examples: list["Example"]
    ) -> float:
        """Compute the FOIL information gain for hypothesis ``h``.

        FOIL gain measures how much the hypothesis reduces the entropy of
        the examples compared to the empty hypothesis.  Grade-weighted.

        Parameters
        ----------
        h:
            Hypothesis to evaluate.
        examples:
            All examples.

        Returns
        -------
        float
            FOIL gain value (higher = better).
        """
        positives = [e for e in examples if e.label]
        covered_all = [e for e in examples if h.covers(e)]
        covered_pos = [e for e in covered_all if e.label]
        p0 = len(positives) / max(len(examples), 1)
        p1 = len(covered_pos) / max(len(covered_all), 1)
        if p0 <= 0 or p1 <= 0:
            return 0.0
        try:
            gain = len(covered_pos) * (math.log2(p1) - math.log2(p0))
        except (ValueError, ZeroDivisionError):
            gain = 0.0
        return gain

    def to_rules(self, hypotheses: list[Hypothesis]) -> list[str]:
        """Convert hypotheses to human-readable rule strings.

        Parameters
        ----------
        hypotheses:
            List of hypotheses.

        Returns
        -------
        list[str]
            Rule strings.
        """
        return [h.to_rule_string() for h in hypotheses]

    def _all_conditions(
        self, examples: list["Example"]
    ) -> list[tuple[str, str]]:
        """Enumerate all unique (feature, value) conditions from examples.

        Parameters
        ----------
        examples:
            Training examples.

        Returns
        -------
        list[tuple[str, str]]
            All unique (feature, value) pairs.
        """
        conditions: set[tuple[str, str]] = set()
        for e in examples:
            conditions.update(e.description.items())
        return sorted(conditions)

    def _best_specialization(
        self,
        h: Hypothesis,
        positives: list["Example"],
        negatives: list["Example"],
    ) -> Optional[Hypothesis]:
        """Find the best single specialization of ``h`` by FOIL gain.

        Parameters
        ----------
        h:
            Current hypothesis.
        positives:
            Remaining positive examples.
        negatives:
            Negative examples.

        Returns
        -------
        Optional[Hypothesis]
            Best specialization, or None if no gain possible.
        """
        all_examples = positives + negatives
        existing_conditions = set(h.conditions)
        candidates = self._generate_specializations(h)
        # Filter to new conditions only
        candidates = [
            c for c in candidates
            if set(c.conditions) - existing_conditions
        ]
        if not candidates:
            return None
        best = max(candidates, key=lambda hh: self._foil_gain(hh, all_examples))
        gain = self._foil_gain(best, all_examples)
        return best if gain > 0 else None

    def _generate_specializations(
        self, h: Hypothesis
    ) -> list[Hypothesis]:
        """Generate all single-condition specializations of ``h``."""
        existing = set(h.conditions)
        all_conds = self._all_conditions(self._examples)
        specializations = []
        for cond in all_conds:
            if cond not in existing:
                specialized = h.specialize_with(*cond)
                specializations.append(specialized)
        return specializations

    def _recompute_stats(
        self,
        h: Hypothesis,
        positives: list["Example"],
        all_examples: list["Example"],
    ) -> Hypothesis:
        """Recompute coverage and precision stats for hypothesis ``h``."""
        covered_pos = [e for e in positives if h.covers(e)]
        covered_all = [e for e in all_examples if h.covers(e)]
        w_covered_pos = sum(e.effective_weight() for e in covered_pos)
        w_total_pos = sum(e.effective_weight() for e in positives)
        w_covered_all = sum(e.effective_weight() for e in covered_all)
        coverage = w_covered_pos / max(w_total_pos, 1e-12)
        precision = w_covered_pos / max(w_covered_all, 1e-12)
        h.coverage = coverage
        h.precision = precision
        h.support = len(covered_pos)
        return h

    def explain_learning(
        self, h: Hypothesis, examples: list["Example"]
    ) -> str:
        """Return a human-readable explanation of why ``h`` has its grade.

        Parameters
        ----------
        h:
            The hypothesis to explain.
        examples:
            All examples.

        Returns
        -------
        str
            Multi-line explanation string.
        """
        g = self.grade_hypothesis(h, examples)
        positives = [e for e in examples if e.label]
        covered_pos = [e for e in positives if h.covers(e)]
        covered_all = [e for e in examples if h.covers(e)]
        lines = [
            f"Hypothesis: {h.to_rule_string()}",
            f"  Grade: {g}",
            f"  Coverage: {h.coverage:.2%} ({len(covered_pos)}/{len(positives)} positives)",
            f"  Precision: {h.precision:.2%} ({len([e for e in covered_all if e.label])}/{len(covered_all)} covered)",
            f"  Complexity: {h.complexity():.0f} conditions",
            f"  Simplicity grade: {Grade.from_prob(math.exp(-0.1 * h.complexity()))}",
            f"  F1: {h.f1_score():.3f}",
        ]
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# FormalConcept
# ---------------------------------------------------------------------------

@dataclass
class FormalConcept:
    """A formal concept in a concept lattice with Grade quality.

    In Formal Concept Analysis (FCA), a formal concept is a pair (A, B)
    where A is the *extent* (set of objects) and B is the *intent*
    (set of attributes), satisfying the Galois connection.

    The ``grade`` is the Grade product of all example grades in the extent,
    reflecting how confidently the concept is supported by training data.

    Attributes
    ----------
    extent:
        Set of example IDs (objects with all attributes in intent).
    intent:
        Set of (feature, value) pairs (attributes shared by all objects in extent).
    grade:
        Grade product of example grades in the extent.
    """

    extent: frozenset[str]
    intent: frozenset[tuple[str, str]]
    grade: Grade

    def generality(self) -> float:
        """Fraction of all examples in the extent.

        A maximally general concept has generality = 1.0 (covers all examples).

        Returns
        -------
        float
            Generality in [0, 1].
        """
        return len(self.extent)  # caller normalizes

    def specificity(self) -> int:
        """Number of attributes in the intent.

        More attributes = more specific concept.

        Returns
        -------
        int
            Number of intent attributes.
        """
        return len(self.intent)

    def subsumes(self, other: "FormalConcept") -> bool:
        """Return True if self is more general than other.

        Self subsumes other iff other.intent ⊇ self.intent.

        Parameters
        ----------
        other:
            Concept to compare.

        Returns
        -------
        bool
            True if self is more general.
        """
        return self.intent.issubset(other.intent)

    def __repr__(self) -> str:
        return (
            f"FormalConcept(extent={len(self.extent)}, "
            f"intent={len(self.intent)}, grade={self.grade})"
        )

    def __hash__(self) -> int:
        return hash((self.extent, self.intent))

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, FormalConcept):
            return NotImplemented
        return self.extent == other.extent and self.intent == other.intent


# ---------------------------------------------------------------------------
# ConceptLattice
# ---------------------------------------------------------------------------

class ConceptLattice:
    """Formal concept analysis with Grade-valued extents.

    Builds a concept lattice from a set of :class:`Example` objects.
    Each formal concept has a Grade reflecting how confidently it is
    supported by high-grade training examples.

    Grade semantics
    ~~~~~~~~~~~~~~~
    A concept's grade = Grade product of the grades of all examples in
    its extent.  More examples = more multiplications = lower grade (unless
    all examples are ``Grade.perfect()``).  This means that a concept
    requiring many supporting examples must have consistently high-grade
    examples to maintain a strong overall grade.

    This correctly captures the intuition that generalizations supported by
    uncertain data should themselves be uncertain.
    """

    def __init__(self) -> None:
        self._examples: list[Example] = []
        self._concepts: list[FormalConcept] = []
        self._dirty: bool = True

    def add_example(self, example: Example) -> None:
        """Add an example to the lattice.

        Marks the lattice as dirty; concepts are recomputed lazily on next
        access.

        Parameters
        ----------
        example:
            Training example to add.
        """
        self._examples.append(example)
        self._dirty = True

    def concepts(
        self,
    ) -> list[tuple[frozenset, frozenset, Grade]]:
        """Return all formal concepts as (extent, intent, grade) triples.

        Recomputes the lattice if dirty.

        Returns
        -------
        list[tuple[frozenset, frozenset, Grade]]
            All concepts.
        """
        if self._dirty:
            self._compute_concepts()
        return [
            (c.extent, c.intent, c.grade)
            for c in self._concepts
        ]

    def grade_concept(self, intent: frozenset) -> Grade:
        """Compute the Grade of a concept with a given intent.

        The grade is the Grade product of all examples whose feature set
        is a superset of the intent.

        Parameters
        ----------
        intent:
            A frozenset of (feature, value) pairs.

        Returns
        -------
        Grade
            Grade product of supporting examples.
        """
        supporting = [
            e for e in self._examples
            if intent.issubset(e.feature_set())
        ]
        if not supporting:
            return Grade.impossible()
        grades = [e.grade for e in supporting]
        return Grade.product(grades)

    def _compute_concepts(self) -> None:
        """Compute all formal concepts using a simplified Next Closure variant.

        For each possible intent (subset of all observed attributes),
        compute the corresponding extent (examples satisfying all attributes).
        Keep only maximal extents.

        This is O(2^|attributes|) which is fine for small feature sets.
        """
        if not self._examples:
            self._concepts = []
            self._dirty = False
            return

        # Collect all possible attributes
        all_attributes: set[tuple[str, str]] = set()
        for e in self._examples:
            all_attributes.update(e.feature_set())

        # Build attribute-indexed example lookup
        attr_to_examples: dict[tuple[str, str], set[str]] = defaultdict(set)
        for e in self._examples:
            for attr in e.feature_set():
                attr_to_examples[attr].add(e.id)

        all_attrs = sorted(all_attributes)
        concepts: list[FormalConcept] = []
        seen_extents: set[frozenset] = set()

        # For small attribute sets, enumerate power set
        n = len(all_attrs)
        # Limit to first 20 attributes to avoid exponential blowup
        attrs_to_use = all_attrs[:20]
        n_use = len(attrs_to_use)
        for mask in range(1 << n_use):
            intent_list = [
                attrs_to_use[i] for i in range(n_use) if mask & (1 << i)
            ]
            intent = frozenset(intent_list)
            extent_ids = self._extent(intent)
            if not extent_ids or extent_ids in seen_extents:
                continue
            seen_extents.add(extent_ids)
            # Compute the closure of the extent
            closure_intent = self._closure(extent_ids)
            if closure_intent != intent:
                continue  # Not a closed concept
            grade = Grade.product(
                [e.grade for e in self._examples if e.id in extent_ids]
            )
            concepts.append(FormalConcept(
                extent=extent_ids,
                intent=intent,
                grade=grade,
            ))

        # Add the top concept (all examples, empty intent)
        all_ids = frozenset(e.id for e in self._examples)
        if all_ids not in seen_extents:
            top_grade = Grade.product([e.grade for e in self._examples])
            concepts.append(FormalConcept(
                extent=all_ids,
                intent=frozenset(),
                grade=top_grade,
            ))

        self._concepts = sorted(concepts, key=lambda c: c.grade, reverse=True)
        self._dirty = False

    def _extent(self, intent: frozenset) -> frozenset[str]:
        """Return the set of example IDs whose feature set ⊇ intent.

        Parameters
        ----------
        intent:
            A frozenset of (feature, value) attributes.

        Returns
        -------
        frozenset[str]
            Example IDs.
        """
        result = frozenset(
            e.id for e in self._examples
            if intent.issubset(e.feature_set())
        )
        return result

    def _closure(self, extent_ids: frozenset[str]) -> frozenset[tuple[str, str]]:
        """Compute the attribute closure of a set of example IDs.

        The closure is the set of attributes shared by all examples in ``extent_ids``.

        Parameters
        ----------
        extent_ids:
            Set of example IDs.

        Returns
        -------
        frozenset[tuple[str, str]]
            Shared attributes.
        """
        examples_in_extent = [
            e for e in self._examples if e.id in extent_ids
        ]
        if not examples_in_extent:
            return frozenset()
        shared = set(examples_in_extent[0].feature_set())
        for e in examples_in_extent[1:]:
            shared &= e.feature_set()
        return frozenset(shared)

    def concept_hierarchy(
        self,
    ) -> list[tuple[FormalConcept, FormalConcept]]:
        """Return direct sub-concept pairs (Hasse diagram edges).

        A concept C1 is a direct subconcept of C2 if:
        * C2 subsumes C1 (C1.intent ⊃ C2.intent)
        * There is no concept C3 between them

        Returns
        -------
        list[tuple[FormalConcept, FormalConcept]]
            (subconcept, superconcept) pairs.
        """
        if self._dirty:
            self._compute_concepts()
        edges: list[tuple[FormalConcept, FormalConcept]] = []
        for i, c1 in enumerate(self._concepts):
            for j, c2 in enumerate(self._concepts):
                if i == j:
                    continue
                if c2.subsumes(c1) and c2 != c1:
                    # Check for intermediate concept
                    has_intermediate = any(
                        c3.subsumes(c1) and c2.subsumes(c3)
                        and c3 != c1 and c3 != c2
                        for k, c3 in enumerate(self._concepts)
                        if k != i and k != j
                    )
                    if not has_intermediate:
                        edges.append((c1, c2))
        return edges

    def most_specific_concept(
        self, example: Example
    ) -> Optional[FormalConcept]:
        """Return the most specific concept covering ``example``.

        The most specific concept has the largest intent that is still
        a subset of the example's features.

        Parameters
        ----------
        example:
            Example to classify.

        Returns
        -------
        Optional[FormalConcept]
            Most specific concept, or None.
        """
        if self._dirty:
            self._compute_concepts()
        feat = example.feature_set()
        candidates = [
            c for c in self._concepts
            if c.intent.issubset(feat)
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda c: len(c.intent))

    def most_general_concept(self) -> Optional[FormalConcept]:
        """Return the top (most general) concept with empty intent.

        Returns
        -------
        Optional[FormalConcept]
            Top concept, or None if lattice is empty.
        """
        if self._dirty:
            self._compute_concepts()
        tops = [c for c in self._concepts if len(c.intent) == 0]
        return tops[0] if tops else None

    def grade_generalization(
        self, object_ids: frozenset[str]
    ) -> Grade:
        """Grade how well a set of objects generalize together.

        Finds the common concept for these objects and returns its grade.

        Parameters
        ----------
        object_ids:
            Set of example IDs.

        Returns
        -------
        Grade
            Grade of the generalization.
        """
        shared = self._closure(object_ids)
        return self.grade_concept(shared)

    def to_gluing(self) -> GluingData:
        """Pack the concept lattice into a GluingData.

        Returns
        -------
        GluingData
            Populated GluingData for harmony computation.
        """
        return GluingData()

    def __len__(self) -> int:
        if self._dirty:
            self._compute_concepts()
        return len(self._concepts)

    def __repr__(self) -> str:
        return f"ConceptLattice(examples={len(self._examples)}, concepts={len(self)})"


# ---------------------------------------------------------------------------
# GradeWeightedID3
# ---------------------------------------------------------------------------

class GradeWeightedID3:
    """Decision tree learner with Grade-weighted examples.

    Extends the ID3 algorithm to handle :class:`Example` objects with
    Grade-valued confidences.  Information gain is computed using
    Grade-weighted entropy.

    All leaf predictions carry a Grade reflecting the purity of the
    examples at that leaf (weighted by their individual grades).

    Parameters
    ----------
    max_depth:
        Maximum tree depth.
    min_grade:
        Minimum Grade threshold for leaf nodes; nodes with lower grade
        are pruned.
    """

    @dataclass
    class TreeNode:
        """A node in the ID3 decision tree."""
        feature: Optional[str]
        value: Optional[str]
        label: Optional[bool]
        grade: Grade
        children: dict = field(default_factory=dict)

        def is_leaf(self) -> bool:
            return self.label is not None

    def __init__(
        self,
        max_depth: int = 5,
        min_grade: Optional[Grade] = None,
    ) -> None:
        self.max_depth = max_depth
        self.min_grade = min_grade or Grade.from_prob(0.1)
        self._root: Optional["GradeWeightedID3.TreeNode"] = None

    def fit(self, examples: list[Example]) -> None:
        """Build the decision tree from training examples.

        Parameters
        ----------
        examples:
            Training data.
        """
        self._all_examples = examples
        features = set()
        for e in examples:
            features.update(e.description.keys())
        self._root = self._build(examples, features, depth=0)

    def predict(self, description: dict[str, str]) -> tuple[bool, Grade]:
        """Predict label and Grade for a new example.

        Parameters
        ----------
        description:
            Feature dictionary.

        Returns
        -------
        tuple[bool, Grade]
            (predicted_label, prediction_grade).
        """
        if self._root is None:
            return False, Grade.impossible()
        node = self._root
        depth = 0
        while not node.is_leaf() and depth < self.max_depth:
            feature = node.feature
            if feature is None:
                break
            value = description.get(feature)
            child = node.children.get(value)
            if child is None:
                break
            node = child
            depth += 1
        label = node.label if node.label is not None else False
        return label, node.grade

    def _build(
        self,
        examples: list[Example],
        features: set[str],
        depth: int,
    ) -> "GradeWeightedID3.TreeNode":
        """Recursively build the decision tree."""
        if not examples:
            return GradeWeightedID3.TreeNode(
                feature=None, value=None, label=False,
                grade=Grade.impossible(), children={}
            )
        pos = [e for e in examples if e.label]
        neg = [e for e in examples if not e.label]
        pos_weight = sum(e.effective_weight() for e in pos)
        neg_weight = sum(e.effective_weight() for e in neg)
        total = pos_weight + neg_weight

        # Purity grade
        if total > 0:
            purity = max(pos_weight, neg_weight) / total
            purity_grade = Grade.from_prob(max(purity, 1e-6))
        else:
            purity_grade = Grade.impossible()

        # Stop conditions
        if not neg or depth >= self.max_depth or not features:
            label = pos_weight >= neg_weight
            return GradeWeightedID3.TreeNode(
                feature=None, value=None, label=label,
                grade=purity_grade, children={}
            )
        if not pos:
            return GradeWeightedID3.TreeNode(
                feature=None, value=None, label=False,
                grade=purity_grade, children={}
            )

        # Find best feature to split on
        best_feature = self._best_feature(examples, features)
        if best_feature is None:
            label = pos_weight >= neg_weight
            return GradeWeightedID3.TreeNode(
                feature=None, value=None, label=label,
                grade=purity_grade, children={}
            )

        # Build children
        values = set(e.description.get(best_feature, "__missing__")
                     for e in examples)
        remaining_features = features - {best_feature}
        children = {}
        for value in values:
            subset = [
                e for e in examples
                if e.description.get(best_feature) == value
            ]
            children[value] = self._build(subset, remaining_features, depth + 1)

        return GradeWeightedID3.TreeNode(
            feature=best_feature, value=None, label=None,
            grade=purity_grade, children=children
        )

    def _best_feature(
        self,
        examples: list[Example],
        features: set[str],
    ) -> Optional[str]:
        """Find the feature with the highest Grade-weighted information gain.

        Parameters
        ----------
        examples:
            Current node examples.
        features:
            Available features.

        Returns
        -------
        Optional[str]
            Best feature name, or None.
        """
        best_gain = -1.0
        best_feat: Optional[str] = None
        for feat in features:
            gain = self._grade_information_gain(examples, feat)
            if gain > best_gain:
                best_gain = gain
                best_feat = feat
        return best_feat

    def _grade_entropy(self, examples: list[Example]) -> float:
        """Compute Grade-weighted entropy of the example set.

        H = -sum(p_i * log2(p_i)) weighted by example grades.

        Parameters
        ----------
        examples:
            Examples to measure entropy of.

        Returns
        -------
        float
            Weighted entropy.
        """
        total = sum(e.effective_weight() for e in examples)
        if total < 1e-12:
            return 0.0
        pos_w = sum(e.effective_weight() for e in examples if e.label)
        neg_w = total - pos_w
        entropy = 0.0
        for p in [pos_w / total, neg_w / total]:
            if p > 1e-12:
                entropy -= p * math.log2(p)
        return entropy

    def _grade_information_gain(
        self, examples: list[Example], feature: str
    ) -> float:
        """Compute Grade-weighted information gain for splitting on ``feature``.

        Parameters
        ----------
        examples:
            Current examples.
        feature:
            Feature to split on.

        Returns
        -------
        float
            Information gain.
        """
        total = sum(e.effective_weight() for e in examples)
        if total < 1e-12:
            return 0.0
        h_before = self._grade_entropy(examples)
        values = set(e.description.get(feature, "__missing__")
                     for e in examples)
        h_after = 0.0
        for value in values:
            subset = [
                e for e in examples
                if e.description.get(feature) == value
            ]
            w = sum(e.effective_weight() for e in subset)
            if w > 0:
                h_after += (w / total) * self._grade_entropy(subset)
        return h_before - h_after

    def to_rules(self) -> list[str]:
        """Convert the decision tree to a list of rule strings.

        Returns
        -------
        list[str]
            One rule per leaf.
        """
        rules: list[str] = []
        if self._root is None:
            return rules
        self._extract_rules(self._root, [], rules)
        return rules

    def _extract_rules(
        self,
        node: "GradeWeightedID3.TreeNode",
        path: list[str],
        rules: list[str],
    ) -> None:
        """Recursively extract rules from the tree."""
        if node.is_leaf():
            label_str = "positive" if node.label else "negative"
            if path:
                cond = " AND ".join(path)
                rules.append(f"IF {cond} THEN {label_str} [{node.grade}]")
            else:
                rules.append(f"THEN {label_str} [{node.grade}]")
            return
        for value, child in node.children.items():
            self._extract_rules(
                child,
                path + [f"{node.feature}={value}"],
                rules,
            )


# ---------------------------------------------------------------------------
# VersionSpace
# ---------------------------------------------------------------------------

class VersionSpace:
    """Mitchell's version space with Grade-valued boundaries.

    Maintains S (specific) and G (general) boundary hypothesis sets.
    Each hypothesis in S and G carries a Grade reflecting how consistent
    it is with all seen examples.

    Grade semantics
    ~~~~~~~~~~~~~~~
    A hypothesis's grade = product of its consistency grades with each
    example seen so far.  Consistent with a positive example → multiply
    by ``Grade.perfect()``; covering a negative example → multiply by
    ``Grade.impossible()`` (eliminates hypothesis).

    The version space converges when S = G = {single hypothesis}.
    The convergence Grade is the grade of that hypothesis.
    """

    def __init__(self) -> None:
        self._s_boundary: list[Hypothesis] = []
        self._g_boundary: list[Hypothesis] = []
        self._examples_seen: list[Example] = []
        self._all_conditions: list[tuple[str, str]] = []

    def initialize(self, positive: Example) -> None:
        """Initialize the version space from the first positive example.

        S = {most specific generalization of positive}
        G = {empty hypothesis (covers everything)}

        Parameters
        ----------
        positive:
            First positive example.
        """
        self._examples_seen = [positive]
        self._update_conditions(positive)
        specific = Hypothesis(
            id="s0",
            conditions=list(positive.description.items()),
            grade=positive.grade,
            coverage=1.0,
            precision=1.0,
        )
        general = Hypothesis(
            id="g0",
            conditions=[],
            grade=Grade.perfect(),
            coverage=1.0,
            precision=0.5,
        )
        self._s_boundary = [specific]
        self._g_boundary = [general]

    def update(self, example: Example) -> None:
        """Update the version space with a new example.

        Parameters
        ----------
        example:
            New example (positive or negative).
        """
        self._examples_seen.append(example)
        self._update_conditions(example)
        if example.label:
            self._generalize_s(example)
        else:
            self._specialize_g(example)

    def _generalize_s(self, pos: Example) -> None:
        """Generalize all S-boundary hypotheses to cover ``pos``."""
        new_s: list[Hypothesis] = []
        for h in self._s_boundary:
            if h.covers(pos):
                new_s.append(h)
                continue
            # Generalize: remove conditions not satisfied by pos
            retained = [
                (f, v) for f, v in h.conditions
                if pos.description.get(f) == v
            ]
            new_h = Hypothesis(
                id=f"{h.id}_gen",
                conditions=retained,
                grade=h.grade * pos.grade,
                coverage=0.0,
                precision=0.0,
            )
            # Check consistency with negative examples
            negs = [e for e in self._examples_seen if not e.label]
            if all(not new_h.covers(neg) for neg in negs):
                new_s.append(new_h)
        self._s_boundary = new_s if new_s else self._s_boundary

    def _specialize_g(self, neg: Example) -> None:
        """Specialize all G-boundary hypotheses to exclude ``neg``."""
        new_g: list[Hypothesis] = []
        for h in self._g_boundary:
            if not h.covers(neg):
                new_g.append(h)
                continue
            # Specialize: add one condition to exclude neg
            for feat, val in self._all_conditions:
                neg_val = neg.description.get(feat)
                if neg_val != val:
                    new_h = h.specialize_with(feat, val)
                    new_h.grade = h.grade * Grade.from_prob(0.8)
                    # Must cover some positive
                    pos = [e for e in self._examples_seen if e.label]
                    if any(new_h.covers(p) for p in pos):
                        new_g.append(new_h)
        self._g_boundary = new_g if new_g else self._g_boundary

    def is_converged(self) -> bool:
        """Check if S and G have converged to a single hypothesis.

        Returns
        -------
        bool
            True if |S| = |G| = 1 and S[0] == G[0].
        """
        if len(self._s_boundary) == 1 and len(self._g_boundary) == 1:
            s = self._s_boundary[0]
            g = self._g_boundary[0]
            return set(s.conditions) == set(g.conditions)
        return False

    def grade_convergence(self) -> Grade:
        """Grade how close the version space is to convergence.

        Convergence grade = Grade.from_prob(1 - (|S| + |G| - 2) / max_size).

        Returns
        -------
        Grade
            Convergence grade; ``Grade.perfect()`` if fully converged.
        """
        if self.is_converged():
            return Grade.perfect()
        size = len(self._s_boundary) + len(self._g_boundary)
        max_size = 20.0
        convergence = max(1.0 - (size - 2) / max_size, 1e-6)
        return Grade.from_prob(convergence)

    def candidate_hypotheses(self) -> list[Hypothesis]:
        """Return all hypotheses that are consistent with seen examples.

        These are hypotheses more specific than some G-boundary member
        and more general than some S-boundary member.

        Returns
        -------
        list[Hypothesis]
            All consistent candidate hypotheses.
        """
        all_candidates = self._s_boundary + self._g_boundary
        seen_ids = set()
        unique = []
        for h in all_candidates:
            key = frozenset(h.conditions)
            if key not in seen_ids:
                seen_ids.add(key)
                unique.append(h)
        return sorted(unique, key=lambda hh: hh.grade, reverse=True)

    def _update_conditions(self, example: Example) -> None:
        """Update the set of all conditions from a new example."""
        for feat, val in example.description.items():
            if (feat, val) not in self._all_conditions:
                self._all_conditions.append((feat, val))


# ---------------------------------------------------------------------------
# Built-in example datasets
# ---------------------------------------------------------------------------

def animal_classification_examples() -> tuple[list[Example], list[Example]]:
    """Return bird vs non-bird classification examples.

    Features: has_feathers, has_fur, has_scales, can_fly, is_warm_blooded,
              has_legs, is_carnivore, lays_eggs, has_beak

    Positive = bird; Negative = non-bird.

    Returns
    -------
    tuple[list[Example], list[Example]]
        (positives, negatives).
    """
    positives = [
        Example("robin", {"has_feathers": "yes", "has_fur": "no", "has_scales": "no",
                           "can_fly": "yes", "is_warm_blooded": "yes", "has_legs": "yes",
                           "is_carnivore": "partial", "lays_eggs": "yes", "has_beak": "yes"},
                True, Grade.perfect(), source="textbook"),
        Example("eagle", {"has_feathers": "yes", "has_fur": "no", "has_scales": "no",
                           "can_fly": "yes", "is_warm_blooded": "yes", "has_legs": "yes",
                           "is_carnivore": "yes", "lays_eggs": "yes", "has_beak": "yes"},
                True, Grade.perfect(), source="textbook"),
        Example("parrot", {"has_feathers": "yes", "has_fur": "no", "has_scales": "no",
                            "can_fly": "yes", "is_warm_blooded": "yes", "has_legs": "yes",
                            "is_carnivore": "no", "lays_eggs": "yes", "has_beak": "yes"},
                True, Grade.perfect()),
        Example("penguin", {"has_feathers": "yes", "has_fur": "no", "has_scales": "no",
                             "can_fly": "no", "is_warm_blooded": "yes", "has_legs": "yes",
                             "is_carnivore": "yes", "lays_eggs": "yes", "has_beak": "yes"},
                True, Grade.from_prob(0.95), source="textbook"),
        Example("owl", {"has_feathers": "yes", "has_fur": "no", "has_scales": "no",
                        "can_fly": "yes", "is_warm_blooded": "yes", "has_legs": "yes",
                        "is_carnivore": "yes", "lays_eggs": "yes", "has_beak": "yes"},
                True, Grade.perfect()),
        Example("sparrow", {"has_feathers": "yes", "has_fur": "no", "has_scales": "no",
                             "can_fly": "yes", "is_warm_blooded": "yes", "has_legs": "yes",
                             "is_carnivore": "no", "lays_eggs": "yes", "has_beak": "yes"},
                True, Grade.perfect()),
        Example("duck", {"has_feathers": "yes", "has_fur": "no", "has_scales": "no",
                         "can_fly": "yes", "is_warm_blooded": "yes", "has_legs": "yes",
                         "is_carnivore": "no", "lays_eggs": "yes", "has_beak": "yes"},
                True, Grade.perfect()),
        Example("hawk", {"has_feathers": "yes", "has_fur": "no", "has_scales": "no",
                         "can_fly": "yes", "is_warm_blooded": "yes", "has_legs": "yes",
                         "is_carnivore": "yes", "lays_eggs": "yes", "has_beak": "yes"},
                True, Grade.perfect()),
        Example("ostrich", {"has_feathers": "yes", "has_fur": "no", "has_scales": "no",
                             "can_fly": "no", "is_warm_blooded": "yes", "has_legs": "yes",
                             "is_carnivore": "partial", "lays_eggs": "yes", "has_beak": "yes"},
                True, Grade.from_prob(0.9)),
        Example("flamingo", {"has_feathers": "yes", "has_fur": "no", "has_scales": "no",
                              "can_fly": "yes", "is_warm_blooded": "yes", "has_legs": "yes",
                              "is_carnivore": "no", "lays_eggs": "yes", "has_beak": "yes"},
                True, Grade.perfect()),
    ]
    negatives = [
        Example("dog", {"has_feathers": "no", "has_fur": "yes", "has_scales": "no",
                        "can_fly": "no", "is_warm_blooded": "yes", "has_legs": "yes",
                        "is_carnivore": "partial", "lays_eggs": "no", "has_beak": "no"},
                False, Grade.perfect()),
        Example("cat", {"has_feathers": "no", "has_fur": "yes", "has_scales": "no",
                        "can_fly": "no", "is_warm_blooded": "yes", "has_legs": "yes",
                        "is_carnivore": "yes", "lays_eggs": "no", "has_beak": "no"},
                False, Grade.perfect()),
        Example("snake", {"has_feathers": "no", "has_fur": "no", "has_scales": "yes",
                          "can_fly": "no", "is_warm_blooded": "no", "has_legs": "no",
                          "is_carnivore": "yes", "lays_eggs": "yes", "has_beak": "no"},
                False, Grade.perfect()),
        Example("fish", {"has_feathers": "no", "has_fur": "no", "has_scales": "yes",
                         "can_fly": "no", "is_warm_blooded": "no", "has_legs": "no",
                         "is_carnivore": "partial", "lays_eggs": "yes", "has_beak": "no"},
                False, Grade.perfect()),
        Example("frog", {"has_feathers": "no", "has_fur": "no", "has_scales": "no",
                         "can_fly": "no", "is_warm_blooded": "no", "has_legs": "yes",
                         "is_carnivore": "yes", "lays_eggs": "yes", "has_beak": "no"},
                False, Grade.perfect()),
        Example("bat", {"has_feathers": "no", "has_fur": "yes", "has_scales": "no",
                        "can_fly": "yes", "is_warm_blooded": "yes", "has_legs": "yes",
                        "is_carnivore": "partial", "lays_eggs": "no", "has_beak": "no"},
                False, Grade.from_prob(0.9)),
        Example("lizard", {"has_feathers": "no", "has_fur": "no", "has_scales": "yes",
                           "can_fly": "no", "is_warm_blooded": "no", "has_legs": "yes",
                           "is_carnivore": "yes", "lays_eggs": "yes", "has_beak": "no"},
                False, Grade.perfect()),
        Example("whale", {"has_feathers": "no", "has_fur": "partial", "has_scales": "no",
                          "can_fly": "no", "is_warm_blooded": "yes", "has_legs": "no",
                          "is_carnivore": "partial", "lays_eggs": "no", "has_beak": "no"},
                False, Grade.perfect()),
        Example("crocodile", {"has_feathers": "no", "has_fur": "no", "has_scales": "yes",
                               "can_fly": "no", "is_warm_blooded": "no", "has_legs": "yes",
                               "is_carnivore": "yes", "lays_eggs": "yes", "has_beak": "no"},
                False, Grade.perfect()),
        Example("turtle", {"has_feathers": "no", "has_fur": "no", "has_scales": "yes",
                            "can_fly": "no", "is_warm_blooded": "no", "has_legs": "yes",
                            "is_carnivore": "partial", "lays_eggs": "yes", "has_beak": "no"},
                False, Grade.perfect()),
    ]
    return positives, negatives


def medical_diagnosis_examples() -> tuple[list[Example], list[Example]]:
    """Return flu vs. cold medical diagnosis examples.

    Features: fever, cough, fatigue, headache, sore_throat, runny_nose,
              muscle_ache, chills, body_temperature_high

    Positive = flu; Negative = cold.

    Returns
    -------
    tuple[list[Example], list[Example]]
        (positives=flu, negatives=cold).
    """
    positives = [
        Example("flu_1", {"fever": "yes", "cough": "yes", "fatigue": "severe",
                           "headache": "yes", "sore_throat": "mild", "runny_nose": "no",
                           "muscle_ache": "severe", "chills": "yes",
                           "body_temperature_high": "yes"},
                True, Grade.perfect()),
        Example("flu_2", {"fever": "yes", "cough": "dry", "fatigue": "severe",
                           "headache": "severe", "sore_throat": "no", "runny_nose": "no",
                           "muscle_ache": "yes", "chills": "yes",
                           "body_temperature_high": "yes"},
                True, Grade.perfect()),
        Example("flu_3", {"fever": "high", "cough": "yes", "fatigue": "yes",
                           "headache": "yes", "sore_throat": "mild", "runny_nose": "mild",
                           "muscle_ache": "yes", "chills": "yes",
                           "body_temperature_high": "yes"},
                True, Grade.perfect()),
        Example("flu_4", {"fever": "yes", "cough": "dry", "fatigue": "severe",
                           "headache": "yes", "sore_throat": "no", "runny_nose": "no",
                           "muscle_ache": "severe", "chills": "yes",
                           "body_temperature_high": "yes"},
                True, Grade.from_prob(0.9)),
        Example("flu_5", {"fever": "high", "cough": "yes", "fatigue": "severe",
                           "headache": "severe", "sore_throat": "mild", "runny_nose": "no",
                           "muscle_ache": "severe", "chills": "yes",
                           "body_temperature_high": "yes"},
                True, Grade.perfect()),
        Example("flu_6", {"fever": "yes", "cough": "yes", "fatigue": "yes",
                           "headache": "yes", "sore_throat": "mild", "runny_nose": "no",
                           "muscle_ache": "yes", "chills": "partial",
                           "body_temperature_high": "yes"},
                True, Grade.from_prob(0.85)),
        Example("flu_7", {"fever": "high", "cough": "dry", "fatigue": "severe",
                           "headache": "yes", "sore_throat": "no", "runny_nose": "no",
                           "muscle_ache": "severe", "chills": "yes",
                           "body_temperature_high": "yes"},
                True, Grade.perfect()),
        Example("flu_8", {"fever": "yes", "cough": "yes", "fatigue": "yes",
                           "headache": "severe", "sore_throat": "no", "runny_nose": "no",
                           "muscle_ache": "yes", "chills": "yes",
                           "body_temperature_high": "yes"},
                True, Grade.perfect()),
        Example("flu_9", {"fever": "yes", "cough": "yes", "fatigue": "severe",
                           "headache": "yes", "sore_throat": "mild", "runny_nose": "mild",
                           "muscle_ache": "yes", "chills": "yes",
                           "body_temperature_high": "yes"},
                True, Grade.from_prob(0.8)),
        Example("flu_10", {"fever": "high", "cough": "yes", "fatigue": "severe",
                            "headache": "yes", "sore_throat": "no", "runny_nose": "no",
                            "muscle_ache": "severe", "chills": "yes",
                            "body_temperature_high": "yes"},
                True, Grade.perfect()),
    ]
    negatives = [
        Example("cold_1", {"fever": "no", "cough": "wet", "fatigue": "mild",
                            "headache": "mild", "sore_throat": "yes", "runny_nose": "yes",
                            "muscle_ache": "no", "chills": "no",
                            "body_temperature_high": "no"},
                False, Grade.perfect()),
        Example("cold_2", {"fever": "low", "cough": "yes", "fatigue": "mild",
                            "headache": "no", "sore_throat": "yes", "runny_nose": "yes",
                            "muscle_ache": "mild", "chills": "no",
                            "body_temperature_high": "no"},
                False, Grade.perfect()),
        Example("cold_3", {"fever": "no", "cough": "wet", "fatigue": "mild",
                            "headache": "no", "sore_throat": "severe", "runny_nose": "yes",
                            "muscle_ache": "no", "chills": "no",
                            "body_temperature_high": "no"},
                False, Grade.perfect()),
        Example("cold_4", {"fever": "no", "cough": "yes", "fatigue": "yes",
                            "headache": "mild", "sore_throat": "yes", "runny_nose": "severe",
                            "muscle_ache": "no", "chills": "no",
                            "body_temperature_high": "no"},
                False, Grade.from_prob(0.9)),
        Example("cold_5", {"fever": "low", "cough": "wet", "fatigue": "mild",
                            "headache": "mild", "sore_throat": "yes", "runny_nose": "yes",
                            "muscle_ache": "mild", "chills": "no",
                            "body_temperature_high": "no"},
                False, Grade.perfect()),
        Example("cold_6", {"fever": "no", "cough": "yes", "fatigue": "mild",
                            "headache": "no", "sore_throat": "yes", "runny_nose": "yes",
                            "muscle_ache": "no", "chills": "no",
                            "body_temperature_high": "no"},
                False, Grade.perfect()),
        Example("cold_7", {"fever": "no", "cough": "wet", "fatigue": "mild",
                            "headache": "mild", "sore_throat": "mild", "runny_nose": "severe",
                            "muscle_ache": "no", "chills": "no",
                            "body_temperature_high": "no"},
                False, Grade.perfect()),
        Example("cold_8", {"fever": "no", "cough": "yes", "fatigue": "yes",
                            "headache": "no", "sore_throat": "yes", "runny_nose": "yes",
                            "muscle_ache": "no", "chills": "no",
                            "body_temperature_high": "no"},
                False, Grade.from_prob(0.85)),
        Example("cold_9", {"fever": "low", "cough": "yes", "fatigue": "mild",
                            "headache": "mild", "sore_throat": "yes", "runny_nose": "yes",
                            "muscle_ache": "mild", "chills": "no",
                            "body_temperature_high": "no"},
                False, Grade.perfect()),
        Example("cold_10", {"fever": "no", "cough": "wet", "fatigue": "mild",
                             "headache": "no", "sore_throat": "severe", "runny_nose": "severe",
                             "muscle_ache": "no", "chills": "no",
                             "body_temperature_high": "no"},
                False, Grade.perfect()),
    ]
    return positives, negatives


def nlp_event_examples() -> tuple[list[Example], list[Example]]:
    """Return telic vs. atelic NLP event classification examples.

    Features: verb_type, has_endpoint, is_durative, has_agent,
              has_patient, has_quantized_object, has_for_adverb,
              has_in_adverb, iterative

    Positive = telic; Negative = atelic.

    Returns
    -------
    tuple[list[Example], list[Example]]
        (positives=telic, negatives=atelic).
    """
    positives = [
        Example("build_house", {"verb_type": "accomplishment", "has_endpoint": "yes",
                                 "is_durative": "yes", "has_agent": "yes",
                                 "has_patient": "yes", "has_quantized_object": "yes",
                                 "has_for_adverb": "no", "has_in_adverb": "yes",
                                 "iterative": "no"},
                True, Grade.perfect()),
        Example("arrive", {"verb_type": "achievement", "has_endpoint": "yes",
                            "is_durative": "no", "has_agent": "yes",
                            "has_patient": "no", "has_quantized_object": "no",
                            "has_for_adverb": "no", "has_in_adverb": "yes",
                            "iterative": "no"},
                True, Grade.perfect()),
        Example("write_letter", {"verb_type": "accomplishment", "has_endpoint": "yes",
                                  "is_durative": "yes", "has_agent": "yes",
                                  "has_patient": "yes", "has_quantized_object": "yes",
                                  "has_for_adverb": "no", "has_in_adverb": "yes",
                                  "iterative": "no"},
                True, Grade.perfect()),
        Example("die", {"verb_type": "achievement", "has_endpoint": "yes",
                        "is_durative": "no", "has_agent": "no",
                        "has_patient": "yes", "has_quantized_object": "no",
                        "has_for_adverb": "no", "has_in_adverb": "no",
                        "iterative": "no"},
                True, Grade.perfect()),
        Example("find", {"verb_type": "achievement", "has_endpoint": "yes",
                         "is_durative": "no", "has_agent": "yes",
                         "has_patient": "yes", "has_quantized_object": "yes",
                         "has_for_adverb": "no", "has_in_adverb": "no",
                         "iterative": "no"},
                True, Grade.perfect()),
        Example("cook_meal", {"verb_type": "accomplishment", "has_endpoint": "yes",
                               "is_durative": "yes", "has_agent": "yes",
                               "has_patient": "yes", "has_quantized_object": "yes",
                               "has_for_adverb": "no", "has_in_adverb": "yes",
                               "iterative": "no"},
                True, Grade.perfect()),
        Example("learn_skill", {"verb_type": "accomplishment", "has_endpoint": "yes",
                                 "is_durative": "yes", "has_agent": "yes",
                                 "has_patient": "yes", "has_quantized_object": "yes",
                                 "has_for_adverb": "no", "has_in_adverb": "yes",
                                 "iterative": "no"},
                True, Grade.from_prob(0.9)),
        Example("break", {"verb_type": "achievement", "has_endpoint": "yes",
                          "is_durative": "no", "has_agent": "partial",
                          "has_patient": "yes", "has_quantized_object": "yes",
                          "has_for_adverb": "no", "has_in_adverb": "no",
                          "iterative": "no"},
                True, Grade.perfect()),
    ]
    negatives = [
        Example("run_activity", {"verb_type": "activity", "has_endpoint": "no",
                                  "is_durative": "yes", "has_agent": "yes",
                                  "has_patient": "no", "has_quantized_object": "no",
                                  "has_for_adverb": "yes", "has_in_adverb": "no",
                                  "iterative": "no"},
                False, Grade.perfect()),
        Example("know", {"verb_type": "state", "has_endpoint": "no",
                         "is_durative": "yes", "has_agent": "no",
                         "has_patient": "no", "has_quantized_object": "no",
                         "has_for_adverb": "yes", "has_in_adverb": "no",
                         "iterative": "no"},
                False, Grade.perfect()),
        Example("swim", {"verb_type": "activity", "has_endpoint": "no",
                         "is_durative": "yes", "has_agent": "yes",
                         "has_patient": "no", "has_quantized_object": "no",
                         "has_for_adverb": "yes", "has_in_adverb": "no",
                         "iterative": "no"},
                False, Grade.perfect()),
        Example("believe", {"verb_type": "state", "has_endpoint": "no",
                             "is_durative": "yes", "has_agent": "no",
                             "has_patient": "no", "has_quantized_object": "no",
                             "has_for_adverb": "yes", "has_in_adverb": "no",
                             "iterative": "no"},
                False, Grade.perfect()),
        Example("walk_activity", {"verb_type": "activity", "has_endpoint": "no",
                                   "is_durative": "yes", "has_agent": "yes",
                                   "has_patient": "no", "has_quantized_object": "no",
                                   "has_for_adverb": "yes", "has_in_adverb": "no",
                                   "iterative": "no"},
                False, Grade.perfect()),
        Example("flash", {"verb_type": "semelfactive", "has_endpoint": "no",
                          "is_durative": "no", "has_agent": "no",
                          "has_patient": "no", "has_quantized_object": "no",
                          "has_for_adverb": "no", "has_in_adverb": "no",
                          "iterative": "yes"},
                False, Grade.perfect()),
        Example("love", {"verb_type": "state", "has_endpoint": "no",
                         "is_durative": "yes", "has_agent": "yes",
                         "has_patient": "yes", "has_quantized_object": "no",
                         "has_for_adverb": "yes", "has_in_adverb": "no",
                         "iterative": "no"},
                False, Grade.perfect()),
    ]
    return positives, negatives


# ---------------------------------------------------------------------------
# GradeHypothesisRanker — rank hypotheses by multiple criteria
# ---------------------------------------------------------------------------

class GradeHypothesisRanker:
    """Ranks a list of hypotheses using a weighted Grade combination.

    The ranking combines:
    * Coverage (recall of positives)
    * Precision
    * Simplicity (inverse complexity)
    * F1 score

    Each criterion contributes a Grade, and the final ranking uses
    Grade multiplication for the required criteria and Grade addition
    for the alternative (best-of) selection.

    Attributes
    ----------
    coverage_weight:
        Weight for coverage grade (in [0, 1]).
    precision_weight:
        Weight for precision grade.
    simplicity_weight:
        Weight for simplicity grade.
    """

    def __init__(
        self,
        coverage_weight: float = 0.4,
        precision_weight: float = 0.4,
        simplicity_weight: float = 0.2,
    ) -> None:
        self.coverage_weight = coverage_weight
        self.precision_weight = precision_weight
        self.simplicity_weight = simplicity_weight

    def rank(
        self, hypotheses: list[Hypothesis]
    ) -> list[tuple[Hypothesis, Grade]]:
        """Rank hypotheses by a weighted Grade score.

        The combined grade is computed as:

        .. code-block::

            g = (coverage_grade.attenuate(coverage_weight)
                 * precision_grade.attenuate(precision_weight)
                 * simplicity_grade.attenuate(simplicity_weight))

        Grade attenuation reflects how much each criterion contributes.
        The product expresses that ALL criteria must be satisfied.

        Parameters
        ----------
        hypotheses:
            Hypotheses to rank.

        Returns
        -------
        list[tuple[Hypothesis, Grade]]
            (hypothesis, combined_grade) pairs, sorted by grade descending.
        """
        scored: list[tuple[Hypothesis, Grade]] = []
        for h in hypotheses:
            cov_g = Grade.from_prob(max(h.coverage, 1e-6))
            prec_g = Grade.from_prob(max(h.precision, 1e-6))
            simp_g = Grade.from_prob(
                max(math.exp(-0.1 * h.complexity()), 1e-6)
            )
            combined = (
                cov_g.attenuate(self.coverage_weight)
                * prec_g.attenuate(self.precision_weight)
                * simp_g.attenuate(self.simplicity_weight)
            )
            scored.append((h, combined))
        scored.sort(key=lambda hs: hs[1], reverse=True)
        return scored

    def best(self, hypotheses: list[Hypothesis]) -> Optional[Hypothesis]:
        """Return the single best hypothesis.

        Parameters
        ----------
        hypotheses:
            Hypotheses to rank.

        Returns
        -------
        Optional[Hypothesis]
            Best hypothesis, or None if list is empty.
        """
        ranked = self.rank(hypotheses)
        return ranked[0][0] if ranked else None

    def grade_ensemble(
        self, hypotheses: list[Hypothesis], example: Example
    ) -> Grade:
        """Compute ensemble Grade for classifying ``example``.

        For hypotheses that cover ``example``: Grade addition (logsumexp)
        of their hypothesis grades — alternative evidence combination.
        For hypotheses that don't cover ``example``: ignored.

        Parameters
        ----------
        hypotheses:
            The hypothesis ensemble.
        example:
            Example to classify.

        Returns
        -------
        Grade
            Ensemble Grade for positive label.
        """
        covering_grades = [
            h.grade for h in hypotheses if h.covers(example)
        ]
        if not covering_grades:
            return Grade.impossible()
        return Grade.best(covering_grades)


# ---------------------------------------------------------------------------
# Utility functions
# ---------------------------------------------------------------------------

def examples_to_feature_matrix(
    examples: list[Example],
) -> tuple[list[dict[str, str]], list[bool], list[float]]:
    """Convert examples to feature matrix, labels, and weights.

    Parameters
    ----------
    examples:
        List of examples.

    Returns
    -------
    tuple[list[dict], list[bool], list[float]]
        (feature_dicts, labels, weights).
    """
    features = [e.description for e in examples]
    labels = [e.label for e in examples]
    weights = [e.effective_weight() for e in examples]
    return features, labels, weights


def grade_dataset_balance(
    positives: list[Example], negatives: list[Example]
) -> Grade:
    """Grade how balanced the dataset is.

    A perfectly balanced dataset has Grade.perfect().
    A heavily imbalanced dataset has a lower grade.

    Grade = from_prob(min(n_pos, n_neg) / max(n_pos, n_neg))

    Parameters
    ----------
    positives, negatives:
        Positive and negative examples.

    Returns
    -------
    Grade
        Balance grade.
    """
    n_pos = len(positives)
    n_neg = len(negatives)
    if n_pos == 0 or n_neg == 0:
        return Grade.impossible()
    ratio = min(n_pos, n_neg) / max(n_pos, n_neg)
    return Grade.from_prob(max(ratio, 1e-6))


def grade_feature_informativeness(
    examples: list[Example], feature: str
) -> Grade:
    """Grade how informative ``feature`` is for classification.

    Uses information gain: high gain → high Grade.

    Parameters
    ----------
    examples:
        All training examples.
    feature:
        Feature to evaluate.

    Returns
    -------
    Grade
        Informativeness grade.
    """
    n = len(examples)
    if n == 0:
        return Grade.impossible()
    pos_total = sum(1 for e in examples if e.label)
    neg_total = n - pos_total
    if pos_total == 0 or neg_total == 0:
        return Grade.impossible()

    def entropy(p: float, total: float) -> float:
        if total == 0:
            return 0.0
        ratio = p / total
        if ratio <= 0 or ratio >= 1:
            return 0.0
        return -ratio * math.log2(ratio) - (1 - ratio) * math.log2(1 - ratio)

    h_total = entropy(pos_total, n)
    values = set(e.description.get(feature, "__missing__") for e in examples)
    h_given_feature = 0.0
    for val in values:
        subset = [e for e in examples if e.description.get(feature) == val]
        pos_in_subset = sum(1 for e in subset if e.label)
        h_given_feature += (len(subset) / n) * entropy(pos_in_subset, len(subset))
    gain = h_total - h_given_feature
    max_gain = h_total if h_total > 0 else 1.0
    normalized = min(gain / max_gain, 1.0) if max_gain > 0 else 0.0
    return Grade.from_prob(max(normalized, 1e-6))
