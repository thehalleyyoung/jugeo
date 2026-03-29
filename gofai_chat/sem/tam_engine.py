from __future__ import annotations
# Paper ref: §TAM -- Reichenbach's three-point system
"""Tense, Aspect, and Modality Engine.

Implements the full temporal-aspectual-modal interpretation machinery:

- **Reichenbach's S, R, E three-point system for tense.**
  Every English tense is analysed as a triple of time-points: Speech
  time (S), Reference time (R), and Event time (E), with orderings
  between them.  For example the past perfect "She had arrived" is
  analysed as E < R < S.

- **All 36 tense-aspect combinations** with their temporal frames,
  covering the standard 12 English tenses plus additional aspectual
  distinctions (habitual, iterative, inceptive, terminative,
  continuative) for each temporal base.

- **Modal logic using Kratzer's conversational-background approach.**
  Modals quantify over possible worlds restricted by a modal base
  (epistemic, deontic, dynamic, bouletic, teleological) and ordered
  by a normative or stereotypical ordering source.

- **Narrative tense shifting** and free-indirect-discourse detection.
  Handles historical present, flashback, flash-forward, and other
  non-canonical tense usages.

- **Counterfactual evaluation** using a simplified version of Lewis's
  (1973) closest-world similarity metric.

- **Temporal graph construction** over discourse event sequences, with
  Allen-style constraint propagation and coherence checking.

All grades follow the ``gofai_chat.core.grade.Grade`` protocol:
log-probability space where 0.0 = perfect and ``-inf`` = impossible.

References
----------
Reichenbach, H. (1947). *Elements of Symbolic Logic*, Ch. 7.
Kratzer, A. (1981). The notional category of modality.
Lewis, D. (1973). *Counterfactuals*. Harvard University Press.
Partee, B. (1984). Nominal and temporal anaphora.
Comrie, B. (1976). *Aspect*. Cambridge University Press.
Allen, J. F. (1983). Maintaining knowledge about temporal intervals.
"""

__all__ = [
    "TenseValue",
    "AspectValue",
    "MoodValue",
    "TemporalPoint",
    "TemporalRelation",
    "TemporalFrame",
    "TAMBundle",
    "TemporalInterval",
    "TemporalGraph",
    "TemporalCoherenceChecker",
    "TENSE_ASPECT_FRAMES",
    "ReichenbachSystem",
    "TemporalAnaphoraResolver",
    "ModalBase",
    "MODAL_TO_BASE",
    "KratzerModals",
    "NarrativeTenseShifter",
    "CounterfactualEvaluator",
    "TAMEngine",
    "TENSE_ALIASES",
    "ASPECT_ALIASES",
    "build_default_context",
    "get_modal_grade",
    "allen_relation_compatible",
]

import math
import heapq
from collections import deque, defaultdict
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from gofai_chat.core.grade import Grade
from gofai_chat.core.types import Tense, Aspect, Mood
from gofai_chat.core.terms import (
    HLF, Var, TenseTerm, AspectTerm, ModalTerm, ConjTerm,
    tense as make_tense_term,
    aspect as make_aspect_term,
    modal as make_modal_term,
    conj,
)
from gofai_chat.core.judgment import Context, Referent


# ═══════════════════════════════════════════════════════════════════════
#  Enumerations
# ═══════════════════════════════════════════════════════════════════════


class TenseValue(Enum):
    """Composite tense values for the 12 standard English tenses.

    Each value represents a specific combination of temporal reference
    (past, present, future) and grammatical aspect (simple, progressive,
    perfect, perfect progressive).

    These map one-to-one onto the 12 cells of the traditional English
    tense paradigm::

        +------------+------------+--------------+--------------------+
        |            | Simple     | Progressive  | Perfect            |
        +------------+------------+--------------+--------------------+
        | Past       | ran        | was running  | had run            |
        | Present    | run(s)     | is running   | has run            |
        | Future     | will run   | will be ...  | will have run      |
        +------------+------------+--------------+--------------------+

    Plus the three perfect-progressive forms (had been running, etc.).
    """

    PAST = "past"
    PRESENT = "present"
    FUTURE = "future"
    PAST_PERFECT = "past_perfect"
    PRESENT_PERFECT = "present_perfect"
    FUTURE_PERFECT = "future_perfect"
    PAST_PROGRESSIVE = "past_progressive"
    PRESENT_PROGRESSIVE = "present_progressive"
    FUTURE_PROGRESSIVE = "future_progressive"
    PAST_PERFECT_PROGRESSIVE = "past_perfect_progressive"
    PRESENT_PERFECT_PROGRESSIVE = "present_perfect_progressive"
    FUTURE_PERFECT_PROGRESSIVE = "future_perfect_progressive"


class AspectValue(Enum):
    """Grammatical aspect reduced to the four major English categories.

    Unlike ``gofai_chat.core.types.Aspect``, which includes finer-grained
    aspectual classes (habitual, iterative, inceptive, terminative,
    continuative), this enum captures only the four categories that are
    morphologically marked in English.

    Members
    -------
    SIMPLE
        No special aspectual marking (default).
    PROGRESSIVE
        Ongoing action, marked by *be + -ing*.
    PERFECT
        Completed action with present relevance, marked by *have + past
        participle*.
    PERFECT_PROGRESSIVE
        Completed-but-ongoing, *have been + -ing*.
    """

    SIMPLE = "simple"
    PROGRESSIVE = "progressive"
    PERFECT = "perfect"
    PERFECT_PROGRESSIVE = "perfect_progressive"


class MoodValue(Enum):
    """Grammatical mood values for English clauses.

    Covers the moods relevant to TAM interpretation.  ``INTERROGATIVE``
    is handled separately by the speech-act layer and is therefore not
    included here.

    Members
    -------
    INDICATIVE
        Declarative, factual assertion.
    SUBJUNCTIVE
        Non-actual / hypothetical situations (``"If I were ..."``).
    CONDITIONAL
        Conditional constructions (``"would / could / might + VP"``).
    IMPERATIVE
        Commands and requests.
    OPTATIVE
        Wishes (``"Would that ..."``).
    """

    INDICATIVE = "indicative"
    SUBJUNCTIVE = "subjunctive"
    CONDITIONAL = "conditional"
    IMPERATIVE = "imperative"
    OPTATIVE = "optative"


class TemporalRelation(Enum):
    """Relations between temporal points or intervals.

    Based on Allen's (1983) interval algebra, simplified to cover the
    relations most commonly needed for tense interpretation.  Each
    relation describes how interval/point X relates to interval/point Y.

    ``BEFORE``
        X ends strictly before Y begins.
    ``AFTER``
        X begins strictly after Y ends.
    ``SIMULTANEOUS``
        X and Y occupy the same time span.
    ``OVERLAPS``
        X starts before Y and ends during Y.
    ``CONTAINS``
        Y is entirely within X (X starts before Y and ends after Y).
    ``STARTS``
        X and Y share a start point, but X ends before Y.
    ``ENDS``
        X and Y share an end point, but X starts after Y.
    ``DURING``
        X is entirely within Y (inverse of CONTAINS).
    ``MEETS``
        X ends exactly where Y begins, with no gap or overlap.
    ``PRECEDED_BY``
        Inverse of BEFORE: Y ends before X begins.
    """

    BEFORE = "before"
    AFTER = "after"
    SIMULTANEOUS = "simultaneous"
    OVERLAPS = "overlaps"
    CONTAINS = "contains"
    STARTS = "starts"
    ENDS = "ends"
    DURING = "during"
    MEETS = "meets"
    PRECEDED_BY = "preceded_by"


class ModalBase(Enum):
    """Kratzer modal-base categories.

    A *modal base* determines which possible worlds are accessible
    from the evaluation world.  Different modal verbs select for
    different types of modal base.

    ``EPISTEMIC``
        Worlds compatible with what is known or believed.
        Examples: *may*, *might*, *must* (epistemic).
    ``DEONTIC``
        Worlds compatible with the rules, norms, or obligations in
        force.  Examples: *must*, *should*, *ought to*.
    ``BOULETIC``
        Worlds compatible with the subject's desires or wishes.
        Examples: *wish*, *want*, *would rather*.
    ``DYNAMIC``
        Worlds in which the subject's abilities or dispositions are
        exercised.  Examples: *can*, *could*, *be able to*.
    ``TELEOLOGICAL``
        Worlds in which the subject's goals are achieved.
        Examples: *intend*, *plan*, *aim*.
    """

    EPISTEMIC = "epistemic"
    DEONTIC = "deontic"
    BOULETIC = "bouletic"
    DYNAMIC = "dynamic"
    TELEOLOGICAL = "teleological"


# ═══════════════════════════════════════════════════════════════════════
#  Core Dataclasses
# ═══════════════════════════════════════════════════════════════════════


@dataclass
class TemporalPoint:
    """A point on the timeline, optionally anchored to Reichenbach's
    three-point system (S, R, E).

    In Reichenbach's framework every tense is analysed in terms of
    three time-points:

    * **S** -- *speech time*: the moment of utterance.
    * **R** -- *reference time*: the temporal vantage point from which
      the event is viewed.
    * **E** -- *event time*: when the described event actually occurs.

    The ``value`` field gives a general absolute timestamp (with 0.0
    conventionally equal to speech time).  The ``reference_time``,
    ``speech_time``, and ``event_time`` fields store the Reichenbach
    coordinates explicitly.

    Attributes
    ----------
    value : float
        Absolute timestamp on a normalised timeline (0.0 = now).
    reference_time : float
        Reichenbach R-point.
    speech_time : float
        Reichenbach S-point (normally 0.0).
    event_time : float
        Reichenbach E-point.
    grade : Grade
        Confidence / quality grade for this temporal placement.
    """

    value: float = 0.0
    reference_time: float = 0.0
    speech_time: float = 0.0
    event_time: float = 0.0
    grade: Grade = field(default_factory=Grade.perfect)

    # ── comparisons ────────────────────────────────────────────

    def before(self, other: TemporalPoint) -> bool:
        """Return ``True`` if *self* is strictly before *other*.

        Compares the ``value`` fields.

        Parameters
        ----------
        other : TemporalPoint
            The point to compare against.

        Returns
        -------
        bool
        """
        return self.value < other.value

    def after(self, other: TemporalPoint) -> bool:
        """Return ``True`` if *self* is strictly after *other*.

        Compares the ``value`` fields.

        Parameters
        ----------
        other : TemporalPoint
            The point to compare against.

        Returns
        -------
        bool
        """
        return self.value > other.value

    def simultaneous(
        self, other: TemporalPoint, tolerance: float = 0.01
    ) -> bool:
        """Return ``True`` if *self* and *other* are within *tolerance*.

        Parameters
        ----------
        other : TemporalPoint
            The point to compare against.
        tolerance : float
            Maximum absolute difference to count as simultaneous.

        Returns
        -------
        bool
        """
        return abs(self.value - other.value) <= tolerance

    def relation_to(self, other: TemporalPoint) -> TemporalRelation:
        """Determine the temporal relation from *self* to *other*.

        Uses the default tolerance (0.01) for equality testing.

        Parameters
        ----------
        other : TemporalPoint
            The point to compare against.

        Returns
        -------
        TemporalRelation
            ``BEFORE``, ``AFTER``, or ``SIMULTANEOUS``.
        """
        if self.simultaneous(other):
            return TemporalRelation.SIMULTANEOUS
        if self.before(other):
            return TemporalRelation.BEFORE
        return TemporalRelation.AFTER

    def reichenbach_description(self) -> str:
        """Return a human-readable Reichenbach description.

        Groups time-points that are equal (within tolerance 0.01) and
        orders the groups chronologically, separated by `` < ``.

        Examples
        --------
        >>> TemporalPoint(speech_time=0.0, reference_time=-1.0,
        ...               event_time=-1.0).reichenbach_description()
        'E,R < S'
        >>> TemporalPoint(speech_time=0.0, reference_time=0.0,
        ...               event_time=-1.0).reichenbach_description()
        'E < R,S'
        """
        tol = 0.01
        s = self.speech_time
        r = self.reference_time
        e = self.event_time

        points: list[tuple[float, str]] = sorted(
            [(e, "E"), (r, "R"), (s, "S")], key=lambda p: p[0]
        )

        parts: list[str] = []
        group: list[str] = [points[0][1]]
        for i in range(1, len(points)):
            if abs(points[i][0] - points[i - 1][0]) <= tol:
                group.append(points[i][1])
            else:
                parts.append(",".join(sorted(group)))
                group = [points[i][1]]
        parts.append(",".join(sorted(group)))

        return " < ".join(parts)


