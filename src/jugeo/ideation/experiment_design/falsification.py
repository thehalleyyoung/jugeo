from __future__ import annotations

"""Popperian Falsification for Ideation Theory Hypotheses — Chapter 53
======================================================================

This module implements a computational framework for applying Karl Popper's
principle of falsification to ideation-theory hypotheses.  A scientific
hypothesis is considered meaningful *only* if it is in principle falsifiable —
i.e. there exists at least one possible observation that would refute it.

The falsifiability criterion is stated formally as:

    A hypothesis H is falsified if ∃ test condition c such that
    P(outcome | H) << P(outcome | ¬H)

Concretely, if the probability of observing a given outcome under H is far
smaller than under the negation of H, then that outcome constitutes a
falsifying instance.

The workflow implemented here is:

    1. **Parse** a natural-language hypothesis into structured components
       (subject, predicate, quantifier, keywords) using
       :class:`HypothesisParser`.
    2. **Design** a battery of adversarial test conditions using
       :class:`FalsificationDesigner` and :class:`AdversarialCaseGenerator`.
    3. **Execute** each condition and record whether the observed outcome
       contradicts the hypothesis' predictions with
       :meth:`FalsificationDesigner.attempt_falsification`.
    4. **Analyse** the resulting evidence with :class:`ConclusivenessAnalyzer`
       to determine whether the hypothesis survives, is falsified, or requires
       more testing.
    5. **Persist** aggregated results in :class:`FalsificationRecord` for
       longitudinal tracking across multiple experimental campaigns.

Design Decisions
----------------
- No external dependencies: all logic relies on :mod:`re`, :mod:`math`,
  :mod:`random`, and :mod:`uuid` from the standard library.
- :class:`FalsificationTest` lives in this module (not ``models.py``) because
  it couples tightly to the falsification lifecycle and not to the broader
  experiment-design pipeline.
- Keyword extraction uses a curated stop-word list rather than a third-party
  NLP tokeniser to remain dependency-free.

References
----------
- Popper, K. (1934). *Logik der Forschung* (The Logic of Scientific Discovery).
- Lakatos, I. (1978). *The Methodology of Scientific Research Programmes*.
- Mayo, D. G. (1996). *Error and the Growth of Experimental Knowledge*.
"""

import logging
import math
import random
import re
import uuid
from dataclasses import dataclass, field
from typing import Any

_log = logging.getLogger(__name__)

__all__ = [
    "FalsificationDesigner",
    "HypothesisParser",
    "AdversarialCaseGenerator",
    "FalsificationRecord",
    "ConclusivenessAnalyzer",
]


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _extract_keywords(text: str) -> set[str]:
    """Extract significant words (length > 3) from *text*.

    Strips punctuation, lower-cases all tokens, and removes a curated set of
    English function words before returning the remaining content-bearing words.
    This lightweight approach avoids external NLP dependencies while still
    capturing the domain vocabulary of a hypothesis.

    Parameters
    ----------
    text:
        Raw hypothesis or phrase string.  Punctuation is silently discarded.

    Returns
    -------
    set[str]
        Unique significant words with more than three characters.

    Examples
    --------
    >>> kw = _extract_keywords("High divergence predicts novelty scores")
    >>> "divergence" in kw and "predicts" in kw
    True
    """
    _STOP: frozenset[str] = frozenset({
        "that", "this", "with", "from", "they", "have", "will",
        "been", "when", "where", "which", "also", "such", "more",
        "than", "into", "onto", "upon", "over", "under", "about",
        "each", "both", "some", "every", "then", "thus", "very",
        "just", "only", "even", "what", "does", "there", "their",
        "these", "those", "would", "should", "could", "might",
        "being", "having", "making", "given", "while", "since",
    })
    tokens = re.findall(r"[a-zA-Z]+", text.lower())
    return {t for t in tokens if len(t) > 3 and t not in _STOP}


def _hypothesis_complexity(text: str) -> float:
    """Return a complexity score in ``[0, 1]`` for the hypothesis *text*.

    Complexity is defined as the number of significant keywords divided by
    ten, clamped to the unit interval.  A richer vocabulary signals a more
    specific — and thus more falsifiable — claim.

    Parameters
    ----------
    text:
        Raw hypothesis string.

    Returns
    -------
    float
        Complexity score ∈ [0, 1].

    Examples
    --------
    >>> _hypothesis_complexity("A predicts B") < _hypothesis_complexity(
    ...     "High semantic divergence in idea space causally predicts novelty scores"
    ... )
    True
    """
    kw = _extract_keywords(text)
    return min(len(kw) / 10.0, 1.0)


