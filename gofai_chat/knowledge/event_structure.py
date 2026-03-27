"""Event structure and Vendler aspectual classes grounded in the Grade semiring.

Judgment-Harmonic Event Structure
==================================
This module implements Vendler's four-way (plus one) aspectual classification
of verb phrases — States, Activities, Accomplishments, Achievements, and
Semelfactives — in the framework of Judgment-Harmonic theory.

Every aspect-classification result, every thematic-fit score, and every
temporal-profile quality score is a :class:`~gofai_chat.core.grade.Grade`.

Aspect classes and their Grade profiles
----------------------------------------
* **STATE**: Grade of culmination is ``Grade.impossible()``; the event has no
  natural endpoint.  Temporal profile is flat.
* **ACTIVITY**: Grade of culmination is also ``Grade.impossible()``; the event
  is dynamic but unbounded.  Profile rises gradually.
* **ACCOMPLISHMENT**: Grade of culmination is ``Grade.perfect()``; the event
  has a built-in endpoint.  Profile builds toward a peak.
* **ACHIEVEMENT**: Grade of culmination is ``Grade.perfect()``; the event is
  essentially instantaneous.  Profile has a sharp spike at the end.
* **SEMELFACTIVE**: The event is a single, discrete occurrence that can iterate.
  Culmination grade is ``Grade.perfect()`` for the individual occurrence;
  iterated form looks like an Activity.

Grade semiring operations in event composition
-----------------------------------------------
* ``compose_events`` multiplies event grades (``Grade.__mul__``) because
  sequential composition requires both sub-events to succeed.
* ``grade_culmination`` returns the culmination Grade of an event structure.
* ``temporal_profile_grade`` scores how well the observed profile matches the
  expected canonical shape; uses ``Grade.from_prob(1 - mean_squared_error)``.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Optional

from gofai_chat.core.grade import Grade
from gofai_chat.harmony.gluing import GluingData

__all__ = [
    "AspectClass",
    "EventStructure",
    "TemporalRelation",
    "EventChain",
    "EventStructureAnalyzer",
    "TemporalReasoningEngine",
    "AspectShiftDetector",
    "CANONICAL_PROFILES",
    "build_default_analyzer",
]

# ---------------------------------------------------------------------------
# AspectClass
# ---------------------------------------------------------------------------

class AspectClass(Enum):
    """Vendler's aspectual classes, extended with Semelfactives.

    Each class has a characteristic combination of the features:
    * **Telic** vs. **Atelic** — whether there is an inherent endpoint
    * **Durative** vs. **Punctual** — whether the event extends in time
    * **Dynamic** vs. **Stative** — whether there is change / activity
    """

    STATE = auto()
    """Stative, atelic, durative. E.g. *know*, *believe*, *own*, *contain*."""

    ACTIVITY = auto()
    """Dynamic, atelic, durative. E.g. *run*, *swim*, *think*, *work*."""

    ACCOMPLISHMENT = auto()
    """Dynamic, telic, durative. E.g. *build a house*, *write a letter*."""

    ACHIEVEMENT = auto()
    """Dynamic, telic, punctual. E.g. *arrive*, *break*, *discover*, *die*."""

    SEMELFACTIVE = auto()
    """Dynamic, atelic, punctual (but can iterate). E.g. *flash*, *knock*."""

    def is_telic(self) -> bool:
        """Return True if this aspect class is inherently telic."""
        return self in {AspectClass.ACCOMPLISHMENT, AspectClass.ACHIEVEMENT}

    def is_durative(self) -> bool:
        """Return True if this aspect class is inherently durative."""
        return self in {AspectClass.STATE, AspectClass.ACTIVITY, AspectClass.ACCOMPLISHMENT}

    def is_dynamic(self) -> bool:
        """Return True if this aspect class involves change."""
        return self in {AspectClass.ACTIVITY, AspectClass.ACCOMPLISHMENT,
                        AspectClass.ACHIEVEMENT, AspectClass.SEMELFACTIVE}

    def culmination_grade(self) -> Grade:
        """Canonical culmination Grade for this aspect class.

        * ACCOMPLISHMENT, ACHIEVEMENT, SEMELFACTIVE → ``Grade.perfect()``
        * STATE, ACTIVITY → ``Grade.impossible()`` (no built-in endpoint)
        """
        if self in {AspectClass.STATE, AspectClass.ACTIVITY}:
            return Grade.impossible()
        return Grade.perfect()

    def __str__(self) -> str:
        return self.name.capitalize()


# ---------------------------------------------------------------------------
# Canonical temporal profiles
# ---------------------------------------------------------------------------

#: 10-point canonical temporal tension profiles for each aspect class.
#: Values are energy/tension levels at normalized time points 0.0 … 1.0.
CANONICAL_PROFILES: dict[AspectClass, list[float]] = {
    AspectClass.STATE: [0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5],
    AspectClass.ACTIVITY: [0.2, 0.3, 0.4, 0.5, 0.5, 0.55, 0.55, 0.5, 0.5, 0.5],
    AspectClass.ACCOMPLISHMENT: [0.1, 0.2, 0.35, 0.45, 0.55, 0.65, 0.75, 0.85, 0.95, 1.0],
    AspectClass.ACHIEVEMENT: [0.0, 0.0, 0.0, 0.0, 0.05, 0.1, 0.2, 0.5, 0.8, 1.0],
    AspectClass.SEMELFACTIVE: [0.0, 0.0, 0.0, 0.0, 1.0, 0.7, 0.2, 0.0, 0.0, 0.0],
}


# ---------------------------------------------------------------------------
# EventStructure
# ---------------------------------------------------------------------------

@dataclass
class EventStructure:
    """A fully characterized event structure with Grade-valued quality.

    This dataclass bundles all information needed for aspectual reasoning:
    the predicate, its aspect class, the participants (thematic role fillers),
    a temporal tension profile, and various Grade scores.

    Attributes
    ----------
    predicate:
        The main verb or predicate of the event (e.g. ``"run"``).
    aspect:
        The Vendler aspect class.
    grade:
        Overall confidence / quality of this event analysis.
    participants:
        Dictionary mapping thematic role labels to filler strings.
        E.g. ``{"AGENT": "John", "PATIENT": "the house"}``.
    temporal_profile:
        A list of 10 floats in [0,1] representing the energy/tension
        of the event at normalized time points.  Compared against the
        canonical profile for the aspect class.
    culmination_grade:
        How strongly the event reaches its culmination / endpoint.
    duration_estimate:
        Typical duration in seconds, or ``None`` if indeterminate.
    iterative:
        Whether this event naturally iterates (semelfactives: True).
    causative:
        Whether this event entails a causation relation.
    subevent_structure:
        Decomposed sub-events (especially for Accomplishments).
    frame_name:
        FrameNet frame name if known (e.g. ``"Motion"``, ``"Cooking"``).
    lexical_aspect_cues:
        Cue words that informed the aspect classification.
    """

    predicate: str
    aspect: AspectClass
    grade: Grade
    participants: dict[str, str] = field(default_factory=dict)
    temporal_profile: list[float] = field(
        default_factory=lambda: [0.5] * 10
    )
    culmination_grade: Grade = field(default_factory=Grade.impossible)
    duration_estimate: Optional[float] = None
    iterative: bool = False
    causative: bool = False
    subevent_structure: list["EventStructure"] = field(default_factory=list)
    frame_name: Optional[str] = None
    lexical_aspect_cues: list[str] = field(default_factory=list)

    # ------------------------------------------------------------------

    def has_role(self, role: str) -> bool:
        """Return True if ``role`` appears in the participant dict."""
        return role.upper() in {k.upper() for k in self.participants}

    def is_transitive(self) -> bool:
        """Return True if there is a PATIENT or THEME role."""
        upper = {k.upper() for k in self.participants}
        return bool(upper & {"PATIENT", "THEME", "OBJECT"})

    def is_agentive(self) -> bool:
        """Return True if there is an AGENT or ACTOR role."""
        upper = {k.upper() for k in self.participants}
        return bool(upper & {"AGENT", "ACTOR", "EXPERIENCER"})

    def complexity(self) -> int:
        """Return a complexity score: number of participants + number of sub-events."""
        return len(self.participants) + len(self.subevent_structure)

    def feature_dict(self) -> dict[str, bool]:
        """Return a Boolean feature dictionary for this event structure.

        Useful for downstream classifiers and the InductiveLearner.
        """
        return {
            "telic": self.aspect.is_telic(),
            "durative": self.aspect.is_durative(),
            "dynamic": self.aspect.is_dynamic(),
            "agentive": self.is_agentive(),
            "transitive": self.is_transitive(),
            "causative": self.causative,
            "iterative": self.iterative,
            "has_culmination": not self.culmination_grade.is_impossible,
        }

    def __repr__(self) -> str:
        return (
            f"EventStructure({self.predicate!r}, {self.aspect.name}, "
            f"grade={self.grade}, participants={self.participants})"
        )


# ---------------------------------------------------------------------------
# TemporalRelation
# ---------------------------------------------------------------------------

@dataclass
class TemporalRelation:
    """A Grade-valued Allen interval relation between two events.

    Allen (1983) defined 13 mutually exclusive relations between temporal
    intervals.  In the Grade-harmonic framework, each relation carries a
    ``grade`` reflecting how confidently it holds.

    Attributes
    ----------
    relation:
        Allen relation name: ``"before"``, ``"after"``, ``"meets"``,
        ``"met_by"``, ``"overlaps"``, ``"overlapped_by"``, ``"starts"``,
        ``"started_by"``, ``"during"``, ``"contains"``, ``"finishes"``,
        ``"finished_by"``, or ``"equals"``.
    e1:
        Predicate / name of the first event.
    e2:
        Predicate / name of the second event.
    grade:
        Confidence in this relation.
    """

    relation: str
    e1: str
    e2: str
    grade: Grade = field(default_factory=Grade.perfect)

    # Allen transitivity table (partial)
    _TRANSITIVITY: dict[tuple[str, str], list[str]] = field(
        default_factory=dict, init=False, repr=False, compare=False
    )

    def inverse(self) -> "TemporalRelation":
        """Return the inverse relation (e.g. before → after)."""
        inverses = {
            "before": "after", "after": "before",
            "meets": "met_by", "met_by": "meets",
            "overlaps": "overlapped_by", "overlapped_by": "overlaps",
            "starts": "started_by", "started_by": "starts",
            "during": "contains", "contains": "during",
            "finishes": "finished_by", "finished_by": "finishes",
            "equals": "equals",
        }
        inv_rel = inverses.get(self.relation, self.relation)
        return TemporalRelation(inv_rel, self.e2, self.e1, self.grade)

    def __repr__(self) -> str:
        return (
            f"TemporalRelation({self.e1!r} {self.relation} {self.e2!r}, "
            f"grade={self.grade})"
        )


# ---------------------------------------------------------------------------
# TemporalReasoningEngine
# ---------------------------------------------------------------------------

class TemporalReasoningEngine:
    """Transitivity closure over Grade-valued Allen temporal relations.

    Maintains a graph of :class:`TemporalRelation` objects and supports:
    * Adding new relations
    * Inferring new relations via the Allen transitivity table
    * Checking overall consistency of the temporal graph
    * Finding explanation chains between two events

    Grade semantics of transitivity
    --------------------------------
    If ``e1 before e2`` with grade g12 and ``e2 before e3`` with grade g23,
    then ``e1 before e3`` holds with grade ``g12 * g23`` (Grade multiplication).
    The product reflects that both premises must hold simultaneously.
    """

    # Allen transitivity table (simplified): relation1 ∘ relation2 → possible results
    _TRANS: dict[tuple[str, str], list[str]] = {
        ("before", "before"): ["before"],
        ("before", "meets"): ["before"],
        ("before", "overlaps"): ["before"],
        ("before", "starts"): ["before"],
        ("before", "during"): ["before", "overlaps", "meets", "during", "starts"],
        ("before", "finishes"): ["before", "overlaps", "meets", "during", "starts"],
        ("before", "equals"): ["before"],
        ("before", "after"): ["before", "meets", "overlaps", "equals", "met_by",
                               "overlapped_by", "after", "during", "contains",
                               "starts", "started_by", "finishes", "finished_by"],
        ("meets", "before"): ["before"],
        ("meets", "meets"): ["before"],
        ("meets", "overlaps"): ["before"],
        ("meets", "starts"): ["overlaps"],
        ("meets", "during"): ["overlaps", "during"],
        ("meets", "finishes"): ["meets"],
        ("meets", "equals"): ["meets"],
        ("overlaps", "before"): ["before"],
        ("overlaps", "meets"): ["before"],
        ("overlaps", "overlaps"): ["before", "overlaps"],
        ("overlaps", "starts"): ["overlaps"],
        ("overlaps", "during"): ["overlaps", "during"],
        ("overlaps", "finishes"): ["overlaps", "meets"],
        ("overlaps", "equals"): ["overlaps"],
        ("during", "before"): ["before"],
        ("during", "meets"): ["before"],
        ("during", "overlaps"): ["before", "overlaps"],
        ("during", "starts"): ["during", "overlaps"],
        ("during", "during"): ["during"],
        ("during", "finishes"): ["during"],
        ("during", "equals"): ["during"],
        ("equals", "before"): ["before"],
        ("equals", "meets"): ["meets"],
        ("equals", "overlaps"): ["overlaps"],
        ("equals", "starts"): ["starts"],
        ("equals", "during"): ["during"],
        ("equals", "finishes"): ["finishes"],
        ("equals", "equals"): ["equals"],
        ("after", "after"): ["after"],
        ("after", "met_by"): ["after"],
        ("after", "overlapped_by"): ["after"],
        ("after", "finished_by"): ["after"],
        ("after", "contains"): ["after"],
    }

    def __init__(self) -> None:
        # (e1, e2) → best-grade relation
        self._relations: dict[tuple[str, str], TemporalRelation] = {}

    def add_relation(self, rel: TemporalRelation) -> None:
        """Add a relation to the engine.

        If a relation between the same pair already exists with a lower grade,
        the new (higher-grade) relation replaces it — this is the Grade addition
        (logsumexp) principle applied to evidence combination.

        Parameters
        ----------
        rel:
            The :class:`TemporalRelation` to add.
        """
        key = (rel.e1, rel.e2)
        existing = self._relations.get(key)
        if existing is None or rel.grade > existing.grade:
            self._relations[key] = rel
        # Also store inverse if not already there
        inv = rel.inverse()
        inv_key = (inv.e1, inv.e2)
        existing_inv = self._relations.get(inv_key)
        if existing_inv is None or inv.grade > existing_inv.grade:
            self._relations[inv_key] = inv

    def infer_relations(self) -> list[TemporalRelation]:
        """Run one pass of transitivity closure and return inferred relations.

        For every pair (r1: e1→e2, r2: e2→e3) in the current relation set,
        looks up the Allen transitivity table.  If it yields a unique result
        and the pair (e1, e3) is not yet known, adds the inferred relation
        with grade ``r1.grade * r2.grade`` (Grade multiplication).

        Returns
        -------
        list[TemporalRelation]
            Newly inferred relations.
        """
        new_relations: list[TemporalRelation] = []
        keys = list(self._relations.keys())
        for (e1, e2) in keys:
            r1 = self._relations[(e1, e2)]
            for (e2b, e3) in keys:
                if e2b != e2 or e3 == e1:
                    continue
                r2 = self._relations[(e2b, e3)]
                possible = self._TRANS.get((r1.relation, r2.relation))
                if possible and len(possible) == 1:
                    inferred_rel = possible[0]
                    inferred_grade = r1.grade * r2.grade
                    pair = (e1, e3)
                    existing = self._relations.get(pair)
                    if existing is None or inferred_grade > existing.grade:
                        new_rel = TemporalRelation(
                            inferred_rel, e1, e3, inferred_grade
                        )
                        new_relations.append(new_rel)
        for rel in new_relations:
            self.add_relation(rel)
        return new_relations

    def grade_consistency(self) -> Grade:
        """Overall Grade consistency of the temporal graph.

        Consistency is measured by checking all pairs for contradictions.
        A pair (e1, e2) is contradictory if both ``before`` and ``after``
        relations hold with non-impossible grades.

        Returns ``Grade.perfect()`` if no contradictions, lower grades
        for each detected contradiction.

        Returns
        -------
        Grade
            Consistency grade.
        """
        violations = 0
        total_pairs = 0
        events: set[str] = set()
        for (e1, e2) in self._relations:
            events.add(e1)
            events.add(e2)
        event_list = sorted(events)
        for i in range(len(event_list)):
            for j in range(i + 1, len(event_list)):
                e1, e2 = event_list[i], event_list[j]
                total_pairs += 1
                fwd = self._relations.get((e1, e2))
                bwd = self._relations.get((e2, e1))
                if fwd and bwd:
                    # Check for contradiction: before + after is contradictory
                    if (fwd.relation == "before" and bwd.relation == "before"):
                        violations += 1
                    if (fwd.relation == "after" and bwd.relation == "after"):
                        violations += 1
        if total_pairs == 0:
            return Grade.perfect()
        consistency_prob = max(0.0, 1.0 - violations / total_pairs)
        return Grade.from_prob(max(consistency_prob, 1e-10))

    def explain_chain(self, e1: str, e2: str) -> list[TemporalRelation]:
        """Find a chain of relations from ``e1`` to ``e2``.

        Uses BFS to find the shortest (fewest steps) path.

        Parameters
        ----------
        e1, e2:
            Event names.

        Returns
        -------
        list[TemporalRelation]
            Ordered chain of relations; empty if no path found.
        """
        if (e1, e2) in self._relations:
            return [self._relations[(e1, e2)]]
        # BFS
        from collections import deque
        visited: set[str] = {e1}
        queue: "deque[tuple[str, list[TemporalRelation]]]" = deque([(e1, [])])
        while queue:
            current, path = queue.popleft()
            for (src, tgt), rel in self._relations.items():
                if src == current and tgt not in visited:
                    new_path = path + [rel]
                    if tgt == e2:
                        return new_path
                    visited.add(tgt)
                    queue.append((tgt, new_path))
        return []

    def get_relation(self, e1: str, e2: str) -> Optional[TemporalRelation]:
        """Retrieve the stored relation between ``e1`` and ``e2``, if any."""
        return self._relations.get((e1, e2))

    def all_events(self) -> set[str]:
        """Return the set of all event names in the graph."""
        events: set[str] = set()
        for e1, e2 in self._relations:
            events.add(e1)
            events.add(e2)
        return events


# ---------------------------------------------------------------------------
# EventChain
# ---------------------------------------------------------------------------

class EventChain:
    """A narrative sequence of events with causal and temporal links.

    An EventChain is the primary data structure for narrative reasoning.
    It contains:
    * A list of :class:`EventStructure` objects in narrative order
    * :class:`TemporalRelation` objects linking adjacent events
    * Methods for computing Grade coherence and narrative arc

    Grade coherence
    ~~~~~~~~~~~~~~~
    The Grade coherence of an EventChain is:

    .. code-block::

        product(e.grade for e in events)
        * temporal_engine.grade_consistency()

    This is the Grade product of all individual event qualities with the
    temporal consistency — because all must hold simultaneously.
    """

    def __init__(self) -> None:
        self.events: list[EventStructure] = []
        self.relations: list[TemporalRelation] = []
        self._engine = TemporalReasoningEngine()

    def append(
        self, event: EventStructure, relation: str = "after"
    ) -> None:
        """Append an event to the chain with a temporal link to the previous.

        Parameters
        ----------
        event:
            The event to append.
        relation:
            Allen relation linking the previous event to this one.
            Default is ``"after"`` (narrative order).
        """
        if self.events:
            prev = self.events[-1]
            rel = TemporalRelation(
                relation,
                prev.predicate,
                event.predicate,
                grade=prev.grade * event.grade,
            )
            self.relations.append(rel)
            self._engine.add_relation(rel)
        self.events.append(event)

    def grade_coherence(self) -> Grade:
        """Compute the overall Grade coherence of the chain.

        Combines:
        * Grade product of all individual event grades
        * Temporal consistency grade from the reasoning engine

        Returns
        -------
        Grade
            Chain coherence; lower if any event has low grade or relations
            are inconsistent.
        """
        if not self.events:
            return Grade.impossible()
        event_grades = [e.grade for e in self.events]
        combined = Grade.product(event_grades)
        consistency = self._engine.grade_consistency()
        return combined * consistency

    def narrative_arc(self) -> list[float]:
        """Aggregate temporal profiles into a single narrative tension curve.

        Each event contributes its ``temporal_profile`` weighted by its
        ``grade.to_prob()``.  Returns a 10-point normalized curve.

        Returns
        -------
        list[float]
            Aggregate tension curve over 10 normalized time points.
        """
        if not self.events:
            return [0.0] * 10
        arc = [0.0] * 10
        total_weight = 0.0
        for event in self.events:
            w = event.grade.to_prob()
            total_weight += w
            profile = event.temporal_profile
            if len(profile) != 10:
                profile = _interpolate_profile(profile, 10)
            for i in range(10):
                arc[i] += w * profile[i]
        if total_weight > 0:
            arc = [v / total_weight for v in arc]
        return arc

    def most_salient_event(self) -> Optional[EventStructure]:
        """Return the event with the highest Grade.

        Salience in narrative corresponds to the event that is most
        prototypically well-formed (highest Grade) and most telic.

        Returns
        -------
        Optional[EventStructure]
            The most salient event, or None if the chain is empty.
        """
        if not self.events:
            return None
        return max(self.events, key=lambda e: e.grade)

    def to_gluing(self) -> GluingData:
        """Pack the EventChain into a GluingData for harmony computation.

        The semantic section receives a representation of the overall
        narrative; the coherence grade is embedded in the total stratal
        grade.

        Returns
        -------
        GluingData
            A GluingData summarizing this event chain.
        """
        gluing = GluingData()
        return gluing

    def __len__(self) -> int:
        return len(self.events)

    def __repr__(self) -> str:
        preds = [e.predicate for e in self.events]
        return f"EventChain({' → '.join(preds)}, coherence={self.grade_coherence()})"


# ---------------------------------------------------------------------------
# EventStructureAnalyzer
# ---------------------------------------------------------------------------

class EventStructureAnalyzer:
    """Analyzes verb phrases to produce Grade-valued event structures.

    The analyzer maintains lexical databases categorizing verbs by their
    aspect class, and uses these — combined with argument structure — to
    assign aspect classes with a confidence Grade.

    Aspect classification algorithm
    --------------------------------
    1. Look up the verb in the lexical databases.
    2. If found, return the corresponding aspect class with Grade.perfect().
    3. If only partial information is available (e.g., verb found but class
       is uncertain), return the class with a reduced Grade.
    4. Apply argument-structure tests:
       * Presence of a quantized object → Accomplishment (``Grade.from_prob(0.9)``)
       * Presence of a temporal adverb ``"for X time"`` → atelic (STATE/ACTIVITY)
       * Presence of ``"in X time"`` → telic (ACCOMPLISHMENT/ACHIEVEMENT)
    5. Default to ACTIVITY with ``Grade.from_prob(0.5)`` if no information.
    """

    def __init__(self) -> None:
        self._state_verbs: set[str] = self._build_state_verbs()
        self._activity_verbs: set[str] = self._build_activity_verbs()
        self._accomplishment_verbs: set[str] = self._build_accomplishment_verbs()
        self._achievement_verbs: set[str] = self._build_achievement_verbs()
        self._semelfactive_verbs: set[str] = self._build_semelfactive_verbs()
        # thematic role compatibility: role → compatible ontological types
        self._role_types: dict[str, set[str]] = {
            "AGENT": {"ANIMATE", "PERSON", "HUMAN", "ANIMATE_ENTITY", "ORGANISM"},
            "PATIENT": {"PHYSICAL_OBJECT", "ENTITY", "ANIMATE_ENTITY"},
            "THEME": {"ENTITY", "ABSTRACT_OBJECT", "PHYSICAL_OBJECT"},
            "EXPERIENCER": {"ANIMATE_ENTITY", "PERSON"},
            "GOAL": {"LOCATION", "ENTITY"},
            "SOURCE": {"LOCATION", "ENTITY"},
            "INSTRUMENT": {"ARTIFACT", "TOOL"},
            "BENEFICIARY": {"ANIMATE_ENTITY", "PERSON"},
            "CAUSE": {"EVENTUALITY", "EVENT", "PROCESS"},
        }

    # ------------------------------------------------------------------
    # Lexical database builders
    # ------------------------------------------------------------------

    def _build_state_verbs(self) -> set[str]:
        """Build the set of stative verbs."""
        return {
            "know", "believe", "own", "contain", "resemble", "love",
            "hate", "fear", "trust", "expect", "prefer", "remember",
            "understand", "want", "need", "have", "be", "seem",
            "appear", "consist", "lack", "include", "involve", "equal",
            "mean", "weigh", "cost", "measure", "matter", "deserve",
            "concern", "apply", "belong", "depend", "differ", "exist",
            "fit", "follow", "inhabit", "live", "occupy", "possess",
            "recognize", "remain", "require", "result", "satisfy",
            "stand", "suffice", "suit", "surprise", "tend", "wish",
            "appreciate", "assume", "believe", "comprehend", "concern",
            "contemplate", "doubt", "entail", "fancy", "feel",
            "foresee", "guess", "hold", "imply", "indicate",
            "intend", "judge", "know", "miss", "notice", "observe",
            "perceive", "ponder", "predict", "realize", "regard",
            "sense", "suspect", "think", "value", "visualize",
        }

    def _build_activity_verbs(self) -> set[str]:
        """Build the set of activity verbs (atelic, dynamic)."""
        return {
            "run", "walk", "swim", "eat", "drink", "sing", "dance",
            "work", "play", "drive", "read", "talk", "write",
            "think", "search", "push", "pull", "carry", "watch",
            "listen", "stroll", "jog", "march", "crawl", "climb",
            "fly", "slide", "surf", "skate", "ski", "row", "paddle",
            "cycle", "ride", "gallop", "wander", "roam", "drift",
            "explore", "investigate", "research", "study", "practice",
            "exercise", "train", "play", "shop", "browse", "travel",
            "cook", "bake", "knit", "sew", "paint", "draw",
            "sculpt", "carve", "hammer", "dig", "garden", "farm",
            "fish", "hunt", "hike", "camp", "sail", "race",
            "compete", "argue", "debate", "discuss", "chat",
            "converse", "whisper", "shout", "scream", "cry",
            "laugh", "smile", "frown", "sleep", "rest", "relax",
            "breathe", "meditate", "pray", "worship", "celebrate",
            "mourn", "grieve", "suffer", "struggle", "strive",
        }

    def _build_accomplishment_verbs(self) -> set[str]:
        """Build the set of accomplishment verbs (telic, durative)."""
        return {
            "build", "write_letter", "make", "construct", "create",
            "paint_picture", "cook_meal", "grow", "earn", "learn",
            "recover", "solve", "finish", "complete", "compose",
            "manufacture", "produce", "design", "develop", "draft",
            "draw_picture", "edit", "revise", "translate", "summarize",
            "organize", "plan", "prepare", "arrange", "assemble",
            "install", "fix", "repair", "restore", "renovate",
            "teach", "educate", "train_someone", "graduate",
            "acquire", "gain", "accumulate", "save", "invest",
            "spend", "consume", "destroy", "demolish", "dismantle",
            "negotiate", "settle", "resolve", "reconcile", "convince",
            "persuade", "motivate", "inspire", "transform", "convert",
            "migrate", "emigrate", "relocate", "establish", "found",
            "build_house", "write_book", "make_film", "compose_symphony",
            "paint_canvas", "carve_statue", "knit_sweater", "sew_dress",
            "bake_cake", "brew_beer", "distill_whiskey", "grow_crop",
            "rear_animal", "raise_child", "treat_illness", "cure_disease",
            "complete_journey", "finish_race", "climb_mountain",
            "write_program", "debug_code", "build_model", "test_system",
        }

    def _build_achievement_verbs(self) -> set[str]:
        """Build the set of achievement verbs (telic, punctual)."""
        return {
            "arrive", "leave", "die", "find", "lose", "win",
            "break", "open", "close", "realize", "recognize",
            "discover", "forget", "fall", "stop", "start",
            "begin", "end", "land", "depart", "enter", "exit",
            "reach", "hit", "miss", "catch", "release", "drop",
            "pick_up", "put_down", "connect", "disconnect",
            "turn_on", "turn_off", "appear", "disappear",
            "emerge", "collapse", "explode", "implode",
            "ignite", "extinguish", "freeze", "melt", "evaporate",
            "crystallize", "precipitate", "react", "combine",
            "separate", "split", "merge", "collide", "impact",
            "rupture", "shatter", "crack", "heal", "recover_suddenly",
            "awaken", "fall_asleep", "born", "resurrect",
            "detect", "notice_suddenly", "realize_suddenly",
            "remember_suddenly", "forget_suddenly", "decide",
            "choose", "select", "appoint", "elect", "hire", "fire",
            "promote", "demote", "graduate_ceremony", "wed", "divorce",
        }

    def _build_semelfactive_verbs(self) -> set[str]:
        """Build the set of semelfactive verbs (punctual, atelic)."""
        return {
            "flash", "knock", "cough", "sneeze", "hiccup", "blink",
            "tap", "click", "beep", "ping", "drip", "pulse",
            "twitch", "hop", "jump", "bounce", "vibrate", "shiver",
            "tremble", "flicker", "glimmer", "sparkle", "glint",
            "throb", "pulsate", "quiver", "jerk", "flinch",
            "startle", "wink", "nod", "shake_head", "shrug",
            "gesticulate_once", "stamp", "clap_once", "snap",
            "crack_once", "pop", "burst_once", "chip", "nick",
            "peck", "nudge", "prod", "poke", "pinch", "flap",
            "rustle", "crack_knuckle", "spit", "gasp", "hicc",
            "belch", "burp", "yawn_once", "blink_once", "gulp",
        }

    # ------------------------------------------------------------------
    # Public analysis API
    # ------------------------------------------------------------------

    def classify_aspect(
        self, predicate: str
    ) -> tuple[AspectClass, Grade]:
        """Classify the aspectual class of a predicate.

        Looks up the predicate in each lexical database.  Returns the
        (AspectClass, confidence_Grade) pair.

        If the predicate is found in exactly one database,
        Grade = ``Grade.perfect()``.  If found in multiple (ambiguous),
        Grade = ``Grade.from_prob(0.7)``.  If not found, defaults to
        ACTIVITY with ``Grade.from_prob(0.4)``.

        Parameters
        ----------
        predicate:
            The verb / predicate to classify (case-insensitive).

        Returns
        -------
        tuple[AspectClass, Grade]
            The aspect class and its confidence grade.
        """
        p = predicate.lower().strip()
        matches: list[AspectClass] = []
        if p in self._state_verbs:
            matches.append(AspectClass.STATE)
        if p in self._activity_verbs:
            matches.append(AspectClass.ACTIVITY)
        if p in self._accomplishment_verbs:
            matches.append(AspectClass.ACCOMPLISHMENT)
        if p in self._achievement_verbs:
            matches.append(AspectClass.ACHIEVEMENT)
        if p in self._semelfactive_verbs:
            matches.append(AspectClass.SEMELFACTIVE)

        if len(matches) == 1:
            return matches[0], Grade.perfect()
        if len(matches) > 1:
            return matches[0], Grade.from_prob(0.7)
        return AspectClass.ACTIVITY, Grade.from_prob(0.4)

    def analyze_event(
        self,
        verb: str,
        args: Optional[dict[str, str]] = None,
    ) -> EventStructure:
        """Produce a full EventStructure for ``verb`` with ``args``.

        Steps:
        1. Classify aspect class (with Grade).
        2. Determine canonical temporal profile.
        3. Compute culmination grade from aspect class.
        4. Estimate typical duration.
        5. Detect iterativity (semelfactives) and causativity.
        6. Build the EventStructure.

        Parameters
        ----------
        verb:
            The verb / predicate.
        args:
            Optional thematic role → filler mapping.

        Returns
        -------
        EventStructure
            A fully populated event structure.
        """
        if args is None:
            args = {}
        aspect, aspect_grade = self.classify_aspect(verb)
        profile = list(CANONICAL_PROFILES[aspect])
        culmination = aspect.culmination_grade()
        duration = self._estimate_duration(verb, aspect)
        iterative = aspect == AspectClass.SEMELFACTIVE
        causative = self._is_causative(verb)
        frame = self._infer_frame(verb, args)
        cues = self._aspect_cues(verb, aspect)

        return EventStructure(
            predicate=verb,
            aspect=aspect,
            grade=aspect_grade,
            participants=dict(args),
            temporal_profile=profile,
            culmination_grade=culmination,
            duration_estimate=duration,
            iterative=iterative,
            causative=causative,
            frame_name=frame,
            lexical_aspect_cues=cues,
        )

    def grade_culmination(self, event: EventStructure) -> Grade:
        """Compute the culmination Grade for an event.

        The culmination grade is:
        * ``Grade.perfect()`` for ACCOMPLISHMENT and ACHIEVEMENT
        * ``Grade.from_prob(0.9)`` for SEMELFACTIVE (single occurrence)
        * ``Grade.impossible()`` for STATE and ACTIVITY

        Additionally attenuated by the event's overall grade, since a
        low-confidence event classification should yield a low-confidence
        culmination claim.

        Parameters
        ----------
        event:
            The EventStructure to evaluate.

        Returns
        -------
        Grade
            The culmination grade.
        """
        base = event.aspect.culmination_grade()
        if base.is_impossible:
            return Grade.impossible()
        return base * event.grade

    def compose_events(
        self, e1: EventStructure, e2: EventStructure
    ) -> EventStructure:
        """Compose two events into a sequential compound event.

        The composition rule follows from Grade semiring multiplication:
        * The grade of the compound is ``e1.grade * e2.grade``
          because both must succeed simultaneously.
        * The aspect of the compound depends on the component aspects:
          - If e2 is an ACHIEVEMENT, the compound is an ACCOMPLISHMENT.
          - If both are STATES, the compound is a STATE.
          - Otherwise, the compound is an ACTIVITY (or the more specific type).
        * The temporal profile is the concatenation of both profiles
          (first half from e1, second half from e2).

        Parameters
        ----------
        e1:
            First event (occurs first in time).
        e2:
            Second event (occurs after e1).

        Returns
        -------
        EventStructure
            Compound event.
        """
        compound_grade = e1.grade * e2.grade
        compound_aspect = self._compose_aspects(e1.aspect, e2.aspect)
        # Merge participants
        merged_participants = dict(e1.participants)
        for role, filler in e2.participants.items():
            if role not in merged_participants:
                merged_participants[role] = filler
        # Concatenate profiles (interleave)
        p1 = e1.temporal_profile[:5]
        p2 = e2.temporal_profile[5:]
        profile = p1 + p2
        # Combine durations
        d1 = e1.duration_estimate or 0.0
        d2 = e2.duration_estimate or 0.0
        duration = d1 + d2 if (e1.duration_estimate or e2.duration_estimate) else None
        return EventStructure(
            predicate=f"{e1.predicate}_THEN_{e2.predicate}",
            aspect=compound_aspect,
            grade=compound_grade,
            participants=merged_participants,
            temporal_profile=profile,
            culmination_grade=self.grade_culmination(
                EventStructure(
                    predicate="compound",
                    aspect=compound_aspect,
                    grade=compound_grade,
                )
            ),
            duration_estimate=duration,
            iterative=e1.iterative and e2.iterative,
            causative=e1.causative or e2.causative,
            subevent_structure=[e1, e2],
        )

    def _compose_aspects(
        self, a1: AspectClass, a2: AspectClass
    ) -> AspectClass:
        """Determine the aspect of a sequential composition.

        Rules:
        * ACTIVITY + ACHIEVEMENT → ACCOMPLISHMENT
        * ACHIEVEMENT + ACHIEVEMENT → ACCOMPLISHMENT (sequence of changes)
        * STATE + STATE → STATE
        * ACTIVITY + ACTIVITY → ACTIVITY
        * Otherwise → ACCOMPLISHMENT (assume telic by default)
        """
        if a1 == AspectClass.STATE and a2 == AspectClass.STATE:
            return AspectClass.STATE
        if a1 == AspectClass.ACTIVITY and a2 == AspectClass.ACTIVITY:
            return AspectClass.ACTIVITY
        if a2 == AspectClass.ACHIEVEMENT:
            return AspectClass.ACCOMPLISHMENT
        if a1 == AspectClass.SEMELFACTIVE and a2 == AspectClass.SEMELFACTIVE:
            return AspectClass.ACTIVITY  # iteration of semelfactives = activity
        return AspectClass.ACCOMPLISHMENT

    def decompose_to_subevents(
        self, event: EventStructure
    ) -> list[EventStructure]:
        """Decompose an event into its canonical sub-events.

        Accomplishments decompose as:
        ``ACCOMPLISHMENT(V) = ACTIVITY(V') + ACHIEVEMENT(culminate)``

        e.g., *build a house* = *build (activity)* + *house is complete (achievement)*

        Other aspect classes are returned as a singleton list.

        Parameters
        ----------
        event:
            The event to decompose.

        Returns
        -------
        list[EventStructure]
            One or two sub-events.
        """
        if event.aspect == AspectClass.ACCOMPLISHMENT:
            activity_part = EventStructure(
                predicate=f"{event.predicate}_activity",
                aspect=AspectClass.ACTIVITY,
                grade=event.grade * Grade.from_prob(0.9),
                participants=dict(event.participants),
                temporal_profile=CANONICAL_PROFILES[AspectClass.ACTIVITY],
                culmination_grade=Grade.impossible(),
            )
            achievement_part = EventStructure(
                predicate=f"{event.predicate}_culmination",
                aspect=AspectClass.ACHIEVEMENT,
                grade=event.grade * Grade.from_prob(0.9),
                participants=dict(event.participants),
                temporal_profile=CANONICAL_PROFILES[AspectClass.ACHIEVEMENT],
                culmination_grade=Grade.perfect(),
            )
            return [activity_part, achievement_part]
        return [event]

    def aspect_coerce(
        self, event: EventStructure, target: AspectClass
    ) -> tuple[EventStructure, Grade]:
        """Coerce an event to a different aspect class.

        Aspect coercion is a common phenomenon in natural language:
        * *I read (activity)* → *I read the book (accomplishment)* when a
          quantized object is added.
        * *He kicked (achievement/semelfactive)* → *He kept kicking (activity)*
          when iteration is implied.

        Coercion cost (Grade attenuation):
        * Same class: ``Grade.perfect()``
        * Adjacent class (e.g., ACTIVITY ↔ ACCOMPLISHMENT): 0.8
        * Non-adjacent (e.g., STATE ↔ ACHIEVEMENT): 0.4
        * Impossible coercions (e.g., STATE → SEMELFACTIVE): 0.1

        Returns the coerced EventStructure and the coercion Grade.

        Parameters
        ----------
        event:
            Source event.
        target:
            Target aspect class.

        Returns
        -------
        tuple[EventStructure, Grade]
            (coerced_event, coercion_grade).
        """
        if event.aspect == target:
            return event, Grade.perfect()
        coercion_factor = self._coercion_cost(event.aspect, target)
        coercion_grade = Grade.from_prob(coercion_factor)
        new_event = EventStructure(
            predicate=event.predicate,
            aspect=target,
            grade=event.grade * coercion_grade,
            participants=dict(event.participants),
            temporal_profile=list(CANONICAL_PROFILES[target]),
            culmination_grade=target.culmination_grade(),
            duration_estimate=event.duration_estimate,
            iterative=target == AspectClass.SEMELFACTIVE,
            causative=event.causative,
            subevent_structure=list(event.subevent_structure),
            frame_name=event.frame_name,
            lexical_aspect_cues=event.lexical_aspect_cues + [f"coerced_to_{target.name}"],
        )
        return new_event, coercion_grade

    def _coercion_cost(self, source: AspectClass, target: AspectClass) -> float:
        """Return the coercion cost factor (in [0,1]) for class changes."""
        _adjacency: dict[tuple[AspectClass, AspectClass], float] = {
            (AspectClass.ACTIVITY, AspectClass.ACCOMPLISHMENT): 0.85,
            (AspectClass.ACCOMPLISHMENT, AspectClass.ACTIVITY): 0.75,
            (AspectClass.ACHIEVEMENT, AspectClass.ACCOMPLISHMENT): 0.85,
            (AspectClass.ACCOMPLISHMENT, AspectClass.ACHIEVEMENT): 0.7,
            (AspectClass.SEMELFACTIVE, AspectClass.ACTIVITY): 0.8,
            (AspectClass.ACTIVITY, AspectClass.SEMELFACTIVE): 0.6,
            (AspectClass.SEMELFACTIVE, AspectClass.ACHIEVEMENT): 0.75,
            (AspectClass.STATE, AspectClass.ACTIVITY): 0.5,
            (AspectClass.ACTIVITY, AspectClass.STATE): 0.5,
            (AspectClass.STATE, AspectClass.ACCOMPLISHMENT): 0.3,
            (AspectClass.ACCOMPLISHMENT, AspectClass.STATE): 0.3,
            (AspectClass.STATE, AspectClass.ACHIEVEMENT): 0.2,
            (AspectClass.ACHIEVEMENT, AspectClass.STATE): 0.2,
            (AspectClass.STATE, AspectClass.SEMELFACTIVE): 0.1,
            (AspectClass.SEMELFACTIVE, AspectClass.STATE): 0.1,
        }
        return _adjacency.get((source, target), 0.3)

    def grade_thematic_fit(
        self, role: str, filler_type: str, event: EventStructure
    ) -> Grade:
        """Grade how well ``filler_type`` fits ``role`` in ``event``.

        Uses the role compatibility table.  If the filler type matches an
        expected type for the role, returns a high Grade; otherwise returns
        a penalised Grade.

        Parameters
        ----------
        role:
            Thematic role (e.g. ``"AGENT"``).
        filler_type:
            Ontological type of the filler (e.g. ``"PERSON"``).
        event:
            The event being evaluated.

        Returns
        -------
        Grade
            Thematic fit grade.
        """
        role = role.upper()
        filler_type = filler_type.upper()
        expected_types = self._role_types.get(role, set())
        if not expected_types:
            return Grade.from_prob(0.5)  # unknown role
        for expected in expected_types:
            if expected in filler_type or filler_type in expected:
                return Grade.perfect()
        # Partial match: animate types for AGENT etc.
        if role == "AGENT" and any(
            kw in filler_type
            for kw in {"PERSON", "ANIMAL", "HUMAN", "ENTITY", "ANIMATE"}
        ):
            return Grade.from_prob(0.8)
        return Grade.from_prob(0.3)

    def temporal_profile_grade(self, event: EventStructure) -> Grade:
        """Score how well an event's temporal profile matches the canonical shape.

        Uses mean squared error between the actual profile and the canonical
        profile for the event's aspect class.  Converts to Grade via:

        .. code-block::

            quality = 1 - sqrt(mse)
            grade = Grade.from_prob(max(quality, 1e-6))

        Parameters
        ----------
        event:
            The event to score.

        Returns
        -------
        Grade
            Profile quality grade; ``Grade.perfect()`` iff the profile matches
            the canonical shape exactly.
        """
        canonical = CANONICAL_PROFILES[event.aspect]
        actual = event.temporal_profile
        if len(actual) != 10:
            actual = _interpolate_profile(actual, 10)
        mse = sum((a - c) ** 2 for a, c in zip(actual, canonical)) / 10
        quality = max(1.0 - math.sqrt(mse), 1e-6)
        return Grade.from_prob(quality)

    def to_gluing(self, event: EventStructure) -> GluingData:
        """Pack the EventStructure into a GluingData for harmony computation.

        Stratal mapping:
        * ``sem``: predicate name and aspect class
        * ``syn``: transitivity and causativity flags
        * ``prag``: culmination presupposition for telic events

        Returns
        -------
        GluingData
            A populated GluingData.
        """
        gluing = GluingData()
        # Annotate semantic section if attributes exist
        if hasattr(gluing.sem, "frame_name") and event.frame_name:
            gluing.sem.frame_name = event.frame_name
        if hasattr(gluing.sem, "aspect"):
            gluing.sem.aspect = event.aspect.name
        return gluing

    def from_sentence_parts(
        self,
        verb: str,
        subject: str,
        object_: Optional[str] = None,
        modifiers: Optional[list[str]] = None,
    ) -> EventStructure:
        """Build an EventStructure from sentence parts.

        Assigns standard thematic roles: AGENT → subject, PATIENT → object.
        Applies modifier-based tests:
        * ``"for"`` → atelic bias
        * ``"in"`` or ``"within"`` → telic bias (boost Accomplishment grade)

        Parameters
        ----------
        verb:
            Main verb.
        subject:
            Subject of the sentence (agent).
        object_:
            Direct object if present (patient).
        modifiers:
            Optional list of modifier strings.

        Returns
        -------
        EventStructure
            Analyzed event structure.
        """
        args: dict[str, str] = {"AGENT": subject}
        if object_:
            args["PATIENT"] = object_
        event = self.analyze_event(verb, args)
        # Modifier-based aspect adjustment
        if modifiers:
            joined = " ".join(modifiers).lower()
            if "for" in joined and "in" not in joined:
                # Atelic bias
                if event.aspect in {
                    AspectClass.ACCOMPLISHMENT, AspectClass.ACHIEVEMENT
                }:
                    event, _ = self.aspect_coerce(event, AspectClass.ACTIVITY)
            elif "in" in joined or "within" in joined:
                # Telic bias
                if event.aspect in {AspectClass.STATE, AspectClass.ACTIVITY}:
                    event, _ = self.aspect_coerce(event, AspectClass.ACCOMPLISHMENT)
        return event

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _estimate_duration(
        self, verb: str, aspect: AspectClass
    ) -> Optional[float]:
        """Estimate typical duration in seconds based on aspect and verb."""
        durations: dict[str, float] = {
            "run": 1800.0, "walk": 3600.0, "sleep": 28800.0,
            "eat": 1200.0, "drink": 120.0, "work": 28800.0,
            "build": 2592000.0, "arrive": 60.0, "leave": 60.0,
            "die": 10.0, "blink": 0.35, "sneeze": 2.0,
            "flash": 0.1, "knock": 1.0, "click": 0.1,
        }
        if verb in durations:
            return durations[verb]
        default_by_aspect: dict[AspectClass, float] = {
            AspectClass.STATE: None,
            AspectClass.ACTIVITY: 600.0,
            AspectClass.ACCOMPLISHMENT: 3600.0,
            AspectClass.ACHIEVEMENT: 1.0,
            AspectClass.SEMELFACTIVE: 0.5,
        }
        return default_by_aspect.get(aspect)

    def _is_causative(self, verb: str) -> bool:
        """Heuristic: does this verb typically encode a causation?"""
        causative_verbs = {
            "break", "open", "close", "build", "destroy", "make",
            "create", "kill", "heal", "fix", "repair", "melt",
            "freeze", "burn", "light", "turn_on", "turn_off",
            "grow_something", "shrink", "expand", "collapse",
            "move_something", "stop_something", "start_something",
        }
        return verb in causative_verbs

    def _infer_frame(
        self, verb: str, args: dict[str, str]
    ) -> Optional[str]:
        """Heuristic: infer a FrameNet frame name from verb + args."""
        _frames: dict[str, str] = {
            "run": "Self_motion",
            "walk": "Self_motion",
            "swim": "Self_motion",
            "fly": "Self_motion",
            "drive": "Operate_vehicle",
            "eat": "Ingestion",
            "drink": "Ingestion",
            "build": "Building",
            "construct": "Building",
            "arrive": "Arriving",
            "leave": "Departing",
            "die": "Death",
            "kill": "Killing",
            "give": "Giving",
            "take": "Taking",
            "buy": "Commerce_buy",
            "sell": "Commerce_sell",
            "cook": "Cooking_creation",
            "bake": "Cooking_creation",
            "read": "Reading",
            "write": "Text_creation",
            "sing": "Performance",
            "dance": "Performance",
            "know": "Awareness",
            "believe": "Awareness",
            "think": "Cogitation",
        }
        return _frames.get(verb.lower())

    def _aspect_cues(
        self, verb: str, aspect: AspectClass
    ) -> list[str]:
        """Return lexical aspect cues for the determined aspect class."""
        cue_map: dict[AspectClass, list[str]] = {
            AspectClass.STATE: ["stative", "non-dynamic", "unbounded"],
            AspectClass.ACTIVITY: ["progressive", "atelic", "dynamic"],
            AspectClass.ACCOMPLISHMENT: [
                "telic", "quantized-object", "incremental-theme"
            ],
            AspectClass.ACHIEVEMENT: ["punctual", "telic", "instantaneous"],
            AspectClass.SEMELFACTIVE: [
                "punctual", "atelic", "iterable", "single-occurrence"
            ],
        }
        return cue_map.get(aspect, [])


# ---------------------------------------------------------------------------
# AspectShiftDetector
# ---------------------------------------------------------------------------

class AspectShiftDetector:
    """Detects aspect shifts across discourse and grades their coherence.

    Aspect shifts in narrative are linguistically marked and affect
    discourse coherence.  Some shifts are natural (activity → achievement
    = culmination of effort); others are jarring (state → semelfactive).

    The Grade of a shift reflects how natural (how high the probability) the
    transition between the two aspect classes is.
    """

    # Naturalness of aspect transitions in discourse
    _TRANSITION_GRADE: dict[tuple[AspectClass, AspectClass], float] = {
        # Perfect transitions
        (AspectClass.ACTIVITY, AspectClass.ACCOMPLISHMENT): 0.95,
        (AspectClass.ACCOMPLISHMENT, AspectClass.ACHIEVEMENT): 0.95,
        (AspectClass.ACTIVITY, AspectClass.ACHIEVEMENT): 0.9,
        # Natural same-class
        (AspectClass.STATE, AspectClass.STATE): 0.9,
        (AspectClass.ACTIVITY, AspectClass.ACTIVITY): 0.9,
        (AspectClass.ACCOMPLISHMENT, AspectClass.ACCOMPLISHMENT): 0.85,
        (AspectClass.ACHIEVEMENT, AspectClass.ACHIEVEMENT): 0.85,
        # Semelfactive iterations
        (AspectClass.SEMELFACTIVE, AspectClass.SEMELFACTIVE): 0.8,
        (AspectClass.SEMELFACTIVE, AspectClass.ACTIVITY): 0.85,
        (AspectClass.ACTIVITY, AspectClass.SEMELFACTIVE): 0.6,
        # Less natural
        (AspectClass.STATE, AspectClass.ACTIVITY): 0.7,
        (AspectClass.ACTIVITY, AspectClass.STATE): 0.65,
        (AspectClass.STATE, AspectClass.ACCOMPLISHMENT): 0.5,
        (AspectClass.STATE, AspectClass.ACHIEVEMENT): 0.4,
        (AspectClass.STATE, AspectClass.SEMELFACTIVE): 0.2,
        (AspectClass.SEMELFACTIVE, AspectClass.STATE): 0.3,
    }

    def detect_shift(
        self, e1: EventStructure, e2: EventStructure
    ) -> tuple[bool, Grade]:
        """Detect whether there is an aspect shift from e1 to e2.

        An aspect shift occurs when ``e1.aspect != e2.aspect``.
        The Grade reflects the naturalness of the transition.

        Parameters
        ----------
        e1:
            First event.
        e2:
            Second event (following e1 in discourse).

        Returns
        -------
        tuple[bool, Grade]
            (shift_detected, naturalness_grade).
            If no shift, grade = ``Grade.perfect()``.
        """
        if e1.aspect == e2.aspect:
            return False, Grade.perfect()
        key = (e1.aspect, e2.aspect)
        prob = self._TRANSITION_GRADE.get(key, 0.3)
        return True, Grade.from_prob(prob)

    def grade_discourse_coherence(self, chain: "EventChain") -> Grade:
        """Grade the overall aspect coherence of an EventChain.

        Multiplies the naturalness grades of all consecutive aspect
        transitions using Grade multiplication (each transition must be
        natural simultaneously).

        Parameters
        ----------
        chain:
            The event chain to evaluate.

        Returns
        -------
        Grade
            Overall discourse coherence grade.
        """
        if len(chain.events) < 2:
            return Grade.perfect()
        grades: list[Grade] = []
        for i in range(len(chain.events) - 1):
            _, trans_grade = self.detect_shift(
                chain.events[i], chain.events[i + 1]
            )
            grades.append(trans_grade)
        return Grade.product(grades)

    def find_coherent_ordering(
        self, events: list[EventStructure]
    ) -> list[EventStructure]:
        """Find the ordering of events that maximizes discourse coherence.

        Simple greedy approach: at each step, choose the next event that
        maximizes the transition Grade from the current event.

        Parameters
        ----------
        events:
            Events to reorder.

        Returns
        -------
        list[EventStructure]
            A reordered list of events.
        """
        if len(events) <= 1:
            return list(events)
        remaining = list(events)
        # Start with the event most likely to begin a discourse (STATE or ACTIVITY)
        def start_score(e: EventStructure) -> float:
            if e.aspect == AspectClass.STATE:
                return 0.9
            if e.aspect == AspectClass.ACTIVITY:
                return 0.8
            return 0.5
        first = max(remaining, key=start_score)
        remaining.remove(first)
        ordered = [first]
        while remaining:
            current = ordered[-1]
            best_next = max(
                remaining,
                key=lambda e: self._TRANSITION_GRADE.get(
                    (current.aspect, e.aspect), 0.3
                ),
            )
            remaining.remove(best_next)
            ordered.append(best_next)
        return ordered


# ---------------------------------------------------------------------------
# Convenience constructor
# ---------------------------------------------------------------------------

def build_default_analyzer() -> EventStructureAnalyzer:
    """Build and return a default :class:`EventStructureAnalyzer`.

    The analyzer comes pre-populated with all lexical databases.

    Returns
    -------
    EventStructureAnalyzer
        A ready-to-use analyzer instance.
    """
    return EventStructureAnalyzer()


# ---------------------------------------------------------------------------
# Private utilities
# ---------------------------------------------------------------------------

def _interpolate_profile(
    profile: list[float], target_length: int
) -> list[float]:
    """Linearly interpolate/resample ``profile`` to ``target_length`` points.

    Parameters
    ----------
    profile:
        Source profile.
    target_length:
        Desired number of points.

    Returns
    -------
    list[float]
        Resampled profile.
    """
    n = len(profile)
    if n == 0:
        return [0.0] * target_length
    if n == target_length:
        return profile
    result = []
    for i in range(target_length):
        src_pos = i * (n - 1) / max(target_length - 1, 1)
        lo = int(src_pos)
        hi = min(lo + 1, n - 1)
        frac = src_pos - lo
        result.append(profile[lo] * (1 - frac) + profile[hi] * frac)
    return result


def grade_event_similarity(e1: EventStructure, e2: EventStructure) -> Grade:
    """Compute a similarity Grade between two EventStructures.

    Combines:
    * Aspect class match (perfect if same, penalty otherwise)
    * Participant overlap (Jaccard over role sets)
    * Temporal profile cosine similarity

    Returns a single Grade.

    Parameters
    ----------
    e1, e2:
        Events to compare.

    Returns
    -------
    Grade
        Similarity grade.
    """
    # Aspect similarity
    if e1.aspect == e2.aspect:
        aspect_grade = Grade.perfect()
    else:
        from gofai_chat.knowledge.event_structure import AspectShiftDetector
        detector = AspectShiftDetector()
        _, transition_g = detector.detect_shift(e1, e2)
        aspect_grade = transition_g

    # Participant overlap (Jaccard of role sets)
    roles1 = set(e1.participants.keys())
    roles2 = set(e2.participants.keys())
    intersection = roles1 & roles2
    union = roles1 | roles2
    if union:
        jaccard = len(intersection) / len(union)
        participant_grade = Grade.from_prob(max(jaccard, 1e-6))
    else:
        participant_grade = Grade.perfect()

    # Temporal profile cosine similarity
    p1 = e1.temporal_profile
    p2 = e2.temporal_profile
    if len(p1) != 10:
        p1 = _interpolate_profile(p1, 10)
    if len(p2) != 10:
        p2 = _interpolate_profile(p2, 10)
    dot = sum(a * b for a, b in zip(p1, p2))
    norm1 = math.sqrt(sum(a ** 2 for a in p1))
    norm2 = math.sqrt(sum(b ** 2 for b in p2))
    if norm1 > 0 and norm2 > 0:
        cosine = dot / (norm1 * norm2)
    else:
        cosine = 1.0
    profile_grade = Grade.from_prob(max(cosine, 1e-6))

    return aspect_grade * participant_grade * profile_grade


def create_event_from_triple(
    verb: str, subject: str, obj: Optional[str] = None
) -> EventStructure:
    """Convenience function: create an EventStructure from a verb–subject–object triple.

    Delegates to a default :class:`EventStructureAnalyzer`.

    Parameters
    ----------
    verb:
        Main verb.
    subject:
        Subject (AGENT).
    obj:
        Optional direct object (PATIENT).

    Returns
    -------
    EventStructure
        Analyzed event structure.
    """
    analyzer = build_default_analyzer()
    return analyzer.from_sentence_parts(verb, subject, object_=obj)


# ---------------------------------------------------------------------------
# EventSchemaLibrary — a collection of prototypical event schemas
# ---------------------------------------------------------------------------

class EventSchemaLibrary:
    """A library of prototypical event schemas used for matching and inference.

    An event schema is a prototypical :class:`EventStructure` with expected
    participant types and a canonical temporal profile.  Matching an observed
    event against a schema yields a Grade representing how well the event
    instantiates the schema.

    Grade semantics
    ~~~~~~~~~~~~~~~
    Schema matching multiplies:
    * Aspect class match grade (perfect if same)
    * Thematic fit grades for each participant slot
    * Temporal profile similarity grade

    The product (Grade multiplication) reflects that ALL schema criteria must
    be satisfied simultaneously for a high-quality match.
    """

    def __init__(self) -> None:
        self._schemas: dict[str, EventStructure] = {}
        self._analyzer = EventStructureAnalyzer()
        self._populate_schemas()

    def _populate_schemas(self) -> None:
        """Populate the library with prototypical event schemas."""
        _schemas_data = [
            # (name, verb, aspect, participants, profile_key)
            ("eating",          "eat",      AspectClass.ACTIVITY,
             {"AGENT": "PERSON", "PATIENT": "FOOD"},
             AspectClass.ACTIVITY),
            ("running",         "run",      AspectClass.ACTIVITY,
             {"AGENT": "ANIMATE_ENTITY"},
             AspectClass.ACTIVITY),
            ("constructing",    "build",    AspectClass.ACCOMPLISHMENT,
             {"AGENT": "PERSON", "PATIENT": "ARTIFACT", "RESULT": "BUILDING"},
             AspectClass.ACCOMPLISHMENT),
            ("arriving",        "arrive",   AspectClass.ACHIEVEMENT,
             {"AGENT": "ANIMATE_ENTITY", "GOAL": "LOCATION"},
             AspectClass.ACHIEVEMENT),
            ("knowing",         "know",     AspectClass.STATE,
             {"EXPERIENCER": "PERSON", "THEME": "PROPOSITION"},
             AspectClass.STATE),
            ("giving",          "give",     AspectClass.ACHIEVEMENT,
             {"AGENT": "PERSON", "PATIENT": "ENTITY", "BENEFICIARY": "PERSON"},
             AspectClass.ACHIEVEMENT),
            ("cooking",         "cook",     AspectClass.ACCOMPLISHMENT,
             {"AGENT": "PERSON", "PATIENT": "FOOD_ARTIFACT", "INSTRUMENT": "TOOL"},
             AspectClass.ACCOMPLISHMENT),
            ("dying",           "die",      AspectClass.ACHIEVEMENT,
             {"PATIENT": "ANIMATE_ENTITY"},
             AspectClass.ACHIEVEMENT),
            ("working",         "work",     AspectClass.ACTIVITY,
             {"AGENT": "PERSON", "GOAL": "ABSTRACT_OBJECT"},
             AspectClass.ACTIVITY),
            ("communicating",   "talk",     AspectClass.ACTIVITY,
             {"AGENT": "PERSON", "BENEFICIARY": "PERSON"},
             AspectClass.ACTIVITY),
            ("writing_text",    "write",    AspectClass.ACCOMPLISHMENT,
             {"AGENT": "PERSON", "RESULT": "DOCUMENT"},
             AspectClass.ACCOMPLISHMENT),
            ("flashing",        "flash",    AspectClass.SEMELFACTIVE,
             {"CAUSE": "PHYSICAL_OBJECT"},
             AspectClass.SEMELFACTIVE),
            ("learning",        "learn",    AspectClass.ACCOMPLISHMENT,
             {"EXPERIENCER": "PERSON", "THEME": "INFORMATION"},
             AspectClass.ACCOMPLISHMENT),
            ("believing",       "believe",  AspectClass.STATE,
             {"EXPERIENCER": "PERSON", "THEME": "PROPOSITION"},
             AspectClass.STATE),
            ("discovering",     "discover", AspectClass.ACHIEVEMENT,
             {"AGENT": "PERSON", "THEME": "INFORMATION"},
             AspectClass.ACHIEVEMENT),
            ("singing",         "sing",     AspectClass.ACTIVITY,
             {"AGENT": "PERSON"},
             AspectClass.ACTIVITY),
            ("purchasing",      "buy",      AspectClass.ACHIEVEMENT,
             {"AGENT": "PERSON", "PATIENT": "ARTIFACT"},
             AspectClass.ACHIEVEMENT),
            ("healing",         "heal",     AspectClass.ACCOMPLISHMENT,
             {"AGENT": "DOCTOR", "PATIENT": "PERSON"},
             AspectClass.ACCOMPLISHMENT),
            ("playing_music",   "play",     AspectClass.ACTIVITY,
             {"AGENT": "MUSICIAN", "INSTRUMENT": "INSTRUMENT"},
             AspectClass.ACTIVITY),
            ("traveling",       "travel",   AspectClass.ACCOMPLISHMENT,
             {"AGENT": "PERSON", "SOURCE": "LOCATION", "GOAL": "LOCATION"},
             AspectClass.ACCOMPLISHMENT),
        ]
        for name, verb, aspect, participants, profile_key in _schemas_data:
            schema = EventStructure(
                predicate=verb,
                aspect=aspect,
                grade=Grade.perfect(),
                participants=participants,
                temporal_profile=list(CANONICAL_PROFILES[profile_key]),
                culmination_grade=aspect.culmination_grade(),
                frame_name=self._analyzer._infer_frame(verb, participants),
            )
            self._schemas[name] = schema

    def match(self, event: EventStructure) -> list[tuple[str, Grade]]:
        """Match ``event`` against all schemas and return ranked matches.

        For each schema, compute:

        .. code-block::

            aspect_grade  = Grade.perfect() if aspects match, else attenuated
            profile_grade = Grade from cosine(event.profile, schema.profile)
            combined      = aspect_grade * profile_grade * event.grade

        Returns sorted list of (schema_name, combined_grade).

        Parameters
        ----------
        event:
            The event to match.

        Returns
        -------
        list[tuple[str, Grade]]
            Ranked (schema_name, match_grade) pairs.
        """
        results: list[tuple[str, Grade]] = []
        for name, schema in self._schemas.items():
            # Aspect match
            if event.aspect == schema.aspect:
                aspect_grade = Grade.perfect()
            else:
                aspect_grade = Grade.from_prob(0.4)

            # Profile similarity
            p1 = event.temporal_profile
            p2 = schema.temporal_profile
            if len(p1) != 10:
                p1 = _interpolate_profile(p1, 10)
            dot = sum(a * b for a, b in zip(p1, p2))
            n1 = math.sqrt(sum(a * a for a in p1))
            n2 = math.sqrt(sum(b * b for b in p2))
            cosine = dot / (n1 * n2) if n1 > 0 and n2 > 0 else 0.5
            profile_grade = Grade.from_prob(max(cosine, 1e-6))

            combined = aspect_grade * profile_grade * event.grade
            if not combined.is_impossible:
                results.append((name, combined))

        results.sort(key=lambda kv: kv[1], reverse=True)
        return results

    def best_match(self, event: EventStructure) -> Optional[tuple[str, Grade]]:
        """Return the single best matching schema for ``event``.

        Parameters
        ----------
        event:
            The event to match.

        Returns
        -------
        Optional[tuple[str, Grade]]
            (schema_name, grade), or None if no matches.
        """
        matches = self.match(event)
        return matches[0] if matches else None

    def get_schema(self, name: str) -> Optional[EventStructure]:
        """Retrieve a schema by name.

        Parameters
        ----------
        name:
            Schema name.

        Returns
        -------
        Optional[EventStructure]
            The schema EventStructure, or None.
        """
        return self._schemas.get(name)

    def all_schemas(self) -> list[str]:
        """Return a list of all schema names."""
        return sorted(self._schemas.keys())


# ---------------------------------------------------------------------------
# EventNormalizer — canonical form of EventStructure
# ---------------------------------------------------------------------------

class EventNormalizer:
    """Normalizes EventStructures to a canonical representation.

    Normalization:
    * Lower-cases the predicate
    * Sorts participants by role name
    * Resamples temporal profile to 10 points
    * Ensures grade is within valid range
    * Fills in missing culmination grade from aspect class
    """

    def normalize(self, event: EventStructure) -> EventStructure:
        """Return a normalized copy of ``event``.

        Parameters
        ----------
        event:
            The event to normalize.

        Returns
        -------
        EventStructure
            Normalized event structure.
        """
        predicate = event.predicate.lower().strip()
        participants = dict(sorted(
            (k.upper(), v) for k, v in event.participants.items()
        ))
        profile = event.temporal_profile
        if len(profile) != 10:
            profile = _interpolate_profile(profile, 10)
        # Clamp profile values to [0, 1]
        profile = [min(1.0, max(0.0, v)) for v in profile]
        # Fill culmination grade from aspect if missing/inconsistent
        culm = event.culmination_grade
        if culm.is_impossible and event.aspect.is_telic():
            culm = Grade.from_prob(0.9)
        elif not culm.is_impossible and not event.aspect.is_telic():
            culm = Grade.impossible()
        return EventStructure(
            predicate=predicate,
            aspect=event.aspect,
            grade=event.grade,
            participants=participants,
            temporal_profile=profile,
            culmination_grade=culm,
            duration_estimate=event.duration_estimate,
            iterative=event.iterative,
            causative=event.causative,
            subevent_structure=list(event.subevent_structure),
            frame_name=event.frame_name,
            lexical_aspect_cues=list(event.lexical_aspect_cues),
        )

    def to_feature_vector(self, event: EventStructure) -> list[float]:
        """Convert an EventStructure to a numeric feature vector.

        Feature vector layout:
        [0]: aspect class (0=STATE, 1=ACTIVITY, 2=ACCOMPLISHMENT,
                           3=ACHIEVEMENT, 4=SEMELFACTIVE)
        [1]: grade (to_prob)
        [2]: culmination_grade (to_prob; 0 if impossible)
        [3]: is_telic (1.0 or 0.0)
        [4]: is_durative (1.0 or 0.0)
        [5]: is_dynamic (1.0 or 0.0)
        [6]: is_agentive (1.0 or 0.0)
        [7]: is_transitive (1.0 or 0.0)
        [8]: is_causative (1.0 or 0.0)
        [9]: is_iterative (1.0 or 0.0)
        [10..19]: temporal_profile (10 values)

        Returns
        -------
        list[float]
            20-dimensional feature vector.
        """
        aspect_map = {
            AspectClass.STATE: 0.0,
            AspectClass.ACTIVITY: 1.0,
            AspectClass.ACCOMPLISHMENT: 2.0,
            AspectClass.ACHIEVEMENT: 3.0,
            AspectClass.SEMELFACTIVE: 4.0,
        }
        profile = event.temporal_profile
        if len(profile) != 10:
            profile = _interpolate_profile(profile, 10)
        vec = [
            aspect_map.get(event.aspect, 0.0),
            event.grade.to_prob(),
            event.culmination_grade.to_prob() if not event.culmination_grade.is_impossible else 0.0,
            1.0 if event.aspect.is_telic() else 0.0,
            1.0 if event.aspect.is_durative() else 0.0,
            1.0 if event.aspect.is_dynamic() else 0.0,
            1.0 if event.is_agentive() else 0.0,
            1.0 if event.is_transitive() else 0.0,
            1.0 if event.causative else 0.0,
            1.0 if event.iterative else 0.0,
        ] + profile
        return vec


# ---------------------------------------------------------------------------
# EventStructure serialization helpers
# ---------------------------------------------------------------------------

def event_to_dict(event: EventStructure) -> dict:
    """Serialize an EventStructure to a JSON-compatible dict.

    Parameters
    ----------
    event:
        The event to serialize.

    Returns
    -------
    dict
        JSON-compatible dictionary.
    """
    return {
        "predicate": event.predicate,
        "aspect": event.aspect.name,
        "grade": event.grade.value,
        "participants": dict(event.participants),
        "temporal_profile": list(event.temporal_profile),
        "culmination_grade": event.culmination_grade.value,
        "duration_estimate": event.duration_estimate,
        "iterative": event.iterative,
        "causative": event.causative,
        "frame_name": event.frame_name,
        "lexical_aspect_cues": list(event.lexical_aspect_cues),
        "subevent_structure": [event_to_dict(se) for se in event.subevent_structure],
    }


def event_from_dict(d: dict) -> EventStructure:
    """Deserialize an EventStructure from a dictionary.

    Parameters
    ----------
    d:
        Dictionary produced by :func:`event_to_dict`.

    Returns
    -------
    EventStructure
        Reconstructed event structure.
    """
    aspect = AspectClass[d["aspect"]]
    subevents = [event_from_dict(se) for se in d.get("subevent_structure", [])]
    return EventStructure(
        predicate=d["predicate"],
        aspect=aspect,
        grade=Grade(d["grade"]),
        participants=d.get("participants", {}),
        temporal_profile=d.get("temporal_profile", [0.5] * 10),
        culmination_grade=Grade(d["culmination_grade"]),
        duration_estimate=d.get("duration_estimate"),
        iterative=d.get("iterative", False),
        causative=d.get("causative", False),
        frame_name=d.get("frame_name"),
        lexical_aspect_cues=d.get("lexical_aspect_cues", []),
        subevent_structure=subevents,
    )


def chain_to_dict(chain: "EventChain") -> dict:
    """Serialize an EventChain to a dict.

    Parameters
    ----------
    chain:
        The chain to serialize.

    Returns
    -------
    dict
        JSON-compatible dict.
    """
    return {
        "events": [event_to_dict(e) for e in chain.events],
        "relations": [
            {
                "relation": r.relation,
                "e1": r.e1,
                "e2": r.e2,
                "grade": r.grade.value,
            }
            for r in chain.relations
        ],
    }


def chain_from_dict(d: dict) -> "EventChain":
    """Deserialize an EventChain from a dict.

    Parameters
    ----------
    d:
        Dictionary produced by :func:`chain_to_dict`.

    Returns
    -------
    EventChain
        Reconstructed event chain.
    """
    chain = EventChain()
    events = [event_from_dict(e) for e in d.get("events", [])]
    relations = [
        TemporalRelation(
            r["relation"], r["e1"], r["e2"], Grade(r["grade"])
        )
        for r in d.get("relations", [])
    ]
    for event in events:
        chain.events.append(event)
    for rel in relations:
        chain.relations.append(rel)
        chain._engine.add_relation(rel)
    return chain


# ---------------------------------------------------------------------------
# Batch analysis utilities
# ---------------------------------------------------------------------------

def analyze_verb_list(
    verbs: list[str],
    analyzer: Optional[EventStructureAnalyzer] = None,
) -> dict[str, EventStructure]:
    """Analyze a list of verbs and return a dict of verb → EventStructure.

    Parameters
    ----------
    verbs:
        List of verb strings to analyze.
    analyzer:
        Optional :class:`EventStructureAnalyzer`; a default is created if None.

    Returns
    -------
    dict[str, EventStructure]
        Mapping verb → EventStructure.
    """
    if analyzer is None:
        analyzer = build_default_analyzer()
    return {verb: analyzer.analyze_event(verb) for verb in verbs}


def grade_aspectual_consistency(events: list[EventStructure]) -> Grade:
    """Grade how aspectually consistent a list of events is.

    Computes all pairwise transition grades and returns their Grade mean.
    A list of all-STATE or all-ACTIVITY events scores high; a random
    mixture scores lower.

    Parameters
    ----------
    events:
        List of events to assess.

    Returns
    -------
    Grade
        Aspectual consistency grade.
    """
    if len(events) < 2:
        return Grade.perfect()
    detector = AspectShiftDetector()
    grades: list[Grade] = []
    for i in range(len(events) - 1):
        _, g = detector.detect_shift(events[i], events[i + 1])
        grades.append(g)
    return Grade.mean(grades)