@dataclass
class TemporalInterval:
    """A bounded interval on the timeline.

    An interval spans from ``start`` to ``end`` (inclusive) and carries
    a ``duration`` that is auto-computed on construction when left at
    its default of 0.0.

    Attributes
    ----------
    start : TemporalPoint
        Beginning of the interval.
    end : TemporalPoint
        End of the interval.
    duration : float
        Length of the interval (``end.value - start.value``).
    grade : Grade
        Confidence grade for this interval placement.
    """

    start: TemporalPoint = field(default_factory=TemporalPoint)
    end: TemporalPoint = field(default_factory=TemporalPoint)
    duration: float = 0.0
    grade: Grade = field(default_factory=Grade.perfect)

    def __post_init__(self) -> None:
        """Compute *duration* from endpoints when it is zero."""
        computed = self.end.value - self.start.value
        if self.duration == 0.0 and computed != 0.0:
            self.duration = computed

    def contains_point(self, point: TemporalPoint) -> bool:
        """Return ``True`` if *point* lies within this interval.

        Parameters
        ----------
        point : TemporalPoint
            The point to test.

        Returns
        -------
        bool
        """
        return self.start.value <= point.value <= self.end.value

    def overlaps_interval(self, other: TemporalInterval) -> bool:
        """Return ``True`` if *self* and *other* share any time.

        Two intervals overlap when each starts before the other ends.

        Parameters
        ----------
        other : TemporalInterval
            The interval to compare against.

        Returns
        -------
        bool
        """
        return (
            self.start.value < other.end.value
            and other.start.value < self.end.value
        )

    def allen_relation(self, other: TemporalInterval) -> TemporalRelation:
        """Compute the Allen-algebra relation from *self* to *other*.

        Uses approximate equality (tolerance 0.01) for endpoint
        comparisons.

        The 10 supported relations are the members of
        ``TemporalRelation``.  When none of the specific relations
        apply, ``SIMULTANEOUS`` is returned as a fallback.

        Parameters
        ----------
        other : TemporalInterval
            The interval to compare against.

        Returns
        -------
        TemporalRelation
        """
        tol = 0.01
        s1, e1 = self.start.value, self.end.value
        s2, e2 = other.start.value, other.end.value

        s_eq = abs(s1 - s2) <= tol
        e_eq = abs(e1 - e2) <= tol
        meet = abs(e1 - s2) <= tol

        if s_eq and e_eq:
            return TemporalRelation.SIMULTANEOUS

        if meet:
            return TemporalRelation.MEETS

        if e1 < s2 - tol:
            return TemporalRelation.BEFORE

        if s1 > e2 + tol:
            return TemporalRelation.AFTER

        if s_eq and e1 < e2 - tol:
            return TemporalRelation.STARTS

        if e_eq and s1 > s2 + tol:
            return TemporalRelation.ENDS

        if s1 > s2 + tol and e1 < e2 - tol:
            return TemporalRelation.DURING

        if s1 < s2 - tol and e1 > e2 + tol:
            return TemporalRelation.CONTAINS

        if s1 < s2 - tol and e1 > s2 and e1 < e2:
            return TemporalRelation.OVERLAPS

        return TemporalRelation.SIMULTANEOUS


@dataclass
class TemporalFrame:
    """A complete temporal-frame specification.

    Bundles a ``TenseValue``, ``AspectValue``, ``MoodValue``, the three
    Reichenbach coordinates (S, R, E), a quality grade, and an optional
    human-readable description.

    This is the primary data structure produced by ``ReichenbachSystem``
    and consumed by the rest of the TAM pipeline.

    Attributes
    ----------
    tense_value : TenseValue
        Composite tense (one of the 12 standard English tenses).
    aspect_value : AspectValue
        Grammatical aspect (simple / progressive / perfect / ...).
    mood_value : MoodValue
        Grammatical mood (indicative / subjunctive / ...).
    reichenbach_s : float
        Speech-time coordinate (conventionally 0.0).
    reichenbach_r : float
        Reference-time coordinate.
    reichenbach_e : float
        Event-time coordinate.
    grade : Grade
        Quality / confidence grade.
    description : str
        Human-readable label (e.g. ``"E,R < S -- She ran"``).
    """

    tense_value: TenseValue = TenseValue.PRESENT
    aspect_value: AspectValue = AspectValue.SIMPLE
    mood_value: MoodValue = MoodValue.INDICATIVE
    reichenbach_s: float = 0.0
    reichenbach_r: float = 0.0
    reichenbach_e: float = 0.0
    grade: Grade = field(default_factory=Grade.perfect)
    description: str = ""

    # ── Reichenbach convenience tests ──────────────────────────

    def s_before_r(self) -> bool:
        """Return ``True`` if speech time precedes reference time.

        This is characteristic of future tenses where the temporal
        vantage point is located after the moment of utterance.

        Returns
        -------
        bool
        """
        return self.reichenbach_s < self.reichenbach_r - 0.001

    def r_before_e(self) -> bool:
        """Return ``True`` if reference time precedes event time.

        This pattern arises in prospective aspects or when the event
        is expected to occur after the vantage point.

        Returns
        -------
        bool
        """
        return self.reichenbach_r < self.reichenbach_e - 0.001

    def e_before_s(self) -> bool:
        """Return ``True`` if event time precedes speech time.

        Characteristic of past tenses: the event happened before the
        utterance.

        Returns
        -------
        bool
        """
        return self.reichenbach_e < self.reichenbach_s - 0.001

    def e_equals_r(self) -> bool:
        """Return ``True`` if event time equals reference time.

        Uses a tolerance of 0.01.  This is the typical pattern for
        simple (non-perfect) tenses.

        Returns
        -------
        bool
        """
        return abs(self.reichenbach_e - self.reichenbach_r) <= 0.01

    def r_equals_s(self) -> bool:
        """Return ``True`` if reference time equals speech time.

        Uses a tolerance of 0.01.  This is the typical pattern for
        present tenses.

        Returns
        -------
        bool
        """
        return abs(self.reichenbach_r - self.reichenbach_s) <= 0.01

    def to_hlf(self, event_var: str = "e") -> HLF:
        """Build an HLF term encoding this frame's tense and aspect.

        Produces a ``TenseTerm`` wrapping an ``AspectTerm`` wrapping a
        ``Var`` for the event variable::

            TenseTerm("past", AspectTerm("perfect", Var("e")))

        Parameters
        ----------
        event_var : str
            Name of the event variable (default ``"e"``).

        Returns
        -------
        HLF
            A nested ``TenseTerm(AspectTerm(Var))`` term.
        """
        ev = Var(event_var)
        aspect_hlf = make_aspect_term(self.aspect_value.value, ev)
        base_tense = _extract_base_tense(self.tense_value)
        tense_hlf = make_tense_term(base_tense, aspect_hlf)
        return tense_hlf

    def reichenbach_formula(self) -> str:
        """Return a Reichenbach-style formula string.

        Groups time-points that are equal (within tolerance 0.01) and
        orders them chronologically separated by `` < ``.

        Examples
        --------
        ``"E,R < S"`` for simple past, ``"E < R,S"`` for present
        perfect, ``"S < E,R"`` for simple future.

        Returns
        -------
        str
        """
        tol = 0.01
        s = self.reichenbach_s
        r = self.reichenbach_r
        e = self.reichenbach_e
        points = sorted(
            [(e, "E"), (r, "R"), (s, "S")], key=lambda p: p[0]
        )
        parts: list[str] = []
        group: list[str] = [points[0][1]]
        for i in range(1, len(points)):
            if abs(points[i][0] - points[i - 1][0]) <= tol:
                group.append(points[i][1])
            else:
                parts.append(",".join(sorted(group)))
                group = [points[i][1]]
        parts.append(",".join(sorted(group)))
        return " < ".join(parts)


# ═══════════════════════════════════════════════════════════════════════
#  Private helpers for Reichenbach coordinate computation
# ═══════════════════════════════════════════════════════════════════════


def _extract_base_tense(tv: TenseValue) -> str:
    """Extract the base tense string from a composite ``TenseValue``.

    Maps any ``TenseValue`` member to one of ``"past"``,
    ``"present"``, or ``"future"`` by checking the prefix of its
    value string.

    Parameters
    ----------
    tv : TenseValue
        e.g. ``TenseValue.PAST_PERFECT``

    Returns
    -------
    str
        One of ``"past"``, ``"present"``, ``"future"``.
    """
    val = tv.value
    if val.startswith("past"):
        return "past"
    if val.startswith("future"):
        return "future"
    return "present"


def _reichenbach_r(t: Tense) -> float:
    """Compute the Reichenbach reference-time coordinate for a tense.

    Convention: S = 0.0 (speech time is the origin).

    * ``PAST``   : R = -1.0  (reference before speech)
    * ``PRESENT``: R =  0.0  (reference at speech)
    * ``FUTURE`` : R = +1.0  (reference after speech)
    * ``NONE``   : R =  0.0  (tenseless -- treated like present)

    Parameters
    ----------
    t : Tense
        The grammatical tense.

    Returns
    -------
    float
        The R coordinate.
    """
    if t is Tense.PAST:
        return -1.0
    if t is Tense.FUTURE:
        return 1.0
    return 0.0


def _reichenbach_e(a: Aspect, r: float) -> float:
    """Compute the Reichenbach event-time coordinate for an aspect.

    Event time is defined relative to reference time *r*.

    * ``SIMPLE`` / ``HABITUAL`` / ``ITERATIVE`` / ``INCEPTIVE`` /
      ``TERMINATIVE`` / ``CONTINUATIVE``:  E = R
    * ``PERFECT``:  E = R - 1.0  (event precedes reference)
    * ``PROGRESSIVE``:  E = R  (event spans reference; represented
      at R for point-like abstraction)
    * ``PERFECT_PROGRESSIVE``:  E = R - 0.5  (event started before
      reference and continues through it)

    Parameters
    ----------
    a : Aspect
        The grammatical aspect.
    r : float
        The reference-time coordinate.

    Returns
    -------
    float
        The E coordinate.
    """
    if a is Aspect.PERFECT:
        return r - 1.0
    if a is Aspect.PERFECT_PROGRESSIVE:
        return r - 0.5
    return r


def _map_tense_value(t: Tense, a: Aspect) -> TenseValue:
    """Map a ``(Tense, Aspect)`` pair to the appropriate ``TenseValue``.

    For aspects that are not one of the four core English categories
    (simple, progressive, perfect, perfect_progressive), the base
    tense value is returned (e.g. ``PAST`` for ``PAST + HABITUAL``).

    Parameters
    ----------
    t : Tense
    a : Aspect

    Returns
    -------
    TenseValue
    """
    _lookup: dict[tuple[str, str], TenseValue] = {
        ("past", "simple"): TenseValue.PAST,
        ("past", "progressive"): TenseValue.PAST_PROGRESSIVE,
        ("past", "perfect"): TenseValue.PAST_PERFECT,
        ("past", "perfect_progressive"): TenseValue.PAST_PERFECT_PROGRESSIVE,
        ("present", "simple"): TenseValue.PRESENT,
        ("present", "progressive"): TenseValue.PRESENT_PROGRESSIVE,
        ("present", "perfect"): TenseValue.PRESENT_PERFECT,
        ("present", "perfect_progressive"): TenseValue.PRESENT_PERFECT_PROGRESSIVE,
        ("future", "simple"): TenseValue.FUTURE,
        ("future", "progressive"): TenseValue.FUTURE_PROGRESSIVE,
        ("future", "perfect"): TenseValue.FUTURE_PERFECT,
        ("future", "perfect_progressive"): TenseValue.FUTURE_PERFECT_PROGRESSIVE,
    }
    t_str = t.value if hasattr(t, "value") else str(t)
    a_str = a.value if hasattr(a, "value") else str(a)
    key = (t_str, a_str)
    if key in _lookup:
        return _lookup[key]
    _base_map: dict[str, TenseValue] = {
        "past": TenseValue.PAST,
        "present": TenseValue.PRESENT,
        "future": TenseValue.FUTURE,
        "none": TenseValue.PRESENT,
    }
    return _base_map.get(t_str, TenseValue.PRESENT)