def _contradicts(outcome: dict[str, Any], prediction: dict[str, Any]) -> bool:
    """Determine whether *outcome* contradicts *prediction*.

    Two result mappings are considered contradictory when both contain a
    ``'value'`` key and those values conflict:

    - For numeric values: ``|outcome - prediction| > 0.1``
    - For all other types: straightforward inequality

    Parameters
    ----------
    outcome:
        Observed result mapping.  Expected to carry a ``'value'`` key.
    prediction:
        Predicted result mapping.  Expected to carry a ``'value'`` key.

    Returns
    -------
    bool
        ``True`` if the two mappings are contradictory; ``False`` if either
        mapping lacks a ``'value'`` key or if the values are compatible.

    Examples
    --------
    >>> _contradicts({"value": 0.9}, {"value": 0.1})
    True
    >>> _contradicts({"value": "high"}, {"value": "low"})
    True
    """
    if "value" not in outcome or "value" not in prediction:
        return False
    ov, pv = outcome["value"], prediction["value"]
    if isinstance(ov, (int, float)) and isinstance(pv, (int, float)):
        return abs(float(ov) - float(pv)) > 0.1
    return ov != pv


def _build_adversarial_condition(hypothesis: str, component: str) -> dict[str, Any]:
    """Build a structured adversarial test condition targeting *component*.

    Returns a dict suitable for inclusion in a :class:`FalsificationTest`'s
    ``test_conditions`` list.  The description is constructed to clearly
    communicate *what* is being stressed and *why* it is expected to be
    discriminating.

    Parameters
    ----------
    hypothesis:
        The full hypothesis string being tested.  Truncated to 80 characters
        in the description to keep logging readable.
    component:
        A keyword or structural component of the hypothesis to stress-test.

    Returns
    -------
    dict
        Structured condition with keys ``'type'``, ``'component'``, and
        ``'description'``.

    Examples
    --------
    >>> cond = _build_adversarial_condition("High novelty predicts success", "novelty")
    >>> cond["type"]
    'adversarial'
    """
    snippet = hypothesis[:80] + ("..." if len(hypothesis) > 80 else "")
    description = (
        f"Adversarial condition targeting component '{component}' "
        f"in hypothesis: \"{snippet}\".  "
        f"Seek an observation that is inconsistent with the predicted "
        f"behaviour of '{component}' under H."
    )
    return {
        "type": "adversarial",
        "component": component,
        "description": description,
        "hypothesis_fragment": hypothesis,
    }


# ---------------------------------------------------------------------------
# FalsificationTest — container for a single test run
# ---------------------------------------------------------------------------


@dataclass
class FalsificationTest:
    """Container for a single falsification trial of one hypothesis.

    A ``FalsificationTest`` is created by :class:`FalsificationDesigner` and
    carries the full lifecycle of evidence for one hypothesis: from the list of
    generated test conditions through the accumulated attempts and the running
    confidence estimate.

    Attributes
    ----------
    hypothesis:
        The natural-language hypothesis under test.
    test_conditions:
        Ordered list of condition dicts generated for this hypothesis.
    falsified:
        ``True`` once at least one condition has yielded a contradicting
        outcome.
    confidence:
        Running confidence estimate that the hypothesis has *survived*
        testing, updated by each call to
        :meth:`FalsificationDesigner.attempt_falsification`.  Initialised at
        0.5 (maximum uncertainty).
    attempts:
        Total number of individual test conditions that have been attempted.
    adversarial_count:
        Number of conditions in :attr:`test_conditions` whose ``'type'`` is
        ``'adversarial'``.
    test_id:
        Unique identifier for this test run, auto-generated via :mod:`uuid`.
    """

    hypothesis: str
    test_conditions: list[dict[str, Any]] = field(default_factory=list)
    falsified: bool = False
    confidence: float = 0.5
    attempts: int = 0
    adversarial_count: int = 0
    test_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])

    def mark_adversarial(self, condition: dict[str, Any]) -> None:
        """Append *condition* to :attr:`test_conditions` and update counters.

        Parameters
        ----------
        condition:
            A condition dict.  If its ``'type'`` is ``'adversarial'``,
            :attr:`adversarial_count` is incremented.
        """
        self.test_conditions.append(condition)
        if condition.get("type") == "adversarial":
            self.adversarial_count += 1

    def record_attempt(self, *, falsifying: bool) -> None:
        """Record one test attempt and update :attr:`confidence`.

        On a falsifying outcome the confidence drops by 0.4; on a surviving
        outcome it increases by 0.05 (asymmetric to reflect asymmetric
        epistemic force of falsification vs. corroboration).

        Parameters
        ----------
        falsifying:
            Whether this attempt yielded an outcome that contradicts the
            hypothesis.
        """
        self.attempts += 1
        if falsifying:
            self.falsified = True
            self.confidence = max(0.0, self.confidence - 0.4)
        else:
            self.confidence = min(1.0, self.confidence + 0.05)

    def __repr__(self) -> str:
        status = "FALSIFIED" if self.falsified else f"conf={self.confidence:.2f}"
        return (
            f"FalsificationTest(id={self.test_id}, "
            f"attempts={self.attempts}, {status}, "
            f"conditions={len(self.test_conditions)})"
        )


# ---------------------------------------------------------------------------
# FalsificationDesigner
# ---------------------------------------------------------------------------


class FalsificationDesigner:
    """Design and manage falsification campaigns for ideation hypotheses.

    This class is the primary entry-point for Popperian hypothesis testing.
    It orchestrates the generation of adversarial test conditions, records
    outcomes, and provides summary statistics on the strength and
    conclusiveness of the evidence gathered.

    Parameters
    ----------
    confidence_threshold:
        Minimum :attr:`FalsificationTest.confidence` required to declare a
        hypothesis *provisionally corroborated* (not yet falsified) after all
        tests have been attempted.
    seed:
        Random seed forwarded to :class:`AdversarialCaseGenerator` for
        reproducible condition generation.

    Examples
    --------
    >>> fd = FalsificationDesigner()
    >>> test = fd.design_falsification("High divergence predicts high novelty")
    >>> len(test.test_conditions)
    7
    """

    def __init__(self, confidence_threshold: float = 0.95, seed: int = 42) -> None:
        self.confidence_threshold = confidence_threshold
        self._rng = random.Random(seed)
        self._parser = HypothesisParser()
        self._generator = AdversarialCaseGenerator(seed=seed)
        _log.debug(
            "FalsificationDesigner initialised (threshold=%.2f, seed=%d)",
            confidence_threshold,
            seed,
        )

    def design_falsification(
        self, hypothesis: str, n_conditions: int = 5
    ) -> FalsificationTest:
        """Design a :class:`FalsificationTest` for *hypothesis*.

        Generates *n_conditions* adversarial test conditions and appends one
        boundary case and one extreme case so that the test suite covers both
        the interior and the edges of the hypothesis' stated domain.

        Parameters
        ----------
        hypothesis:
            Natural-language hypothesis string.
        n_conditions:
            Number of adversarial conditions to generate (boundary and extreme
            cases are added on top of this count).

        Returns
        -------
        FalsificationTest
            A freshly minted test object ready to be executed.
        """
        test = FalsificationTest(hypothesis=hypothesis)
        for case in self._generator.generate(hypothesis, n_cases=n_conditions):
            test.mark_adversarial(case)
        # Always include at least one boundary and one extreme case
        test.test_conditions.append(self._generator.generate_boundary_case(hypothesis))
        test.test_conditions.append(self._generator.generate_extreme_case(hypothesis))
        _log.info(
            "Designed falsification test %s with %d conditions for: %.60s",
            test.test_id,
            len(test.test_conditions),
            hypothesis,
        )
        return test

    def attempt_falsification(
        self,
        test: FalsificationTest,
        condition: dict[str, Any],
        outcome: dict[str, Any],
    ) -> bool:
        """Attempt to falsify *test* under *condition* given observed *outcome*.

        Parses the hypothesis' predicate as the expected prediction, compares
        it to *outcome* using :func:`_contradicts`, and updates the test's
        internal confidence and ``falsified`` flag accordingly.

        Parameters
        ----------
        test:
            The :class:`FalsificationTest` to update.
        condition:
            The test condition that was executed (used for logging).
        outcome:
            Observed outcome dict.  Must contain a ``'value'`` key for
            contradiction detection to engage.

        Returns
        -------
        bool
            ``True`` if the outcome contradicts the hypothesis' prediction.
        """
        parsed = self._parser.parse(test.hypothesis)
        prediction: dict[str, Any] = {
            "value": parsed.get("predicate", ""),
            "source": "hypothesis",
        }
        falsifying = _contradicts(outcome, prediction)
        test.record_attempt(falsifying=falsifying)
        _log.debug(
            "Test %s: condition_type=%s falsifying=%s confidence=%.3f",
            test.test_id,
            condition.get("type", "?"),
            falsifying,
            test.confidence,
        )
        return falsifying

    def strength_of_test(self, test: FalsificationTest) -> float:
        """Return the proportion of adversarial conditions in *test*.

        A test battery is stronger when a greater fraction of its conditions
        are deliberately adversarial (as opposed to benign boundary or extreme
        cases).  A value of 1.0 means every condition was adversarial.

        Parameters
        ----------
        test:
            The :class:`FalsificationTest` to evaluate.

        Returns
        -------
        float
            Proportion ∈ [0, 1].  Returns 0.0 for an empty test.
        """
        total = len(test.test_conditions)
        if total == 0:
            return 0.0
        adversarial = sum(
            1 for c in test.test_conditions if c.get("type") == "adversarial"
        )
        return adversarial / total

    def design_critical_test(self, hypothesis: str) -> dict[str, Any]:
        """Return the single most informative test condition for *hypothesis*.

        The critical test targets the longest (most domain-specific) keyword
        in the hypothesis — the pivot term whose removal would change the
        hypothesis' meaning most dramatically — and is labelled with a
        rationale explaining its discriminating power.

        Parameters
        ----------
        hypothesis:
            Natural-language hypothesis string.

        Returns
        -------
        dict
            A single adversarial condition dict with an extra ``'critical'``
            and ``'rationale'`` key.
        """
        keywords = sorted(_extract_keywords(hypothesis), key=len, reverse=True)
        pivot = keywords[0] if keywords else "unknown"
        condition = _build_adversarial_condition(hypothesis, pivot)
        condition["critical"] = True
        condition["rationale"] = (
            f"Targeting '{pivot}' (the most domain-specific keyword) maximises "
            "the discriminating power between H and ¬H: if behaviour under "
            f"'{pivot}' cannot be distinguished from random variation, H loses "
            "its empirical content."
        )
        return condition

    def evaluate_falsifiability(self, hypothesis: str) -> float:
        """Score how falsifiable *hypothesis* is, on a scale from 0 to 1.

        Higher scores indicate a hypothesis that makes precise, testable
        predictions.  Three components contribute:

        - **Specificity** (0–0.40): keyword count relative to 10.
        - **Quantitative markers** (0–0.35): presence of comparative /
          directional terms.
        - **Universal quantifiers** (0–0.25): "all", "every", "never", etc.

        Parameters
        ----------
        hypothesis:
            Natural-language hypothesis string.

        Returns
        -------
        float
            Falsifiability score ∈ [0, 1].

        Examples
        --------
        >>> fd = FalsificationDesigner()
        >>> fd.evaluate_falsifiability("Things happen") < fd.evaluate_falsifiability(
        ...     "All high-divergence idea pairs produce significantly more novel outputs"
        ... )
        True
        """
        score = 0.0
        lower = hypothesis.lower()
        # Specificity from keyword count
        score += _hypothesis_complexity(hypothesis) * 0.40
        # Quantitative / directional terms boost falsifiability
        quant_terms: frozenset[str] = frozenset({
            "more", "less", "higher", "lower", "greater", "fewer",
            "increase", "decrease", "faster", "slower", "positive",
            "negative", "correlation", "significant", "percent",
            "predict", "causes", "effect", "leads",
        })
        quant_hit = sum(1 for t in quant_terms if t in lower)
        score += min(quant_hit / 5.0, 1.0) * 0.35
        # Universal quantifiers make claims maximally falsifiable
        universal_terms = ["all ", "every ", "never ", "always ", "none ", "no "]
        if any(t in lower for t in universal_terms):
            score += 0.25
        return min(score, 1.0)