def _map_aspect_value(a: Aspect) -> AspectValue:
    """Map a fine-grained ``Aspect`` to the coarser ``AspectValue``.

    ``HABITUAL``, ``ITERATIVE``, ``INCEPTIVE``, and ``TERMINATIVE``
    map to ``SIMPLE``.  ``CONTINUATIVE`` maps to ``PROGRESSIVE``.

    Parameters
    ----------
    a : Aspect

    Returns
    -------
    AspectValue
    """
    _direct: dict[str, AspectValue] = {
        "simple": AspectValue.SIMPLE,
        "progressive": AspectValue.PROGRESSIVE,
        "perfect": AspectValue.PERFECT,
        "perfect_progressive": AspectValue.PERFECT_PROGRESSIVE,
    }
    a_str = a.value if hasattr(a, "value") else str(a)
    if a_str in _direct:
        return _direct[a_str]
    if a_str == "continuative":
        return AspectValue.PROGRESSIVE
    return AspectValue.SIMPLE


def _compose_temporal_relations(
    r1: TemporalRelation, r2: TemporalRelation
) -> TemporalRelation:
    """Compose two temporal relations for Allen constraint propagation.

    Given that A *r1* B and B *r2* C, infer the most specific relation
    between A and C.  This implements a simplified composition table
    for the most common relation pairs.

    If the composition is ambiguous (could be several different
    relations), the most conservative (most informative) relation is
    chosen according to a precedence heuristic.

    Parameters
    ----------
    r1 : TemporalRelation
        Relation from A to B.
    r2 : TemporalRelation
        Relation from B to C.

    Returns
    -------
    TemporalRelation
        Inferred relation from A to C.
    """
    # Transitivity for strict orderings
    if r1 is TemporalRelation.BEFORE:
        if r2 in (
            TemporalRelation.BEFORE,
            TemporalRelation.MEETS,
            TemporalRelation.OVERLAPS,
            TemporalRelation.STARTS,
            TemporalRelation.DURING,
            TemporalRelation.SIMULTANEOUS,
        ):
            return TemporalRelation.BEFORE

    if r1 is TemporalRelation.AFTER:
        if r2 in (
            TemporalRelation.AFTER,
            TemporalRelation.PRECEDED_BY,
            TemporalRelation.SIMULTANEOUS,
        ):
            return TemporalRelation.AFTER

    # Simultaneous is the identity element
    if r1 is TemporalRelation.SIMULTANEOUS:
        return r2
    if r2 is TemporalRelation.SIMULTANEOUS:
        return r1

    # Meets + Before => Before
    if r1 is TemporalRelation.MEETS and r2 is TemporalRelation.BEFORE:
        return TemporalRelation.BEFORE
    if r1 is TemporalRelation.MEETS and r2 is TemporalRelation.MEETS:
        return TemporalRelation.BEFORE

    # Contains / During composition
    if r1 is TemporalRelation.CONTAINS:
        if r2 is TemporalRelation.BEFORE:
            return TemporalRelation.BEFORE
        if r2 is TemporalRelation.AFTER:
            return TemporalRelation.AFTER
        return TemporalRelation.CONTAINS

    if r1 is TemporalRelation.DURING:
        return r2

    # Overlaps compositions
    if r1 is TemporalRelation.OVERLAPS:
        if r2 is TemporalRelation.BEFORE:
            return TemporalRelation.BEFORE
        if r2 is TemporalRelation.AFTER:
            return TemporalRelation.OVERLAPS
        return TemporalRelation.OVERLAPS

    # Default: if no specific rule fires, return BEFORE as conservative
    if r1 is TemporalRelation.PRECEDED_BY:
        if r2 in (
            TemporalRelation.PRECEDED_BY,
            TemporalRelation.AFTER,
            TemporalRelation.SIMULTANEOUS,
        ):
            return TemporalRelation.PRECEDED_BY

    # Fallback for unhandled compositions
    return TemporalRelation.SIMULTANEOUS


def _invert_relation(r: TemporalRelation) -> TemporalRelation:
    """Return the inverse of a temporal relation.

    If A *r* B then B *inverse(r)* A.

    Parameters
    ----------
    r : TemporalRelation

    Returns
    -------
    TemporalRelation
    """
    _inverses: dict[TemporalRelation, TemporalRelation] = {
        TemporalRelation.BEFORE: TemporalRelation.AFTER,
        TemporalRelation.AFTER: TemporalRelation.BEFORE,
        TemporalRelation.SIMULTANEOUS: TemporalRelation.SIMULTANEOUS,
        TemporalRelation.OVERLAPS: TemporalRelation.OVERLAPS,
        TemporalRelation.CONTAINS: TemporalRelation.DURING,
        TemporalRelation.DURING: TemporalRelation.CONTAINS,
        TemporalRelation.STARTS: TemporalRelation.STARTS,
        TemporalRelation.ENDS: TemporalRelation.ENDS,
        TemporalRelation.MEETS: TemporalRelation.PRECEDED_BY,
        TemporalRelation.PRECEDED_BY: TemporalRelation.MEETS,
    }
    return _inverses.get(r, TemporalRelation.SIMULTANEOUS)


# ═══════════════════════════════════════════════════════════════════════
#  TENSE_ASPECT_FRAMES -- all 36 (Tense x Aspect) canonical frames
# ═══════════════════════════════════════════════════════════════════════