# ---------------------------------------------------------------------------
# HypothesisParser
# ---------------------------------------------------------------------------


class HypothesisParser:
    """Parse natural-language hypothesis strings into structured components.

    The parser applies lightweight heuristics and regular expressions to
    decompose a hypothesis into subject, predicate, quantifier, and keyword
    components without requiring any NLP library.

    All methods are pure functions of their inputs; the class carries no
    mutable state and its instances may be shared across threads.

    Examples
    --------
    >>> hp = HypothesisParser()
    >>> result = hp.parse("All creative agents produce more novel ideas.")
    >>> result["is_universal"]
    True
    >>> result["quantifier"]
    'universal'
    """

    _UNIVERSAL_MARKERS: tuple[str, ...] = (
        "all ", "every ", "always ", "never ", "none ", "no ", "each ",
    )
    _EXISTENTIAL_MARKERS: tuple[str, ...] = (
        "some ", "there exists", "at least one", "occasionally", "sometimes",
    )
    _QUANTITATIVE_MARKERS: tuple[str, ...] = (
        "more", "less", "greater", "fewer", "higher", "lower",
        "increase", "decrease", "larger", "smaller", "faster", "slower",
    )

    def __init__(self) -> None:
        _log.debug("HypothesisParser initialised")

    def parse(self, hypothesis: str) -> dict[str, Any]:
        """Parse *hypothesis* into its structural components.

        Uses the first-word as a heuristic subject and everything after the
        first whitespace token as the predicate.  Quantifier detection
        searches the full lowercase string for marker substrings.

        Parameters
        ----------
        hypothesis:
            Natural-language hypothesis string.

        Returns
        -------
        dict
            Keys: ``'subject'``, ``'predicate'``, ``'keywords'``,
            ``'quantifier'``, ``'is_universal'``.
        """
        lower = hypothesis.lower().strip()
        quantifier = "existential"
        is_universal = False
        for marker in self._UNIVERSAL_MARKERS:
            if lower.startswith(marker) or f" {marker.strip()} " in lower:
                quantifier = "universal"
                is_universal = True
                break
        if not is_universal:
            for marker in self._EXISTENTIAL_MARKERS:
                if marker in lower:
                    quantifier = "existential"
                    break
        tokens = hypothesis.split()
        subject = tokens[0] if tokens else ""
        predicate = " ".join(tokens[1:]) if len(tokens) > 1 else ""
        keywords = sorted(_extract_keywords(hypothesis))
        return {
            "subject": subject,
            "predicate": predicate,
            "keywords": keywords,
            "quantifier": quantifier,
            "is_universal": is_universal,
        }

    def extract_predictions(self, hypothesis: str) -> list[str]:
        """Extract a list of testable predictions from *hypothesis*.

        Each prediction is a clause that can in principle be observed or
        measured.  The function identifies comparative clauses, causal
        statements, and directional claims.

        Parameters
        ----------
        hypothesis:
            Natural-language hypothesis string.

        Returns
        -------
        list[str]
            Ordered list of prediction strings.  Returns the full hypothesis
            as a single element when no sub-predictions can be extracted.

        Examples
        --------
        >>> hp = HypothesisParser()
        >>> preds = hp.extract_predictions(
        ...     "High divergence increases novelty and reduces redundancy."
        ... )
        >>> len(preds) >= 1
        True
        """
        predictions: list[str] = []
        clauses = re.split(r"\band\b|;|,\s*(?=[A-Z])", hypothesis)
        for clause in clauses:
            clause = clause.strip().strip(",").strip()
            if not clause:
                continue
            lower = clause.lower()
            if any(m in lower for m in self._QUANTITATIVE_MARKERS):
                predictions.append(clause)
            elif " than " in lower or " compared to " in lower:
                predictions.append(clause)
            elif any(
                kw in lower for kw in (
                    " causes ", " leads to ", " results in ",
                    " predicts ", " correlates ",
                )
            ):
                predictions.append(clause)
        if not predictions:
            predictions = [hypothesis.strip()]
        return predictions

    def negate(self, hypothesis: str) -> str:
        """Return the logical negation of *hypothesis*.

        Applies ordered linguistic transformations: universal → existential
        quantifiers are swapped, verb auxiliaries are negated, and directional
        predicates are inverted.  The first matching transformation wins; if
        nothing matches, the result is prefixed with "It is not the case that".

        Parameters
        ----------
        hypothesis:
            Natural-language hypothesis string.

        Returns
        -------
        str
            A natural-language string representing ¬hypothesis.

        Examples
        --------
        >>> hp = HypothesisParser()
        >>> "not" in hp.negate("All agents produce more ideas").lower()
        True
        """
        replacements: list[tuple[str, str]] = [
            (r"\ball\b", "some"),
            (r"\bevery\b", "not every"),
            (r"\balways\b", "not always"),
            (r"\bnever\b", "sometimes"),
            (r"\bnone\b", "some"),
            (r"\bis not\b", "is"),
            (r"\bis\b", "is not"),
            (r"\bwill\b", "will not"),
            (r"\bincreases\b", "does not increase"),
            (r"\bdecreases\b", "does not decrease"),
            (r"\bcauses\b", "does not cause"),
            (r"\bpredicts\b", "does not predict"),
            (r"\bleads to\b", "does not lead to"),
        ]
        lower_h = hypothesis.lower()
        for pattern, replacement in replacements:
            m = re.search(pattern, lower_h)
            if m:
                start, end = m.span()
                negated = hypothesis[:start] + replacement + hypothesis[end:]
                return negated
        # Fallback: prefix with explicit negation
        return "It is not the case that " + hypothesis[0].lower() + hypothesis[1:]

    def decompose(self, hypothesis: str) -> list[str]:
        """Break a compound hypothesis into simple sub-hypotheses.

        Splits on coordinating conjunctions and semicolons to produce a list
        of atomic claims, each independently falsifiable.

        Parameters
        ----------
        hypothesis:
            Natural-language hypothesis string, potentially compound.

        Returns
        -------
        list[str]
            List of simple sub-hypothesis strings, each capitalised.

        Examples
        --------
        >>> hp = HypothesisParser()
        >>> parts = hp.decompose("Novelty increases and redundancy decreases.")
        >>> len(parts)
        2
        """
        parts = re.split(
            r"\band\b|\bbut\b|\bwhile\b|\bwhereas\b|\balthough\b|;",
            hypothesis,
            flags=re.IGNORECASE,
        )
        cleaned = [p.strip().strip(",").strip() for p in parts if p.strip()]
        return [p[0].upper() + p[1:] if p else p for p in cleaned]

    def is_falsifiable(self, hypothesis: str) -> bool:
        """Return ``True`` if *hypothesis* contains a testable quantitative prediction.

        A hypothesis is considered falsifiable when it includes at least one
        quantitative, comparative, or causal marker whose predicted direction
        could be empirically measured and potentially violated.

        Parameters
        ----------
        hypothesis:
            Natural-language hypothesis string.

        Returns
        -------
        bool

        Examples
        --------
        >>> hp = HypothesisParser()
        >>> hp.is_falsifiable("Creativity is important.")
        False
        >>> hp.is_falsifiable("High creativity produces more novel outcomes.")
        True
        """
        lower = hypothesis.lower()
        for marker in self._QUANTITATIVE_MARKERS:
            if marker in lower:
                return True
        for phrase in (
            " more than ", " less than ", " greater than ",
            " at least ", " at most ", " correlates ", " predicts ",
            " causes ", " leads to ", " results in ",
        ):
            if phrase in lower:
                return True
        return False


# ---------------------------------------------------------------------------
# AdversarialCaseGenerator
# ---------------------------------------------------------------------------


class AdversarialCaseGenerator:
    """Generate adversarial test cases designed to stress-test a hypothesis.

    Adversarial cases are constructed to maximise the probability of exposing
    weaknesses in a hypothesis.  Four distinct case types are supported:

    - **Adversarial**: targets a specific keyword under hostile conditions.
    - **Boundary**: probes the edges of the hypothesis' stated domain.
    - **Extreme**: applies parameter values far outside the typical range.
    - **Contradiction**: seeks conditions under which ¬H is expected to hold.

    Parameters
    ----------
    seed:
        Random seed for reproducible case generation.

    Examples
    --------
    >>> acg = AdversarialCaseGenerator(seed=0)
    >>> cases = acg.generate("High divergence predicts novelty", n_cases=3)
    >>> len(cases)
    3
    >>> all(c["type"] == "adversarial" for c in cases)
    True
    """

    _EXTREME_MODIFIERS: tuple[str, ...] = (
        "extremely low",
        "extremely high",
        "exactly zero",
        "maximum possible",
        "minimum possible",
        "randomly distributed",
        "uniformly distributed",
        "adversarially chosen",
        "degenerate (all-identical)",
    )

    def __init__(self, seed: int = 42) -> None:
        self._rng = random.Random(seed)
        _log.debug("AdversarialCaseGenerator initialised (seed=%d)", seed)

    def generate(self, hypothesis: str, n_cases: int = 5) -> list[dict[str, Any]]:
        """Generate *n_cases* adversarial test cases for *hypothesis*.

        Each case targets a different keyword extracted from the hypothesis.
        If the hypothesis has fewer keywords than *n_cases*, keywords are
        reused cyclically with an incrementing index to keep each case
        distinct.

        Parameters
        ----------
        hypothesis:
            Natural-language hypothesis string.
        n_cases:
            Number of adversarial cases to generate.

        Returns
        -------
        list[dict]
            List of adversarial condition dicts, each with ``'type'``,
            ``'component'``, ``'description'``, ``'index'``, and
            ``'strength'`` keys.
        """
        keywords = list(_extract_keywords(hypothesis))
        self._rng.shuffle(keywords)
        cases: list[dict[str, Any]] = []
        for i in range(n_cases):
            component = keywords[i % len(keywords)] if keywords else f"component_{i}"
            case = _build_adversarial_condition(hypothesis, component)
            case["index"] = i
            case["strength"] = round(self._rng.uniform(0.5, 1.0), 4)
            cases.append(case)
        return cases

    def generate_boundary_case(self, hypothesis: str) -> dict[str, Any]:
        """Generate a test case that probes boundary conditions of *hypothesis*.

        A boundary case tests the hypothesis at the edges of its stated domain,
        where the claim is just barely expected to hold.  It targets the
        *shortest* significant keyword (the most fundamental concept) to
        stress-test the hypothesis at its most basic operative level.

        Parameters
        ----------
        hypothesis:
            Natural-language hypothesis string.

        Returns
        -------
        dict
            Boundary condition dict with ``'type': 'boundary'``.
        """
        keywords = sorted(_extract_keywords(hypothesis), key=len)
        pivot = keywords[0] if keywords else "parameter"
        return {
            "type": "boundary",
            "component": pivot,
            "description": (
                f"Boundary case: set '{pivot}' to its minimum non-trivial value "
                "and verify the hypothesis still holds at that edge.  "
                "A failure here would indicate the claim does not extend to "
                "the boundary of its own domain."
            ),
            "hypothesis_fragment": hypothesis,
            "boundary_value": "minimum_non_trivial",
        }

    def generate_extreme_case(self, hypothesis: str) -> dict[str, Any]:
        """Generate a test case with extreme parameter values.

        Extreme cases apply the hypothesis under conditions far outside its
        typical operating range, testing robustness and
        over-generalisation.

        Parameters
        ----------
        hypothesis:
            Natural-language hypothesis string.

        Returns
        -------
        dict
            Extreme condition dict with ``'type': 'extreme'``.
        """
        modifier = self._rng.choice(self._EXTREME_MODIFIERS)
        keywords = sorted(_extract_keywords(hypothesis), key=len, reverse=True)
        pivot = keywords[0] if keywords else "variable"
        return {
            "type": "extreme",
            "component": pivot,
            "modifier": modifier,
            "description": (
                f"Extreme case: apply '{modifier}' values to '{pivot}' and "
                "observe whether the hypothesis' prediction still holds.  "
                "A failure under extreme inputs may indicate the hypothesis "
                "only holds for a restricted parameter range."
            ),
            "hypothesis_fragment": hypothesis,
        }

    def generate_contradiction_case(self, hypothesis: str) -> dict[str, Any]:
        """Generate a case explicitly designed to contradict *hypothesis*.

        Constructs conditions under which the negation of the hypothesis is
        predicted to be true, maximising falsification potential by directly
        seeking P(outcome | ¬H) >> P(outcome | H).

        Parameters
        ----------
        hypothesis:
            Natural-language hypothesis string.

        Returns
        -------
        dict
            Contradiction condition dict with ``'type': 'contradiction'``.
        """
        parser = HypothesisParser()
        negation = parser.negate(hypothesis)
        keywords = list(_extract_keywords(hypothesis))
        pivot = keywords[0] if keywords else "core_claim"
        return {
            "type": "contradiction",
            "component": pivot,
            "negation": negation,
            "description": (
                f"Contradiction case: seek experimental conditions under which "
                f"\"{negation[:100]}\".  "
                "If such conditions exist and produce the predicted outcome, "
                "the hypothesis is falsified."
            ),
            "hypothesis_fragment": hypothesis,
            "expected_outcome": "contradiction",
        }

    def score_adversarial_strength(self, case: dict[str, Any]) -> float:
        """Return a composite adversarial strength score in ``[0, 1]``.

        Strength is the arithmetic mean of:

        - **Type weight**: ``contradiction`` → 1.0, ``extreme`` → 0.85,
          ``adversarial`` → 0.70, ``boundary`` → 0.50, unknown → 0.50.
        - **Pre-computed strength** stored in ``case['strength']`` (defaults
          to 0.5 if absent).

        Parameters
        ----------
        case:
            A condition dict as produced by :meth:`generate` or its siblings.

        Returns
        -------
        float
            Adversarial strength score ∈ [0, 1].
        """
        _TYPE_WEIGHTS: dict[str, float] = {
            "contradiction": 1.00,
            "extreme": 0.85,
            "adversarial": 0.70,
            "boundary": 0.50,
        }
        type_score = _TYPE_WEIGHTS.get(case.get("type", ""), 0.50)
        stored_strength = float(case.get("strength", 0.50))
        return round((type_score + stored_strength) / 2.0, 4)