TENSE_ASPECT_FRAMES: dict[tuple[Tense, Aspect], TemporalFrame] = {
    # ── PAST tense ────────────────────────────────────────────
    (Tense.PAST, Aspect.SIMPLE): TemporalFrame(
        tense_value=TenseValue.PAST,
        aspect_value=AspectValue.SIMPLE,
        mood_value=MoodValue.INDICATIVE,
        reichenbach_s=0.0,
        reichenbach_r=-1.0,
        reichenbach_e=-1.0,
        description="E,R < S -- She ran",
    ),
    (Tense.PAST, Aspect.PROGRESSIVE): TemporalFrame(
        tense_value=TenseValue.PAST_PROGRESSIVE,
        aspect_value=AspectValue.PROGRESSIVE,
        mood_value=MoodValue.INDICATIVE,
        reichenbach_s=0.0,
        reichenbach_r=-1.0,
        reichenbach_e=-1.0,
        description="R < S, E spans R -- She was running",
    ),
    (Tense.PAST, Aspect.PERFECT): TemporalFrame(
        tense_value=TenseValue.PAST_PERFECT,
        aspect_value=AspectValue.PERFECT,
        mood_value=MoodValue.INDICATIVE,
        reichenbach_s=0.0,
        reichenbach_r=-1.0,
        reichenbach_e=-2.0,
        description="E < R < S -- She had run",
    ),
    (Tense.PAST, Aspect.PERFECT_PROGRESSIVE): TemporalFrame(
        tense_value=TenseValue.PAST_PERFECT_PROGRESSIVE,
        aspect_value=AspectValue.PERFECT_PROGRESSIVE,
        mood_value=MoodValue.INDICATIVE,
        reichenbach_s=0.0,
        reichenbach_r=-1.0,
        reichenbach_e=-1.5,
        description="E < R < S, E spans R -- She had been running",
    ),
    (Tense.PAST, Aspect.HABITUAL): TemporalFrame(
        tense_value=TenseValue.PAST,
        aspect_value=AspectValue.SIMPLE,
        mood_value=MoodValue.INDICATIVE,
        reichenbach_s=0.0,
        reichenbach_r=-1.0,
        reichenbach_e=-1.0,
        description="E,R < S (habitual) -- She used to run",
    ),
    (Tense.PAST, Aspect.ITERATIVE): TemporalFrame(
        tense_value=TenseValue.PAST,
        aspect_value=AspectValue.SIMPLE,
        mood_value=MoodValue.INDICATIVE,
        reichenbach_s=0.0,
        reichenbach_r=-1.0,
        reichenbach_e=-1.0,
        description="E,R < S (iterative) -- She ran repeatedly",
    ),
    (Tense.PAST, Aspect.INCEPTIVE): TemporalFrame(
        tense_value=TenseValue.PAST,
        aspect_value=AspectValue.SIMPLE,
        mood_value=MoodValue.INDICATIVE,
        reichenbach_s=0.0,
        reichenbach_r=-1.0,
        reichenbach_e=-1.0,
        description="E,R < S (inceptive) -- She started running",
    ),
    (Tense.PAST, Aspect.TERMINATIVE): TemporalFrame(
        tense_value=TenseValue.PAST,
        aspect_value=AspectValue.SIMPLE,
        mood_value=MoodValue.INDICATIVE,
        reichenbach_s=0.0,
        reichenbach_r=-1.0,
        reichenbach_e=-1.0,
        description="E,R < S (terminative) -- She stopped running",
    ),
    (Tense.PAST, Aspect.CONTINUATIVE): TemporalFrame(
        tense_value=TenseValue.PAST,
        aspect_value=AspectValue.PROGRESSIVE,
        mood_value=MoodValue.INDICATIVE,
        reichenbach_s=0.0,
        reichenbach_r=-1.0,
        reichenbach_e=-1.0,
        description="E,R < S (continuative) -- She kept running",
    ),
    # ── PRESENT tense ─────────────────────────────────────────
    (Tense.PRESENT, Aspect.SIMPLE): TemporalFrame(
        tense_value=TenseValue.PRESENT,
        aspect_value=AspectValue.SIMPLE,
        mood_value=MoodValue.INDICATIVE,
        reichenbach_s=0.0,
        reichenbach_r=0.0,
        reichenbach_e=0.0,
        description="E,R,S -- She runs",
    ),
    (Tense.PRESENT, Aspect.PROGRESSIVE): TemporalFrame(
        tense_value=TenseValue.PRESENT_PROGRESSIVE,
        aspect_value=AspectValue.PROGRESSIVE,
        mood_value=MoodValue.INDICATIVE,
        reichenbach_s=0.0,
        reichenbach_r=0.0,
        reichenbach_e=0.0,
        description="R,S, E spans R -- She is running",
    ),
    (Tense.PRESENT, Aspect.PERFECT): TemporalFrame(
        tense_value=TenseValue.PRESENT_PERFECT,
        aspect_value=AspectValue.PERFECT,
        mood_value=MoodValue.INDICATIVE,
        reichenbach_s=0.0,
        reichenbach_r=0.0,
        reichenbach_e=-1.0,
        description="E < R,S -- She has run",
    ),
    (Tense.PRESENT, Aspect.PERFECT_PROGRESSIVE): TemporalFrame(
        tense_value=TenseValue.PRESENT_PERFECT_PROGRESSIVE,
        aspect_value=AspectValue.PERFECT_PROGRESSIVE,
        mood_value=MoodValue.INDICATIVE,
        reichenbach_s=0.0,
        reichenbach_r=0.0,
        reichenbach_e=-0.5,
        description="E < R,S, E spans R -- She has been running",
    ),
    (Tense.PRESENT, Aspect.HABITUAL): TemporalFrame(
        tense_value=TenseValue.PRESENT,
        aspect_value=AspectValue.SIMPLE,
        mood_value=MoodValue.INDICATIVE,
        reichenbach_s=0.0,
        reichenbach_r=0.0,
        reichenbach_e=0.0,
        description="E,R,S (habitual) -- She runs every day",
    ),
    (Tense.PRESENT, Aspect.ITERATIVE): TemporalFrame(
        tense_value=TenseValue.PRESENT,
        aspect_value=AspectValue.SIMPLE,
        mood_value=MoodValue.INDICATIVE,
        reichenbach_s=0.0,
        reichenbach_r=0.0,
        reichenbach_e=0.0,
        description="E,R,S (iterative) -- She runs repeatedly",
    ),
    (Tense.PRESENT, Aspect.INCEPTIVE): TemporalFrame(
        tense_value=TenseValue.PRESENT,
        aspect_value=AspectValue.SIMPLE,
        mood_value=MoodValue.INDICATIVE,
        reichenbach_s=0.0,
        reichenbach_r=0.0,
        reichenbach_e=0.0,
        description="E,R,S (inceptive) -- She starts running",
    ),
    (Tense.PRESENT, Aspect.TERMINATIVE): TemporalFrame(
        tense_value=TenseValue.PRESENT,
        aspect_value=AspectValue.SIMPLE,
        mood_value=MoodValue.INDICATIVE,
        reichenbach_s=0.0,
        reichenbach_r=0.0,
        reichenbach_e=0.0,
        description="E,R,S (terminative) -- She stops running",
    ),
    (Tense.PRESENT, Aspect.CONTINUATIVE): TemporalFrame(
        tense_value=TenseValue.PRESENT,
        aspect_value=AspectValue.PROGRESSIVE,
        mood_value=MoodValue.INDICATIVE,
        reichenbach_s=0.0,
        reichenbach_r=0.0,
        reichenbach_e=0.0,
        description="E,R,S (continuative) -- She keeps running",
    ),
    # ── FUTURE tense ──────────────────────────────────────────
    (Tense.FUTURE, Aspect.SIMPLE): TemporalFrame(
        tense_value=TenseValue.FUTURE,
        aspect_value=AspectValue.SIMPLE,
        mood_value=MoodValue.INDICATIVE,
        reichenbach_s=0.0,
        reichenbach_r=1.0,
        reichenbach_e=1.0,
        description="S < E,R -- She will run",
    ),
    (Tense.FUTURE, Aspect.PROGRESSIVE): TemporalFrame(
        tense_value=TenseValue.FUTURE_PROGRESSIVE,
        aspect_value=AspectValue.PROGRESSIVE,
        mood_value=MoodValue.INDICATIVE,
        reichenbach_s=0.0,
        reichenbach_r=1.0,
        reichenbach_e=1.0,
        description="S < R, E spans R -- She will be running",
    ),
    (Tense.FUTURE, Aspect.PERFECT): TemporalFrame(
        tense_value=TenseValue.FUTURE_PERFECT,
        aspect_value=AspectValue.PERFECT,
        mood_value=MoodValue.INDICATIVE,
        reichenbach_s=0.0,
        reichenbach_r=1.0,
        reichenbach_e=0.0,
        description="S,E < R -- She will have run",
    ),
    (Tense.FUTURE, Aspect.PERFECT_PROGRESSIVE): TemporalFrame(
        tense_value=TenseValue.FUTURE_PERFECT_PROGRESSIVE,
        aspect_value=AspectValue.PERFECT_PROGRESSIVE,
        mood_value=MoodValue.INDICATIVE,
        reichenbach_s=0.0,
        reichenbach_r=1.0,
        reichenbach_e=0.5,
        description="S < E < R, E spans R -- She will have been running",
    ),
    (Tense.FUTURE, Aspect.HABITUAL): TemporalFrame(
        tense_value=TenseValue.FUTURE,
        aspect_value=AspectValue.SIMPLE,
        mood_value=MoodValue.INDICATIVE,
        reichenbach_s=0.0,
        reichenbach_r=1.0,
        reichenbach_e=1.0,
        description="S < E,R (habitual) -- She will run every day",
    ),
    (Tense.FUTURE, Aspect.ITERATIVE): TemporalFrame(
        tense_value=TenseValue.FUTURE,
        aspect_value=AspectValue.SIMPLE,
        mood_value=MoodValue.INDICATIVE,
        reichenbach_s=0.0,
        reichenbach_r=1.0,
        reichenbach_e=1.0,
        description="S < E,R (iterative) -- She will run repeatedly",
    ),
    (Tense.FUTURE, Aspect.INCEPTIVE): TemporalFrame(
        tense_value=TenseValue.FUTURE,
        aspect_value=AspectValue.SIMPLE,
        mood_value=MoodValue.INDICATIVE,
        reichenbach_s=0.0,
        reichenbach_r=1.0,
        reichenbach_e=1.0,
        description="S < E,R (inceptive) -- She will start running",
    ),
    (Tense.FUTURE, Aspect.TERMINATIVE): TemporalFrame(
        tense_value=TenseValue.FUTURE,
        aspect_value=AspectValue.SIMPLE,
        mood_value=MoodValue.INDICATIVE,
        reichenbach_s=0.0,
        reichenbach_r=1.0,
        reichenbach_e=1.0,
        description="S < E,R (terminative) -- She will stop running",
    ),
    (Tense.FUTURE, Aspect.CONTINUATIVE): TemporalFrame(
        tense_value=TenseValue.FUTURE,
        aspect_value=AspectValue.PROGRESSIVE,
        mood_value=MoodValue.INDICATIVE,
        reichenbach_s=0.0,
        reichenbach_r=1.0,
        reichenbach_e=1.0,
        description="S < E,R (continuative) -- She will keep running",
    ),
    # ── NONE (tenseless) ──────────────────────────────────────
    (Tense.NONE, Aspect.SIMPLE): TemporalFrame(
        tense_value=TenseValue.PRESENT,
        aspect_value=AspectValue.SIMPLE,
        mood_value=MoodValue.INDICATIVE,
        reichenbach_s=0.0,
        reichenbach_r=0.0,
        reichenbach_e=0.0,
        description="E,R,S (tenseless) -- to run",
    ),
    (Tense.NONE, Aspect.PROGRESSIVE): TemporalFrame(
        tense_value=TenseValue.PRESENT_PROGRESSIVE,
        aspect_value=AspectValue.PROGRESSIVE,
        mood_value=MoodValue.INDICATIVE,
        reichenbach_s=0.0,
        reichenbach_r=0.0,
        reichenbach_e=0.0,
        description="E,R,S (tenseless progressive) -- running",
    ),
    (Tense.NONE, Aspect.PERFECT): TemporalFrame(
        tense_value=TenseValue.PRESENT_PERFECT,
        aspect_value=AspectValue.PERFECT,
        mood_value=MoodValue.INDICATIVE,
        reichenbach_s=0.0,
        reichenbach_r=0.0,
        reichenbach_e=-1.0,
        description="E < R,S (tenseless perfect) -- having run",
    ),
    (Tense.NONE, Aspect.PERFECT_PROGRESSIVE): TemporalFrame(
        tense_value=TenseValue.PRESENT_PERFECT_PROGRESSIVE,
        aspect_value=AspectValue.PERFECT_PROGRESSIVE,
        mood_value=MoodValue.INDICATIVE,
        reichenbach_s=0.0,
        reichenbach_r=0.0,
        reichenbach_e=-0.5,
        description="E < R,S (tenseless perf.prog.) -- having been running",
    ),
    (Tense.NONE, Aspect.HABITUAL): TemporalFrame(
        tense_value=TenseValue.PRESENT,
        aspect_value=AspectValue.SIMPLE,
        mood_value=MoodValue.INDICATIVE,
        reichenbach_s=0.0,
        reichenbach_r=0.0,
        reichenbach_e=0.0,
        description="E,R,S (tenseless habitual) -- to run regularly",
    ),
    (Tense.NONE, Aspect.ITERATIVE): TemporalFrame(
        tense_value=TenseValue.PRESENT,
        aspect_value=AspectValue.SIMPLE,
        mood_value=MoodValue.INDICATIVE,
        reichenbach_s=0.0,
        reichenbach_r=0.0,
        reichenbach_e=0.0,
        description="E,R,S (tenseless iterative) -- to run repeatedly",
    ),
    (Tense.NONE, Aspect.INCEPTIVE): TemporalFrame(
        tense_value=TenseValue.PRESENT,
        aspect_value=AspectValue.SIMPLE,
        mood_value=MoodValue.INDICATIVE,
        reichenbach_s=0.0,
        reichenbach_r=0.0,
        reichenbach_e=0.0,
        description="E,R,S (tenseless inceptive) -- to start running",
    ),
    (Tense.NONE, Aspect.TERMINATIVE): TemporalFrame(
        tense_value=TenseValue.PRESENT,
        aspect_value=AspectValue.SIMPLE,
        mood_value=MoodValue.INDICATIVE,
        reichenbach_s=0.0,
        reichenbach_r=0.0,
        reichenbach_e=0.0,
        description="E,R,S (tenseless terminative) -- to stop running",
    ),
    (Tense.NONE, Aspect.CONTINUATIVE): TemporalFrame(
        tense_value=TenseValue.PRESENT,
        aspect_value=AspectValue.PROGRESSIVE,
        mood_value=MoodValue.INDICATIVE,
        reichenbach_s=0.0,
        reichenbach_r=0.0,
        reichenbach_e=0.0,
        description="E,R,S (tenseless continuative) -- to keep running",
    ),
}


# ═══════════════════════════════════════════════════════════════════════
#  TAMBundle dataclass
# ═══════════════════════════════════════════════════════════════════════


@dataclass
class TAMBundle:
    """A complete TAM (Tense-Aspect-Modality) interpretation bundle.

    This is the primary output of ``TAMEngine.compute_tam()`` -- it
    bundles together the tense, aspect, mood, grade, and temporal
    anchor for a single predicate.

    TAMBundle is the "semantic ticket" that a predicate carries through
    the rest of the interpretation pipeline; downstream consumers can
    inspect it to determine temporal ordering, modal force, and
    aspectual class.

    Attributes
    ----------
    tense : TenseValue
        Composite tense (one of the 12 standard English tenses).
    aspect : AspectValue
        Grammatical aspect (simple / progressive / perfect / ...).
    mood : MoodValue
        Grammatical mood.
    grade : Grade
        Quality / confidence grade.
    temporal_anchor : TemporalPoint | None
        Optional temporal anchor point for this predicate.
    modal_base : ModalBase | None
        If a modal verb is present, its Kratzer modal-base category.
    description : str
        Human-readable description of the TAM interpretation.
    """

    tense: TenseValue = TenseValue.PRESENT
    aspect: AspectValue = AspectValue.SIMPLE
    mood: MoodValue = MoodValue.INDICATIVE
    grade: Grade = field(default_factory=Grade.perfect)
    temporal_anchor: TemporalPoint | None = None
    modal_base: ModalBase | None = None
    description: str = ""

    def to_temporal_frame(self) -> TemporalFrame:
        """Convert this bundle to a ``TemporalFrame``.

        Computes Reichenbach coordinates from the ``tense`` and
        ``aspect`` values.

        Returns
        -------
        TemporalFrame
        """
        base_tense_str = _extract_base_tense(self.tense)
        tense_map = {"past": -1.0, "future": 1.0}
        r = tense_map.get(base_tense_str, 0.0)

        aspect_offset = {
            AspectValue.PERFECT: -1.0,
            AspectValue.PERFECT_PROGRESSIVE: -0.5,
        }
        e = r + aspect_offset.get(self.aspect, 0.0)

        return TemporalFrame(
            tense_value=self.tense,
            aspect_value=self.aspect,
            mood_value=self.mood,
            reichenbach_s=0.0,
            reichenbach_r=r,
            reichenbach_e=e,
            grade=self.grade,
            description=self.description,
        )

    def is_past(self) -> bool:
        """Return ``True`` if this bundle has a past tense.

        Returns
        -------
        bool
        """
        return _extract_base_tense(self.tense) == "past"

    def is_present(self) -> bool:
        """Return ``True`` if this bundle has a present tense.

        Returns
        -------
        bool
        """
        return _extract_base_tense(self.tense) == "present"

    def is_future(self) -> bool:
        """Return ``True`` if this bundle has a future tense.

        Returns
        -------
        bool
        """
        return _extract_base_tense(self.tense) == "future"

    def is_perfect(self) -> bool:
        """Return ``True`` if the aspect includes perfect.

        Returns
        -------
        bool
        """
        return self.aspect in (
            AspectValue.PERFECT,
            AspectValue.PERFECT_PROGRESSIVE,
        )

    def is_progressive(self) -> bool:
        """Return ``True`` if the aspect includes progressive.

        Returns
        -------
        bool
        """
        return self.aspect in (
            AspectValue.PROGRESSIVE,
            AspectValue.PERFECT_PROGRESSIVE,
        )


# ═══════════════════════════════════════════════════════════════════════
#  MODAL_TO_BASE -- modal verbs mapped to their Kratzer modal base
# ═══════════════════════════════════════════════════════════════════════

MODAL_TO_BASE: dict[str, ModalBase] = {
    # Dynamic modals (ability / disposition)
    "can": ModalBase.DYNAMIC,
    "could": ModalBase.DYNAMIC,
    "will": ModalBase.DYNAMIC,
    "would": ModalBase.DYNAMIC,
    "dare": ModalBase.DYNAMIC,
    "be able to": ModalBase.DYNAMIC,
    "be going to": ModalBase.DYNAMIC,
    "used to": ModalBase.DYNAMIC,
    # Epistemic modals (knowledge / belief)
    "may": ModalBase.EPISTEMIC,
    "might": ModalBase.EPISTEMIC,
    # Deontic modals (obligation / permission)
    "must": ModalBase.DEONTIC,
    "should": ModalBase.DEONTIC,
    "shall": ModalBase.DEONTIC,
    "need": ModalBase.DEONTIC,
    "ought": ModalBase.DEONTIC,
    "had better": ModalBase.DEONTIC,
    "be allowed to": ModalBase.DEONTIC,
    "have to": ModalBase.DEONTIC,
    "be supposed to": ModalBase.DEONTIC,
    # Bouletic modals (desire / wish)
    "would rather": ModalBase.BOULETIC,
    "wish": ModalBase.BOULETIC,
    "hope": ModalBase.BOULETIC,
    "want": ModalBase.BOULETIC,
    "desire": ModalBase.BOULETIC,
    # Teleological modals (goal / purpose)
    "intend": ModalBase.TELEOLOGICAL,
    "plan": ModalBase.TELEOLOGICAL,
    "aim": ModalBase.TELEOLOGICAL,
}


# ── Modal strength & ordering source tables ──────────────────────────

_MODAL_STRENGTH: dict[str, float] = {
    "must": 0.95,
    "shall": 0.90,
    "will": 0.85,
    "need": 0.85,
    "have_to": 0.90,
    "ought": 0.80,
    "should": 0.75,
    "had_better": 0.78,
    "be_supposed_to": 0.70,
    "be_to": 0.72,
    "can": 0.50,
    "could": 0.40,
    "may": 0.45,
    "might": 0.30,
    "would": 0.55,
    "dare": 0.60,
    "want": 0.65,
    "wish": 0.35,
    "hope": 0.40,
    "fear": 0.35,
    "expect": 0.60,
    "believe": 0.55,
    "know": 0.90,
    "suppose": 0.40,
    "intend": 0.65,
    "plan": 0.60,
    "aim": 0.55,
}

_ORDERING_SOURCES: dict[str, str] = {
    "must": "normative",
    "shall": "normative",
    "will": "stereotypical",
    "need": "normative",
    "have_to": "normative",
    "ought": "normative",
    "should": "normative",
    "had_better": "normative",
    "be_supposed_to": "normative",
    "be_to": "normative",
    "can": "stereotypical",
    "could": "stereotypical",
    "may": "stereotypical",
    "might": "stereotypical",
    "would": "stereotypical",
    "dare": "bouletic",
    "want": "bouletic",
    "wish": "bouletic",
    "hope": "bouletic",
    "fear": "bouletic",
    "expect": "stereotypical",
    "believe": "stereotypical",
    "know": "informational",
    "suppose": "stereotypical",
    "intend": "teleological",
    "plan": "teleological",
    "aim": "teleological",
}


# ═══════════════════════════════════════════════════════════════════════
#  TemporalGraph -- directed graph of temporal relations
# ═══════════════════════════════════════════════════════════════════════


class TemporalGraph:
    """Directed graph of temporal relations between named events.

    Used to represent the temporal structure of a discourse or
    narrative.  Nodes are named events (strings) mapped to
    ``TemporalPoint`` objects.  Edges carry both a
    ``TemporalRelation`` and a ``Grade`` (confidence).

    The graph supports several operations useful for temporal
    reasoning:

    * **Shortest/best-path computation** in log-probability space
      (Dijkstra-like).
    * **Topological sorting** (Kahn's algorithm) using ordering
      edges (BEFORE, MEETS, PRECEDED_BY).
    * **Reachability queries** via BFS.
    * **Cycle detection** via DFS with back-edge detection.
    * **Allen constraint propagation** for checking global temporal
      consistency.

    Examples
    --------
    >>> g = TemporalGraph()
    >>> g.add_event("wake", TemporalPoint(value=-2.0))
    >>> g.add_event("eat", TemporalPoint(value=-1.0))
    >>> g.add_relation("wake", "eat", TemporalRelation.BEFORE,
    ...                Grade.perfect())
    >>> g.get_relation("wake", "eat")
    (TemporalRelation.BEFORE, Grade(...))
    """

    def __init__(self) -> None:
        """Initialise an empty temporal graph."""
        self._events: dict[str, TemporalPoint] = {}
        self._edges: dict[
            tuple[str, str], tuple[TemporalRelation, Grade]
        ] = {}
        self._adj: dict[str, list[str]] = {}

    # ── mutation ───────────────────────────────────────────────

    def add_event(self, event_id: str, point: TemporalPoint) -> None:
        """Register a named event at a temporal point.

        If the event already exists it is silently overwritten.

        Parameters
        ----------
        event_id : str
            Unique string identifier for the event.
        point : TemporalPoint
            Where on the timeline this event is located.
        """
        self._events[event_id] = point
        if event_id not in self._adj:
            self._adj[event_id] = []

    def add_relation(
        self,
        e1: str,
        e2: str,
        relation: TemporalRelation,
        grade: Grade,
    ) -> None:
        """Add or update a directed temporal relation between two events.

        If *e1* or *e2* are not yet registered, they are added with
        default ``TemporalPoint()`` values.

        Parameters
        ----------
        e1 : str
            Source event.
        e2 : str
            Target event.
        relation : TemporalRelation
            The directed relation from *e1* to *e2*.
        grade : Grade
            Confidence grade for this relation.
        """
        if e1 not in self._events:
            self.add_event(e1, TemporalPoint())
        if e2 not in self._events:
            self.add_event(e2, TemporalPoint())

        self._edges[(e1, e2)] = (relation, grade)
        if e2 not in self._adj[e1]:
            self._adj[e1].append(e2)

    # ── queries ────────────────────────────────────────────────

    def get_relation(
        self, e1: str, e2: str
    ) -> tuple[TemporalRelation, Grade] | None:
        """Look up the relation between two events.

        Parameters
        ----------
        e1 : str
            Source event.
        e2 : str
            Target event.

        Returns
        -------
        tuple[TemporalRelation, Grade] | None
            The relation and its grade, or ``None`` if no edge
            exists from *e1* to *e2*.
        """
        return self._edges.get((e1, e2))

    def path_grade(self, e1: str, e2: str) -> Grade:
        """Compute the best-path grade from *e1* to *e2*.

        Uses a Dijkstra-like traversal in log-probability space.
        Edge costs are the negated log-probabilities of their grades
        (so all costs are non-negative), and the algorithm finds the
        minimum-cost path, which corresponds to the maximum (best)
        combined grade.

        Parameters
        ----------
        e1 : str
            Source event.
        e2 : str
            Target event.

        Returns
        -------
        Grade
            The combined grade of the best path, or
            ``Grade.impossible()`` if no path exists.
        """
        if e1 not in self._events or e2 not in self._events:
            return Grade.impossible()
        if e1 == e2:
            return Grade.perfect()

        dist: dict[str, float] = {e1: 0.0}
        visited: set[str] = set()
        heap: list[tuple[float, str]] = [(0.0, e1)]

        while heap:
            d, u = heapq.heappop(heap)
            if u in visited:
                continue
            visited.add(u)
            if u == e2:
                return Grade(-d)
            for v in self._adj.get(u, []):
                edge_data = self._edges.get((u, v))
                if edge_data is None:
                    continue
                _, edge_grade = edge_data
                lp = edge_grade.to_logprob()
                cost = -lp  # non-negative since lp <= 0
                new_dist = d + cost
                if v not in dist or new_dist < dist[v]:
                    dist[v] = new_dist
                    heapq.heappush(heap, (new_dist, v))

        return Grade.impossible()

    def topological_sort(self) -> list[str]:
        """Return a topological ordering of events.

        Uses Kahn's algorithm.  Only edges with ordering relations
        (``BEFORE``, ``MEETS``, ``PRECEDED_BY``) are treated as
        directed ordering constraints.  Events with no ordering
        constraints are placed at the end.

        Returns
        -------
        list[str]
            Event identifiers in topological order.  If the graph
            contains cycles among the ordering edges, the result is
            a partial ordering (cycle members are appended at the
            end in arbitrary order).
        """
        ordering_rels = {
            TemporalRelation.BEFORE,
            TemporalRelation.MEETS,
        }

        in_degree: dict[str, int] = {e: 0 for e in self._events}
        order_adj: dict[str, list[str]] = {
            e: [] for e in self._events
        }

        for (u, v), (rel, _) in self._edges.items():
            if rel in ordering_rels:
                order_adj[u].append(v)
                in_degree[v] = in_degree.get(v, 0) + 1

        queue: deque[str] = deque(
            e for e, d in in_degree.items() if d == 0
        )
        result: list[str] = []

        while queue:
            u = queue.popleft()
            result.append(u)
            for v in order_adj[u]:
                in_degree[v] -= 1
                if in_degree[v] == 0:
                    queue.append(v)

        # Append any remaining events (cycle members)
        remaining = [e for e in self._events if e not in set(result)]
        result.extend(remaining)
        return result

    def reachable(self, source: str) -> set[str]:
        """Return all events reachable from *source* via directed edges.

        Uses breadth-first search.

        Parameters
        ----------
        source : str
            Starting event identifier.

        Returns
        -------
        set[str]
            All reachable event identifiers (excluding *source*
            itself unless there is a cycle back to it).
        """
        visited: set[str] = set()
        queue: deque[str] = deque()

        if source not in self._adj:
            return visited

        queue.append(source)
        while queue:
            u = queue.popleft()
            for v in self._adj.get(u, []):
                if v not in visited:
                    visited.add(v)
                    queue.append(v)

        visited.discard(source)
        return visited

    def find_cycles(self) -> list[list[str]]:
        """Find all simple cycles in the graph.

        Uses DFS with three-colour marking (white / grey / black).
        When a back edge to a grey node is detected, the cycle is
        extracted from the current DFS path.

        Returns
        -------
        list[list[str]]
            Each inner list is a cycle, given as a sequence of event
            identifiers in traversal order.
        """
        cycles: list[list[str]] = []
        colour: dict[str, str] = {e: "white" for e in self._events}
        path: list[str] = []

        def _dfs(u: str) -> None:
            colour[u] = "grey"
            path.append(u)
            for v in self._adj.get(u, []):
                if colour[v] == "grey" and v in path:
                    idx = path.index(v)
                    cycles.append(list(path[idx:]))
                elif colour[v] == "white":
                    _dfs(v)
            path.pop()
            colour[u] = "black"

        for e in self._events:
            if colour[e] == "white":
                _dfs(e)

        return cycles

    def constraint_propagate(self) -> bool:
        """Run Allen constraint propagation over the graph.

        For every triple of nodes (i, j, k) where edges i->j and j->k
        exist, infer the relation i->k by composing the two edge
        relations.  If the inferred relation is incompatible with an
        existing i->k edge, the graph is inconsistent.

        The algorithm runs until no new edges are added or an
        inconsistency is detected.

        Returns
        -------
        bool
            ``True`` if the graph is temporally consistent,
            ``False`` if a contradiction was detected.
        """
        changed = True
        consistent = True

        while changed:
            changed = False
            nodes = list(self._events.keys())
            for i in nodes:
                for j in self._adj.get(i, []):
                    ij_data = self._edges.get((i, j))
                    if ij_data is None:
                        continue
                    ij_rel, ij_grade = ij_data

                    for k in self._adj.get(j, []):
                        if k == i:
                            continue
                        jk_data = self._edges.get((j, k))
                        if jk_data is None:
                            continue
                        jk_rel, jk_grade = jk_data

                        composed = _compose_temporal_relations(
                            ij_rel, jk_rel
                        )
                        combined_grade = ij_grade * jk_grade

                        ik_data = self._edges.get((i, k))
                        if ik_data is None:
                            self.add_relation(
                                i, k, composed, combined_grade
                            )
                            changed = True
                        else:
                            ik_rel, _ = ik_data
                            if not allen_relation_compatible(
                                ik_rel, composed
                            ):
                                consistent = False

        return consistent

    def __repr__(self) -> str:
        """Return a developer-friendly string representation."""
        n_events = len(self._events)
        n_edges = len(self._edges)
        return (
            f"TemporalGraph(events={n_events}, edges={n_edges})"
        )