# ---------------------------------------------------------------------------
# FalsificationRecord
# ---------------------------------------------------------------------------


@dataclass
class FalsificationRecord:
    """Persistent aggregate record of falsification attempts for one hypothesis.

    This dataclass accumulates results over multiple :class:`FalsificationTest`
    runs and provides longitudinal statistics about the robustness of a
    hypothesis across different experimental campaigns.

    Under the Popperian framework, robustness is *not* positive evidence for
    truth — it is merely the absence of refutation so far.
    :meth:`is_robust` reflects this epistemic humility by using a strict
    10 % falsification-rate threshold.

    Attributes
    ----------
    record_id:
        Unique identifier for this record (UUID fragment or user-supplied).
    hypothesis:
        The hypothesis under investigation.
    tests:
        List of test-result dicts appended by :meth:`add_test`.
    total_attempts:
        Total number of individual test conditions attempted across all runs.
    successful_falsifications:
        Number of test *runs* in which the hypothesis was falsified.
    failed_falsifications:
        Number of test *runs* in which the hypothesis survived (i.e. was not
        falsified).
    """

    record_id: str
    hypothesis: str
    tests: list[dict[str, Any]] = field(default_factory=list)
    total_attempts: int = 0
    successful_falsifications: int = 0
    failed_falsifications: int = 0

    def add_test(self, test_result: dict[str, Any]) -> None:
        """Append *test_result* and update running counters.

        Parameters
        ----------
        test_result:
            A dict with at least a ``'falsified'`` boolean key and optionally
            an ``'attempts'`` integer key.  The ``'attempts'`` value (default
            1) is added to :attr:`total_attempts`.
        """
        self.tests.append(test_result)
        attempts = int(test_result.get("attempts", 1))
        self.total_attempts += attempts
        if test_result.get("falsified", False):
            self.successful_falsifications += 1
        else:
            self.failed_falsifications += 1
        _log.debug(
            "FalsificationRecord %s updated: total=%d falsified=%d survived=%d",
            self.record_id,
            self.total_attempts,
            self.successful_falsifications,
            self.failed_falsifications,
        )

    def falsification_rate(self) -> float:
        """Return the proportion of test *runs* that succeeded in falsifying the hypothesis.

        Returns
        -------
        float
            Rate ∈ [0, 1].  Returns ``0.0`` if no tests have been recorded.

        Examples
        --------
        >>> rec = FalsificationRecord("r1", "H0")
        >>> rec.add_test({"falsified": True, "attempts": 3})
        >>> rec.add_test({"falsified": False, "attempts": 5})
        >>> rec.falsification_rate()
        0.5
        """
        n = len(self.tests)
        if n == 0:
            return 0.0
        return self.successful_falsifications / n

    def is_robust(self) -> bool:
        """Return ``True`` if the hypothesis is provisionally robust.

        A hypothesis is deemed robust when its falsification rate is strictly
        below 10 %.  This threshold reflects the requirement for substantial
        (but not perfect) resistance to adversarial testing.

        Returns
        -------
        bool
        """
        return self.falsification_rate() < 0.10

    def summary(self) -> str:
        """Return a human-readable multi-line summary of this record.

        Returns
        -------
        str
            Multi-line string suitable for logging or display.
        """
        rate = self.falsification_rate()
        status = "ROBUST" if self.is_robust() else "FALSIFIED / WEAK"
        snippet = self.hypothesis[:80] + ("..." if len(self.hypothesis) > 80 else "")
        lines = [
            f"FalsificationRecord [{self.record_id}]",
            f"  Hypothesis : {snippet}",
            f"  Tests run  : {len(self.tests)}",
            f"  Attempts   : {self.total_attempts}",
            f"  Falsified  : {self.successful_falsifications} "
            f"({rate:.1%} falsification rate)",
            f"  Survived   : {self.failed_falsifications}",
            f"  Status     : {status}",
        ]
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# ConclusivenessAnalyzer
# ---------------------------------------------------------------------------