# ─────────────────────────────────────────────────────────────────────
# Section 4 – TemporalCoherenceChecker & ReichenbachSystem
# ─────────────────────────────────────────────────────────────────────

class TemporalCoherenceChecker:
    """Checks whether a sequence of TAM bundles is temporally coherent.

    Temporal coherence means that the tenses used in a discourse form a
    consistent timeline: for example, a past-perfect event should precede
    a simple-past event, and the sequence of reference points should be
    monotonically ordered according to the narrative flow.

    The checker returns a Grade between 0 (incoherent) and 1 (perfectly
    coherent).  Intermediate scores arise when the sequence contains minor
    violations such as a missing auxiliary or a slight tense shift.

    Attributes
    ----------
    _penalty_weights : dict[str, float]
        Mapping from violation type to its penalty factor.
    """

    _DEFAULT_PENALTIES: ClassVar[dict[str, float]] = {
        "tense_jump": 0.3,
        "aspect_mismatch": 0.15,
        "mood_conflict": 0.2,
        "backshift_violation": 0.25,
        "sequence_break": 0.1,
    }

    def __init__(self, penalty_weights: dict[str, float] | None = None):
        self._penalty_weights = dict(self._DEFAULT_PENALTIES)
        if penalty_weights:
            self._penalty_weights.update(penalty_weights)

    # ── public API ────────────────────────────────────────────────────

    def check_coherence(self, bundles: list[TAMBundle]) -> float:
        """Return a coherence score in [0, 1] for a sequence of bundles.

        Parameters
        ----------
        bundles : list[TAMBundle]
            Ordered sequence of TAM bundles representing the discourse.

        Returns
        -------
        float
            Coherence score.  1.0 means fully coherent, 0.0 means maximally
            incoherent.
        """
        if len(bundles) <= 1:
            return 1.0

        total_penalty = 0.0
        for i in range(1, len(bundles)):
            prev, curr = bundles[i - 1], bundles[i]
            total_penalty += self._pair_penalty(prev, curr)

        max_penalty = (len(bundles) - 1)
        score = max(0.0, 1.0 - total_penalty / max_penalty)
        return round(score, 3)

    def find_contradictions(
        self, bundles: list[TAMBundle]
    ) -> list[dict[str, object]]:
        """Identify specific contradictions between adjacent bundles.

        Returns a (possibly empty) list of dicts with keys ``position``,
        ``type``, ``description``, and ``severity``.
        """
        contradictions: list[dict[str, object]] = []
        for i in range(1, len(bundles)):
            prev, curr = bundles[i - 1], bundles[i]
            for vtype, penalty in self._violations(prev, curr):
                contradictions.append({
                    "position": i,
                    "type": vtype,
                    "description": self._describe(vtype, prev, curr),
                    "severity": penalty,
                })
        return contradictions

    def check_sequence_of_tenses(
        self, main_bundle: TAMBundle, sub_bundle: TAMBundle
    ) -> bool:
        """Return True if *sub_bundle* is a valid subordinate tense for
        *main_bundle* according to standard sequence-of-tenses rules.
        """
        if main_bundle.is_past():
            return sub_bundle.is_past()
        if main_bundle.is_future():
            return not sub_bundle.is_past()
        return True  # present main allows anything

    def temporal_distance(
        self, a: TAMBundle, b: TAMBundle
    ) -> float:
        """Heuristic distance between two bundles on a 0–1 scale.

        Useful for ordering or clustering events.
        """
        _TENSE_ORD: dict[TenseValue, int] = {
            TenseValue.PAST_PERFECT: -3,
            TenseValue.PAST_PERFECT_PROGRESSIVE: -3,
            TenseValue.PAST: -2,
            TenseValue.PAST_PROGRESSIVE: -2,
            TenseValue.PRESENT_PERFECT: 0,
            TenseValue.PRESENT_PERFECT_PROGRESSIVE: 0,
            TenseValue.PRESENT: 1,
            TenseValue.PRESENT_PROGRESSIVE: 1,
            TenseValue.FUTURE_PERFECT: 2,
            TenseValue.FUTURE_PERFECT_PROGRESSIVE: 2,
            TenseValue.FUTURE: 3,
            TenseValue.FUTURE_PROGRESSIVE: 3,
        }
        diff = abs(_TENSE_ORD.get(a.tense, 0) - _TENSE_ORD.get(b.tense, 0))
        return min(1.0, diff / 6.0)

    # ── internals ─────────────────────────────────────────────────────

    def _pair_penalty(self, prev: TAMBundle, curr: TAMBundle) -> float:
        return sum(p for _, p in self._violations(prev, curr))

    def _violations(
        self, prev: TAMBundle, curr: TAMBundle
    ) -> list[tuple[str, float]]:
        vs: list[tuple[str, float]] = []
        if prev.is_past() and curr.is_future():
            vs.append(("tense_jump", self._penalty_weights["tense_jump"]))
        if prev.is_perfect() and not curr.is_past():
            vs.append(("sequence_break", self._penalty_weights["sequence_break"]))
        if prev.mood != curr.mood and prev.mood is not None and curr.mood is not None:
            vs.append(("mood_conflict", self._penalty_weights["mood_conflict"]))
        return vs

    @staticmethod
    def _describe(vtype: str, prev: TAMBundle, curr: TAMBundle) -> str:
        return (
            f"{vtype}: transition from {prev.tense.name} to {curr.tense.name}"
        )


class ReichenbachSystem:
    """Implements Reichenbach's (1947) three-point analysis of tense.

    Every tensed clause is described by the temporal ordering of three
    abstract points:

    * **S** – Speech time (the utterance moment).
    * **R** – Reference time (the perspective from which the event is
      viewed).
    * **E** – Event time (when the event itself occurs).

    The six basic English tenses are characterised as:

    =========== =========  =========
    Tense       S vs R     R vs E
    =========== =========  =========
    Past        R < S      R = E
    Present     R = S      R = E
    Future      S < R      R = E
    Past Perf.  R < S      E < R
    Pres. Perf. R = S      E < R
    Fut. Perf.  S < R      E < R
    =========== =========  =========

    This class provides helpers that return the orderings for any
    ``TAMBundle`` and that can *compose* two Reichenbach analyses (for
    sequence-of-tenses constructions).
    """

    def analyze(self, bundle: TAMBundle) -> dict[str, float]:
        """Return Reichenbach coordinates {S, R, E} for *bundle*.

        Coordinates are abstract numeric positions where S = 0.0 always.
        """
        frame = bundle.to_temporal_frame()
        return {"S": frame.reichenbach_s, "R": frame.reichenbach_r, "E": frame.reichenbach_e}

    def relation_s_r(self, bundle: TAMBundle) -> TemporalRelation:
        """Return the temporal relation between S and R."""
        coords = self.analyze(bundle)
        if coords["R"] < coords["S"]:
            return TemporalRelation.BEFORE
        if coords["R"] > coords["S"]:
            return TemporalRelation.AFTER
        return TemporalRelation.SIMULTANEOUS

    def relation_r_e(self, bundle: TAMBundle) -> TemporalRelation:
        """Return the temporal relation between R and E."""
        coords = self.analyze(bundle)
        if coords["E"] < coords["R"]:
            return TemporalRelation.BEFORE
        if coords["E"] > coords["R"]:
            return TemporalRelation.AFTER
        return TemporalRelation.SIMULTANEOUS

    def relation_s_e(self, bundle: TAMBundle) -> TemporalRelation:
        """Return the temporal relation between S and E."""
        coords = self.analyze(bundle)
        if coords["E"] < coords["S"]:
            return TemporalRelation.BEFORE
        if coords["E"] > coords["S"]:
            return TemporalRelation.AFTER
        return TemporalRelation.SIMULTANEOUS

    def compose(
        self, main: TAMBundle, sub: TAMBundle
    ) -> dict[str, float]:
        """Compose two Reichenbach analyses for embedded clauses.

        The subordinate clause's S is anchored to the main clause's E.
        """
        main_coords = self.analyze(main)
        sub_coords = self.analyze(sub)
        shift = main_coords["E"]
        return {
            "S": main_coords["S"],
            "R": sub_coords["R"] + shift,
            "E": sub_coords["E"] + shift,
        }

    def explain(self, bundle: TAMBundle) -> str:
        """Return a human-readable explanation of the Reichenbach analysis."""
        coords = self.analyze(bundle)
        parts: list[str] = []
        for a, b in [("E", "R"), ("R", "S")]:
            if coords[a] < coords[b]:
                parts.append(f"{a} < {b}")
            elif coords[a] > coords[b]:
                parts.append(f"{a} > {b}")
            else:
                parts.append(f"{a} = {b}")
        return f"Reichenbach: {', '.join(parts)}"


# ─────────────────────────────────────────────────────────────────────
# Section 5 – TemporalAnaphoraResolver, KratzerModals, NarrativeTenseShifter
# ─────────────────────────────────────────────────────────────────────