class ConclusivenessAnalyzer:
    """Analyse the conclusiveness of falsification test results.

    Evaluates whether a :class:`FalsificationTest` has accumulated sufficient
    evidence to support a definite conclusion about the hypothesis under test,
    and recommends next steps when testing is incomplete.

    A test is considered *conclusive* when:

    - At least 5 conditions have been attempted.
    - Evidence strength is ≥ 0.60.
    - The confidence is more than 0.25 away from the 0.50 uncertainty midpoint.

    Examples
    --------
    >>> ca = ConclusivenessAnalyzer()
    >>> fd = FalsificationDesigner()
    >>> test = fd.design_falsification("High entropy predicts novelty", n_conditions=10)
    >>> analysis = ca.analyze(test)
    >>> set(analysis.keys()) == {"is_conclusive", "confidence", "recommendation",
    ...                          "evidence_strength"}
    True
    """

    _RECOMMENDATION_TEMPLATES: list[str] = [
        "Increase sample size by running more adversarial conditions.",
        "Focus next tests on boundary cases near the hypothesised threshold.",
        "Introduce control conditions assuming ¬H and measure outcomes.",
        "Replicate the highest-strength adversarial condition from the prior run.",
        "Vary the experimental context to test generalisability of the hypothesis.",
        "Seek independent corroboration through a separate experimental paradigm.",
        "Apply Holm–Bonferroni correction if running simultaneous sub-hypothesis tests.",
    ]

    def __init__(self) -> None:
        _log.debug("ConclusivenessAnalyzer initialised")

    def analyze(self, test: FalsificationTest) -> dict[str, Any]:
        """Return a conclusiveness analysis of *test*.

        Parameters
        ----------
        test:
            A :class:`FalsificationTest` with recorded outcomes.

        Returns
        -------
        dict
            Keys: ``'is_conclusive'`` (bool), ``'confidence'`` (float),
            ``'recommendation'`` (str), ``'evidence_strength'`` (float).
        """
        ev_strength = self.evidence_strength(test)
        confidence = test.confidence
        is_conclusive = (
            test.attempts >= 5
            and ev_strength >= 0.60
            and abs(confidence - 0.5) >= 0.25
        )
        recommendation = self.recommend_next_test(test)
        _log.debug(
            "ConclusivenessAnalyzer: test=%s conclusive=%s strength=%.3f conf=%.3f",
            test.test_id,
            is_conclusive,
            ev_strength,
            confidence,
        )
        return {
            "is_conclusive": is_conclusive,
            "confidence": confidence,
            "recommendation": recommendation,
            "evidence_strength": ev_strength,
        }

    def evidence_strength(self, test: FalsificationTest) -> float:
        """Compute evidence strength for *test*.

        Strength is a weighted combination of:

        - **Quantity score** (weight 0.50): number of test conditions saturating
          at n = 20.
        - **Quality score** (weight 0.50): proportion of those conditions that
          are of type ``'adversarial'``.

        Parameters
        ----------
        test:
            The :class:`FalsificationTest` to evaluate.

        Returns
        -------
        float
            Evidence strength ∈ [0, 1].
        """
        n = len(test.test_conditions)
        quantity_score = min(n / 20.0, 1.0)
        adversarial = sum(
            1 for c in test.test_conditions if c.get("type") == "adversarial"
        )
        quality_score = adversarial / n if n > 0 else 0.0
        return round(0.5 * quantity_score + 0.5 * quality_score, 4)

    def recommend_next_test(self, test: FalsificationTest) -> str:
        """Return a recommendation for what to test next.

        The recommendation is chosen based on the current test state:

        - Very few attempts → collect more data.
        - Hypothesis already falsified → seek replication.
        - High confidence → probe boundaries.
        - No adversarial conditions yet → add adversarial conditions.
        - Otherwise → deterministic selection from a template pool keyed
          on the test identifier for reproducibility.

        Parameters
        ----------
        test:
            The :class:`FalsificationTest` currently under analysis.

        Returns
        -------
        str
            Natural-language recommendation string.
        """
        if test.attempts < 3:
            return self._RECOMMENDATION_TEMPLATES[0]
        if test.falsified:
            return self._RECOMMENDATION_TEMPLATES[3]
        if test.confidence > 0.85:
            return self._RECOMMENDATION_TEMPLATES[1]
        if test.adversarial_count == 0:
            return self._RECOMMENDATION_TEMPLATES[2]
        # Deterministic selection keyed on test_id for reproducibility
        idx = abs(hash(test.test_id)) % len(self._RECOMMENDATION_TEMPLATES)
        return self._RECOMMENDATION_TEMPLATES[idx]

    def compare_hypotheses(
        self, tests: list[FalsificationTest]
    ) -> list[tuple[str, float]]:
        """Rank *tests* by their falsification confidence, highest first.

        A higher confidence score indicates a hypothesis that has survived
        more adversarial testing and is therefore more provisionally
        corroborated.  Ties are broken by the order of *tests*.

        Parameters
        ----------
        tests:
            List of :class:`FalsificationTest` objects to rank.

        Returns
        -------
        list[tuple[str, float]]
            Sorted list of ``(hypothesis_snippet, confidence)`` pairs in
            descending order of confidence.

        Examples
        --------
        >>> ca = ConclusivenessAnalyzer()
        >>> t1 = FalsificationTest(hypothesis="H1 predicts more X", confidence=0.9)
        >>> t2 = FalsificationTest(hypothesis="H2 predicts less Y", confidence=0.6)
        >>> ca.compare_hypotheses([t1, t2])[0][1]
        0.9
        """
        ranked = [(t.hypothesis[:80], t.confidence) for t in tests]
        ranked.sort(key=lambda pair: pair[1], reverse=True)
        return ranked