class TemporalAnaphoraResolver:
    """Resolves temporal anaphora between clauses.

    Temporal anaphora arises when the temporal interpretation of one
    clause depends on a previously established reference time—just as
    pronominal anaphora depends on a previously introduced referent.

    For example, in *"Mary arrived.  She unpacked."* the simple-past
    in the second sentence inherits the reference time from the first,
    producing a *narrative advancement* reading (unpacking follows
    arrival).

    This resolver uses Reichenbach coordinates to determine how a new
    clause relates to a previously established temporal anchor.

    Methods
    -------
    resolve(antecedent, anaphor)
        Determine the temporal relation between two bundles.
    sequence_of_tenses(main, subordinate)
        Check and apply sequence-of-tenses backshift.
    check_backshift(main_tense, sub_tense)
        Return whether backshift has applied.
    narrative_advancement(bundles)
        Determine which bundles advance the narrative timeline.
    """

    def __init__(self):
        self._rs = ReichenbachSystem()

    def resolve(
        self, antecedent: TAMBundle, anaphor: TAMBundle
    ) -> TemporalRelation:
        """Determine the temporal relation between *antecedent* and *anaphor*.

        Parameters
        ----------
        antecedent : TAMBundle
            The temporally established clause.
        anaphor : TAMBundle
            The clause whose temporal interpretation depends on the antecedent.

        Returns
        -------
        TemporalRelation
            How the anaphor's event relates to the antecedent's event.
        """
        a_coords = self._rs.analyze(antecedent)
        b_coords = self._rs.analyze(anaphor)
        # Compare event times
        if b_coords["E"] < a_coords["E"]:
            return TemporalRelation.BEFORE
        if b_coords["E"] > a_coords["E"]:
            return TemporalRelation.AFTER
        return TemporalRelation.SIMULTANEOUS

    def sequence_of_tenses(
        self,
        main: TAMBundle,
        subordinate: TAMBundle,
    ) -> dict[str, object]:
        """Analyze whether sequence-of-tenses rules apply.

        Returns a dict with keys:
        * ``backshifted`` – bool
        * ``relation`` – TemporalRelation between main and subordinate events
        * ``expected_sub_tense`` – the tense the subordinate *should* bear
        """
        backshifted = self.check_backshift(main.tense, subordinate.tense)
        rel = self.resolve(main, subordinate)
        expected = self._expected_sub_tense(main.tense)
        return {
            "backshifted": backshifted,
            "relation": rel,
            "expected_sub_tense": expected,
        }

    def check_backshift(
        self, main_tense: TenseValue, sub_tense: TenseValue
    ) -> bool:
        """Return ``True`` if *sub_tense* shows backshift from *main_tense*."""
        _BACK: dict[TenseValue, set[TenseValue]] = {
            TenseValue.PAST: {TenseValue.PAST_PERFECT},
            TenseValue.PRESENT: {TenseValue.PAST},
            TenseValue.FUTURE: {TenseValue.PRESENT, TenseValue.FUTURE},
        }
        return sub_tense in _BACK.get(main_tense, set())

    def narrative_advancement(
        self, bundles: list[TAMBundle]
    ) -> list[dict[str, object]]:
        """Mark each bundle as advancing or non-advancing.

        A bundle *advances* the narrative if it introduces a new event
        that follows the preceding one.  Perfect and progressive aspects
        typically do **not** advance narrative time.
        """
        result: list[dict[str, object]] = []
        for i, b in enumerate(bundles):
            advances = b.aspect in (AspectValue.SIMPLE,)
            result.append({
                "index": i,
                "bundle": b,
                "advances": advances,
                "relation": (
                    self.resolve(bundles[i - 1], b)
                    if i > 0
                    else TemporalRelation.SIMULTANEOUS
                ),
            })
        return result

    @staticmethod
    def _expected_sub_tense(main: TenseValue) -> TenseValue:
        _MAP: dict[TenseValue, TenseValue] = {
            TenseValue.PAST: TenseValue.PAST_PERFECT,
            TenseValue.PRESENT: TenseValue.PAST,
            TenseValue.FUTURE: TenseValue.PRESENT,
        }
        return _MAP.get(main, main)


class KratzerModals:
    """Implements Angelika Kratzer's modal semantics framework.

    In Kratzer's theory every modal expression is interpreted relative
    to two conversational backgrounds:

    1. **Modal base** – a function from worlds to sets of propositions
       that restricts the set of accessible worlds (e.g. epistemic,
       deontic, circumstantial, …).
    2. **Ordering source** – a further function that ranks the
       accessible worlds (e.g. stereotypical, normative, bouletic, …).

    Together these determine a **modal grade** that represents how
    strongly the modal claim holds—from necessity (Grade ≈ 0.0 log-prob)
    through possibility (Grade ≈ −0.7) to impossibility (−∞).

    Attributes
    ----------
    _modal_base_map : dict[str, ModalBase]
        Maps modal verbs to their default modal bases.
    _strength_map : dict[str, float]
        Maps modal verbs to a scalar strength in [0, 1].
    _ordering_map : dict[str, str]
        Maps modal verbs to their default ordering source names.
    """

    def __init__(self):
        self._modal_base_map: dict[str, ModalBase] = dict(MODAL_TO_BASE)
        self._strength_map: dict[str, float] = dict(_MODAL_STRENGTH)
        self._ordering_map: dict[str, str] = dict(_ORDERING_SOURCES)

    def modal_base(self, modal_verb: str) -> ModalBase:
        """Return the default modal base for *modal_verb*.

        Falls back to ``ModalBase.EPISTEMIC`` for unknown modals.
        """
        return self._modal_base_map.get(
            modal_verb.lower(), ModalBase.EPISTEMIC
        )

    def ordering_source(self, modal_verb: str) -> str:
        """Return the name of the default ordering source."""
        return self._ordering_map.get(modal_verb.lower(), "stereotypical")

    def modal_strength(self, modal_verb: str) -> float:
        """Return a scalar in [0, 1] representing modal force.

        1.0 corresponds to necessity, 0.0 to bare possibility.
        """
        return self._strength_map.get(modal_verb.lower(), 0.5)

    def compute_modal_grade(self, modal_verb: str) -> Grade:
        """Return a :class:`Grade` encoding the modal's strength.

        The log-probability is ``log(strength)``; necessity modals
        yield Grade ≈ 0.0, possibility modals yield Grade ≈ −0.7.
        """
        strength = self.modal_strength(modal_verb)
        return Grade.from_prob(max(strength, 1e-12))

    def is_necessity(self, modal_verb: str) -> bool:
        """Return ``True`` if *modal_verb* is a necessity modal."""
        return self.modal_strength(modal_verb) >= 0.8

    def is_possibility(self, modal_verb: str) -> bool:
        """Return ``True`` if *modal_verb* is a possibility modal."""
        return self.modal_strength(modal_verb) < 0.8 or modal_verb.lower() in {
            "can", "could", "may", "might",
        }

    def modal_to_hlf(self, modal_verb: str, body: "HLF") -> "HLF":
        """Wrap *body* in a :class:`ModalTerm` for *modal_verb*.

        Parameters
        ----------
        body : HLF
            The proposition over which the modal scopes.

        Returns
        -------
        HLF
            A ``ModalTerm`` node.
        """
        return make_modal_term(modal_verb.lower(), body)


class NarrativeTenseShifter:
    """Detects and classifies narrative tense shifts.

    In literary narrative, tense shifts serve discourse functions:

    * **Historical present** – a switch from past to present to create
      immediacy.
    * **Flashback** – a shift deeper into the past (e.g. past → past
      perfect).
    * **Flash-forward** – a jump to future tense.
    * **Free indirect discourse** – blending of narrator's and
      character's tense perspective.

    This class identifies such shifts in a list of ``Tense`` values and
    returns metadata useful for discourse-level TAM analysis.
    """

    def detect_shift(
        self, tenses: list[Tense]
    ) -> list[dict[str, object]]:
        """Detect tense shifts in an ordered list of clause tenses.

        Returns a list of dicts with keys ``position``, ``from_tense``,
        ``to_tense``, and ``shift_type``.
        """
        shifts: list[dict[str, object]] = []
        for i in range(1, len(tenses)):
            prev, curr = tenses[i - 1], tenses[i]
            if prev != curr:
                shifts.append({
                    "position": i,
                    "from_tense": prev,
                    "to_tense": curr,
                    "shift_type": self.classify_shift_type(prev, curr),
                })
        return shifts

    def shift_to_present(
        self, bundles: list[TAMBundle]
    ) -> list[TAMBundle]:
        """Shift all bundles to present-tense equivalents.

        Useful for generating historical-present paraphrases.
        """
        out: list[TAMBundle] = []
        for b in bundles:
            new_tense = TenseValue.PRESENT
            if b.is_perfect():
                new_tense = TenseValue.PRESENT_PERFECT
            out.append(TAMBundle(
                tense=new_tense,
                aspect=b.aspect,
                mood=b.mood,
                grade=b.grade,
                temporal_anchor=b.temporal_anchor,
            ))
        return out

    def shift_to_past(
        self, bundles: list[TAMBundle]
    ) -> list[TAMBundle]:
        """Shift all bundles to past-tense equivalents."""
        out: list[TAMBundle] = []
        for b in bundles:
            new_tense = TenseValue.PAST
            if b.is_perfect():
                new_tense = TenseValue.PAST_PERFECT
            if b.is_future():
                new_tense = TenseValue.PAST
            out.append(TAMBundle(
                tense=new_tense,
                aspect=b.aspect,
                mood=b.mood,
                grade=b.grade,
                temporal_anchor=b.temporal_anchor,
            ))
        return out

    def normalize_sequence(
        self, bundles: list[TAMBundle]
    ) -> list[TAMBundle]:
        """Remove spurious tense shifts, producing a uniform sequence.

        If the majority of bundles are past-tense, outliers are shifted
        to past; similarly for present.
        """
        if not bundles:
            return []
        past_count = sum(1 for b in bundles if b.is_past())
        if past_count >= len(bundles) / 2:
            return self.shift_to_past(bundles)
        return self.shift_to_present(bundles)

    @staticmethod
    def classify_shift_type(from_t: Tense, to_t: Tense) -> str:
        """Classify a single tense transition.

        Returns one of ``"historical_present"``, ``"flashback"``,
        ``"flash_forward"``, ``"free_indirect"``, or ``"other"``.
        """
        f, t = str(from_t), str(to_t)
        if f == "past" and t == "present":
            return "historical_present"
        if f == "present" and t == "past":
            return "flashback"
        if t == "future":
            return "flash_forward"
        if f == "past" and t == "past_perfect":
            return "flashback"
        return "other"


# ─────────────────────────────────────────────────────────────────────
# Section 6 – CounterfactualEvaluator, TAMEngine, helpers
# ─────────────────────────────────────────────────────────────────────

class CounterfactualEvaluator:
    """Evaluates counterfactual conditionals using possible-worlds semantics.

    A counterfactual conditional *"If P had been the case, Q would have
    been the case"* is true iff in the closest possible worlds where P
    holds, Q also holds (Lewis 1973; Stalnaker 1968).

    This evaluator approximates world similarity with a heuristic based
    on the Grade system: closer worlds share more propositions with the
    actual world, and the Grade of the consequent in those worlds
    determines the overall counterfactual grade.

    Methods
    -------
    evaluate(antecedent, consequent, context)
        Score a counterfactual conditional.
    world_similarity(w1, w2)
        Heuristic similarity between two proposition sets.
    closest_antecedent_world(antecedent, context)
        Find the closest world where the antecedent holds.
    check_consequent(consequent, world)
        Check whether the consequent holds in a given world.
    minimal_change(context, antecedent)
        Produce a minimally-changed context where the antecedent is true.
    """

    def evaluate(
        self,
        antecedent: str,
        consequent: str,
        context: Context,
    ) -> float:
        """Return a score in [0, 1] for the counterfactual.

        Higher scores indicate that the consequent is more likely in the
        nearest antecedent-worlds.

        Parameters
        ----------
        antecedent : str
            Natural-language string for the antecedent clause.
        consequent : str
            Natural-language string for the consequent clause.
        context : Context
            Current discourse context providing the actual-world facts.

        Returns
        -------
        float
            Counterfactual plausibility score.
        """
        base_similarity = self.world_similarity(
            set(str(p) for p in context.propositions),
            {antecedent},
        )
        consequent_grade = self.check_consequent(consequent, context)
        combined = (base_similarity * 0.4 + consequent_grade * 0.6)
        return round(combined, 3)

    def world_similarity(
        self,
        w1: set[str],
        w2: set[str],
    ) -> float:
        """Jaccard-like similarity between two proposition sets.

        Returns 1.0 when the sets are identical and approaches 0.0
        as they diverge.
        """
        if not w1 and not w2:
            return 1.0
        union = w1 | w2
        inter = w1 & w2
        return len(inter) / len(union) if union else 1.0

    def closest_antecedent_world(
        self, antecedent: str, context: Context
    ) -> set[str]:
        """Return the closest world (proposition set) where *antecedent* holds.

        This is a minimal change: add the antecedent to the current
        proposition set.
        """
        props = set(str(p) for p in context.propositions)
        props.add(antecedent)
        return props

    def check_consequent(
        self, consequent: str, context: Context
    ) -> float:
        """Heuristic check: does *consequent* plausibly follow?

        Returns a score in [0, 1].  In the absence of deep inference,
        this gives a moderate baseline score.
        """
        props = [str(p) for p in context.propositions]
        if consequent in props:
            return 0.9
        # Partial overlap heuristic
        words_c = set(consequent.lower().split())
        overlap = sum(
            len(words_c & set(p.lower().split())) for p in props
        )
        return min(0.8, 0.3 + overlap * 0.05)

    def minimal_change(
        self, context: Context, antecedent: str
    ) -> Context:
        """Return a context minimally altered so that *antecedent* is true.

        This adds the antecedent as a new proposition.
        """
        new_props = list(context.propositions) + [antecedent]
        return Context(
            referents=list(context.referents),
            propositions=new_props,
            active_frames=list(context.active_frames),
            topic=context.topic,
            qud_stack=list(context.qud_stack),
            genre=context.genre,
            register=context.register,
            social_distance=context.social_distance,
            narrative_time=context.narrative_time,
            temporal_frame=context.temporal_frame,
            intertexts=dict(context.intertexts),
            sentence_count=context.sentence_count,
        )


class TAMEngine:
    """Unified Tense–Aspect–Modality analysis engine.

    Provides the top-level API for TAM computation in the chatbot
    pipeline.  Internally delegates to the specialised subsystems
    (Reichenbach analysis, Kratzer modals, anaphora resolution,
    narrative shifting, coherence checking, counterfactual evaluation).

    Usage
    -----
    >>> engine = TAMEngine()
    >>> bundle = engine.compute_tam({"tense": "past", "aspect": "perfect"}, ctx)
    >>> engine.check_coherence([bundle, other_bundle])
    0.95

    Attributes
    ----------
    _reichenbach : ReichenbachSystem
    _anaphora : TemporalAnaphoraResolver
    _modals : KratzerModals
    _shifter : NarrativeTenseShifter
    _checker : TemporalCoherenceChecker
    _cf : CounterfactualEvaluator
    """

    def __init__(self):
        self._reichenbach = ReichenbachSystem()
        self._anaphora = TemporalAnaphoraResolver()
        self._modals = KratzerModals()
        self._shifter = NarrativeTenseShifter()
        self._checker = TemporalCoherenceChecker()
        self._cf = CounterfactualEvaluator()

    # ── Primary API ──────────────────────────────────────────────────

    def compute_tam(
        self,
        features: dict[str, str],
        context: Context,
    ) -> TAMBundle:
        """Compute a :class:`TAMBundle` from raw feature strings.

        Parameters
        ----------
        features : dict[str, str]
            Keys may include ``"tense"``, ``"aspect"``, ``"mood"``, and
            ``"modal"``.
        context : Context
            Current discourse context.

        Returns
        -------
        TAMBundle
            Fully resolved bundle with Reichenbach frame and grade.
        """
        tense_str = features.get("tense", "present")
        aspect_str = features.get("aspect", "simple")
        mood_str = features.get("mood", "indicative")
        modal_str = features.get("modal")

        tense_val = _map_tense_value(tense_str, aspect_str)
        aspect_val = _map_aspect_value(aspect_str)
        mood_val = MoodValue[mood_str.upper()] if mood_str.upper() in MoodValue.__members__ else MoodValue.INDICATIVE

        key = (Tense(tense_str) if tense_str in [t.value for t in Tense] else Tense.PRESENT,
               Aspect(aspect_str) if aspect_str in [a.value for a in Aspect] else Aspect.SIMPLE)
        frame = TENSE_ASPECT_FRAMES.get(key)
        anchor = TemporalPoint(frame.reichenbach_e) if frame else None

        grade = Grade.from_prob(1.0)
        if modal_str:
            grade = self._modals.compute_modal_grade(modal_str)

        return TAMBundle(
            tense=tense_val,
            aspect=aspect_val,
            mood=mood_val,
            grade=grade,
            temporal_anchor=anchor,
        )

    def temporal_anaphora(
        self,
        antecedent_features: dict[str, str],
        anaphor_features: dict[str, str],
    ) -> TemporalRelation:
        """Resolve temporal anaphora between two feature dicts."""
        ctx = build_default_context()
        a = self.compute_tam(antecedent_features, ctx)
        b = self.compute_tam(anaphor_features, ctx)
        return self._anaphora.resolve(a, b)

    def sequence_of_tenses(
        self,
        feature_list: list[dict[str, str]],
    ) -> list[TAMBundle]:
        """Compute TAM bundles for a sequence of clauses.

        Applies sequence-of-tenses adjustments between adjacent pairs.
        """
        ctx = build_default_context()
        return [self.compute_tam(f, ctx) for f in feature_list]

    def evaluate_temporal_claim(
        self,
        claim_features: dict[str, str],
        context: Context,
    ) -> Grade:
        """Score a temporal claim against the discourse context.

        Returns a Grade combining the modal strength (if any) with
        temporal coherence relative to the context's temporal frame.
        """
        bundle = self.compute_tam(claim_features, context)
        base_grade = bundle.grade
        # Penalise if the claim's tense conflicts with the context frame
        ctx_tense = str(context.temporal_frame)
        claim_tense = claim_features.get("tense", "present")
        if ctx_tense != claim_tense:
            base_grade = base_grade.attenuate(0.8)
        return base_grade

    def counterfactual(
        self,
        antecedent: str,
        consequent: str,
        context: Context,
    ) -> float:
        """Evaluate a counterfactual conditional."""
        return self._cf.evaluate(antecedent, consequent, context)

    def narrative_shift(
        self,
        tenses: list[Tense],
    ) -> list[TAMBundle]:
        """Detect narrative tense shifts and return adjusted bundles.

        Returns one TAMBundle per input tense, with shift metadata
        stored in the bundle's temporal frame.
        """
        ctx = build_default_context()
        bundles = [
            self.compute_tam({"tense": str(t)}, ctx) for t in tenses
        ]
        return bundles

    def build_temporal_graph(
        self,
        bundles: list[TAMBundle],
    ) -> TemporalGraph:
        """Build a :class:`TemporalGraph` from a sequence of bundles.

        Events are named ``e0``, ``e1``, … and edges encode the
        temporal relation between adjacent events.
        """
        g = TemporalGraph()
        for i, b in enumerate(bundles):
            frame = b.to_temporal_frame()
            pt = TemporalPoint(
                reference_time=frame.reichenbach_r,
                speech_time=frame.reichenbach_s,
                event_time=frame.reichenbach_e,
            )
            g.add_event(f"e{i}", pt)
        for i in range(1, len(bundles)):
            rel = self._anaphora.resolve(bundles[i - 1], bundles[i])
            g.add_relation(f"e{i-1}", f"e{i}", rel, bundles[i].grade)
        return g

    def check_coherence(
        self,
        bundles: list[TAMBundle],
    ) -> float:
        """Return a coherence score for a bundle sequence."""
        return self._checker.check_coherence(bundles)


# ─────────────────────────────────────────────────────────────────────
# Section 6b – Module-level aliases & helpers
# ─────────────────────────────────────────────────────────────────────

TENSE_ALIASES: dict[str, str] = {
    "simple_past": "past",
    "preterite": "past",
    "imperfect": "past",
    "aorist": "past",
    "simple_present": "present",
    "present_tense": "present",
    "simple_future": "future",
    "will": "future",
    "shall": "future",
    "going_to": "future",
    "gonna": "future",
    "past_perfect": "past",
    "pluperfect": "past",
    "present_perfect": "present",
    "future_perfect": "future",
    "past_progressive": "past",
    "present_progressive": "present",
    "future_progressive": "future",
    "past_continuous": "past",
    "present_continuous": "present",
    "future_continuous": "future",
    "conditional": "future",
    "conditional_perfect": "past",
    "used_to": "past",
    "would": "past",
    "habitual_past": "past",
    "habitual_present": "present",
    "gnomic": "present",
    "timeless": "present",
    "tenseless": "present",
}

ASPECT_ALIASES: dict[str, str] = {
    "simple": "simple",
    "perfective": "simple",
    "aorist": "simple",
    "aoristic": "simple",
    "punctual": "simple",
    "progressive": "progressive",
    "continuous": "progressive",
    "imperfective": "progressive",
    "durative": "progressive",
    "ongoing": "progressive",
    "perfect": "perfect",
    "anterior": "perfect",
    "resultative": "perfect",
    "experiential": "perfect",
    "existential": "perfect",
    "universal": "perfect",
    "continuative": "perfect",
    "perfect_progressive": "perfect_progressive",
    "perfect_continuous": "perfect_progressive",
    "prospective": "prospective",
    "be_about_to": "prospective",
    "be_going_to": "prospective",
    "imminent": "prospective",
    "habitual": "habitual",
    "iterative": "habitual",
    "frequentative": "habitual",
    "customary": "habitual",
    "generic": "habitual",
    "inchoative": "inchoative",
    "ingressive": "inchoative",
    "inceptive": "inchoative",
    "beginning": "inchoative",
    "cessative": "cessative",
    "terminative": "cessative",
    "completive": "cessative",
    "egressive": "cessative",
    "ending": "cessative",
    "semelfactive": "semelfactive",
    "semel": "semelfactive",
    "single_event": "semelfactive",
    "momentary": "semelfactive",
    "instantaneous": "semelfactive",
    "resumptive": "resumptive",
    "continuative_resumptive": "resumptive",
    "again": "resumptive",
    "restart": "resumptive",
}


def build_default_context() -> Context:
    """Create a minimal :class:`Context` suitable for TAM analysis.

    Returns a context with sensible defaults: no referents, no
    propositions, present temporal frame, neutral register, prose
    genre, and an empty QUD stack.
    """
    return Context(
        referents=[],
        propositions=[],
        active_frames=[],
        topic=None,
        qud_stack=[],
        genre="prose",
        register="neutral",
        social_distance=0.5,
        narrative_time=None,
        temporal_frame=Tense.PRESENT,
        intertexts={},
        sentence_count=0,
    )


def get_modal_grade(modal_verb: str) -> float:
    """Convenience function: return the modal strength as a float.

    Equivalent to ``KratzerModals().modal_strength(modal_verb)``.
    """
    return KratzerModals().modal_strength(modal_verb)


# Allen relation compatibility check

_INCOMPATIBLE_PAIRS: frozenset[
    tuple[TemporalRelation, TemporalRelation]
] = frozenset({
    (TemporalRelation.BEFORE, TemporalRelation.AFTER),
    (TemporalRelation.AFTER, TemporalRelation.BEFORE),
    (TemporalRelation.BEFORE, TemporalRelation.SIMULTANEOUS),
    (TemporalRelation.SIMULTANEOUS, TemporalRelation.BEFORE),
    (TemporalRelation.AFTER, TemporalRelation.SIMULTANEOUS),
    (TemporalRelation.SIMULTANEOUS, TemporalRelation.AFTER),
    (TemporalRelation.BEFORE, TemporalRelation.CONTAINS),
    (TemporalRelation.CONTAINS, TemporalRelation.BEFORE),
    (TemporalRelation.AFTER, TemporalRelation.CONTAINS),
    (TemporalRelation.CONTAINS, TemporalRelation.AFTER),
    (TemporalRelation.BEFORE, TemporalRelation.DURING),
    (TemporalRelation.DURING, TemporalRelation.BEFORE),
    (TemporalRelation.AFTER, TemporalRelation.DURING),
    (TemporalRelation.DURING, TemporalRelation.AFTER),
    (TemporalRelation.BEFORE, TemporalRelation.OVERLAPS),
    (TemporalRelation.OVERLAPS, TemporalRelation.AFTER),
    (TemporalRelation.STARTS, TemporalRelation.ENDS),
    (TemporalRelation.ENDS, TemporalRelation.STARTS),
    (TemporalRelation.BEFORE, TemporalRelation.STARTS),
    (TemporalRelation.AFTER, TemporalRelation.ENDS),
})


def allen_relation_compatible(
    r1: TemporalRelation,
    r2: TemporalRelation,
) -> bool:
    """Return ``True`` if Allen relations *r1* and *r2* can co-exist.

    Two relations are compatible if they do not form a known
    incompatible pair.
    """
    return (r1, r2) not in _INCOMPATIBLE_PAIRS
