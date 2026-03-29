"""Typed φ-feature structures for Harmonic Grammar syntax/morphology.

Feature theory grounded in Grade semiring, after:
  Chomsky (1995) *The Minimalist Program* — interpretable/uninterpretable features
  Halle & Marantz (1993) "Distributed Morphology" — morphosyntactic feature bundles
  Pollard & Sag (1994) *Head-Driven Phrase Structure Grammar* — typed feature AVMs
  Harley & Ritter (2002) "A feature-geometric analysis of person and number"

Every feature value carries a Grade confidence weight in the semiring.
"""
from __future__ import annotations

__all__ = [
    "Feature",
    "FeatureBundle",
    "PhiFeatures",
    "CaseFeature",
    "TenseFeature",
    "CASE_NOMINATIVE",
    "CASE_ACCUSATIVE",
    "CASE_DATIVE",
    "CASE_GENITIVE",
    "CASE_LOCATIVE",
    "CASE_INSTRUMENTAL",
]

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from gofai_chat.core.grade import Grade


# ══════════════════════════════════════════════════════════════════════════
# Feature — atomic name=value pair with Grade
# ══════════════════════════════════════════════════════════════════════════


@dataclass
class Feature:
    """A single morphosyntactic feature with a Grade confidence weight.

    After Chomsky (1995 ch. 4): features are either *interpretable*
    (contribute to semantic interpretation at LF) or *uninterpretable*
    (must be checked/valued and deleted before LF).

    The Grade indicates how confident we are that this feature has the
    specified value.  Grade.perfect() = certain; Grade.impossible() = clash.
    """

    name: str
    value: str
    grade: Grade = field(default_factory=Grade.perfect)
    interpretable: bool = True

    def matches(self, other: "Feature") -> Grade:
        """Grade of agreement between two feature values.

        Returns Grade.perfect() on identity, Grade.impossible() on hard clash,
        and an intermediate Grade when one side is under-specified ("_").
        """
        if self.name != other.name:
            return Grade.impossible()
        if self.value == other.value:
            return self.grade * other.grade
        if self.value == "_" or other.value == "_":
            # Under-specified: partial match
            return (self.grade * other.grade).attenuate(0.85)
        # Hard clash
        return Grade.impossible()

    def __repr__(self) -> str:
        i = "i" if self.interpretable else "u"
        return f"[{i}{self.name}:{self.value}]"


# ══════════════════════════════════════════════════════════════════════════
# FeatureBundle — typed AVM / feature matrix
# ══════════════════════════════════════════════════════════════════════════


@dataclass
class FeatureBundle:
    """Morphosyntactic feature bundle on a syntactic terminal.

    Corresponds to DM's morphosyntactic feature matrix, HPSG's SYNSEM|LOC|CAT,
    and Minimalism's syntactic feature set F(LI).

    Features are stored as a dict keyed by feature name for O(1) lookup.
    """

    features: Dict[str, Feature] = field(default_factory=dict)

    # ── Query ────────────────────────────────────────────────────────────

    def value(self, feat_name: str) -> Optional[str]:
        """Get the value of a feature (highest-Grade value wins)."""
        f = self.features.get(feat_name)
        return f.value if f else None

    def grade_of(self, feat_name: str) -> Grade:
        """Grade for a specific feature."""
        f = self.features.get(feat_name)
        return f.grade if f else Grade.impossible()

    def has(self, feat_name: str) -> bool:
        return feat_name in self.features

    def get(self, feat_name: str) -> Optional[Feature]:
        return self.features.get(feat_name)

    # ── Agreement ────────────────────────────────────────────────────────

    def agrees_with(self, other: "FeatureBundle") -> Grade:
        """Grade of φ-feature agreement between two bundles.

        Full agreement = Grade.perfect(); partial mismatch = graded penalty.
        The Grade is the product of per-feature match grades for all
        shared feature names.  Missing features on either side are
        treated as under-specified (minor penalty).

        For agreement (unlike unification), a clash on a non-critical
        feature (e.g. Gender in English) is a penalty, not impossible.
        """
        shared = set(self.features.keys()) & set(other.features.keys())
        if not shared:
            return Grade.from_prob(0.5)
        grades: list[Grade] = []
        for name in shared:
            g = self.features[name].matches(other.features[name])
            if g.is_impossible:
                # For agreement: clash is a strong penalty, not death sentence
                grades.append(Grade.from_prob(0.15))
            else:
                grades.append(g)
        return Grade.product(grades)

    # ── Unification ──────────────────────────────────────────────────────

    def unify(self, other: "FeatureBundle") -> Tuple["FeatureBundle", Grade]:
        """Unification (HPSG/LFG-style): merge two bundles.

        Returns (merged_bundle, Grade).
        Grade = product of per-feature unification grades:
          - matching values → perfect
          - one side under-specified → adopt the other, slight penalty
          - clash → impossible (propagated to overall Grade)
        """
        merged: Dict[str, Feature] = {}
        overall = Grade.perfect()

        all_names = set(self.features.keys()) | set(other.features.keys())
        for name in all_names:
            f1 = self.features.get(name)
            f2 = other.features.get(name)
            if f1 and f2:
                g = f1.matches(f2)
                overall = overall * g
                if g.is_impossible:
                    return FeatureBundle(merged), Grade.impossible()
                # Take the more specific value
                winner = f1 if f1.value != "_" else f2
                merged[name] = Feature(
                    name=name,
                    value=winner.value,
                    grade=f1.grade * f2.grade,
                    interpretable=f1.interpretable or f2.interpretable,
                )
            elif f1:
                merged[name] = deepcopy(f1)
            else:
                assert f2 is not None
                merged[name] = deepcopy(f2)

        return FeatureBundle(merged), overall

    # ── Subset check (for DM Vocabulary Insertion) ───────────────────────

    def is_subset_of(self, other: "FeatureBundle") -> Tuple[bool, Grade]:
        """Check whether self's features are a subset of other's.

        Returns (is_subset, match_grade).
        Used by the Subset Principle in Vocabulary Insertion:
        the VI whose feature set is the largest subset of the terminal's
        features wins.
        """
        if not self.features:
            return True, Grade.perfect()
        match_grade = Grade.perfect()
        for name, feat in self.features.items():
            other_feat = other.features.get(name)
            if other_feat is None:
                return False, Grade.impossible()
            g = feat.matches(other_feat)
            if g.is_impossible:
                return False, Grade.impossible()
            match_grade = match_grade * g
        return True, match_grade

    def subset_size(self) -> int:
        """Number of specified (non-'_') features."""
        return sum(1 for f in self.features.values() if f.value != "_")

    # ── Mutation helpers ─────────────────────────────────────────────────

    def set(self, name: str, value: str, grade: Grade | None = None,
            interpretable: bool = True) -> "FeatureBundle":
        """Set a feature value, returning self for chaining."""
        self.features[name] = Feature(
            name=name, value=value,
            grade=grade or Grade.perfect(),
            interpretable=interpretable,
        )
        return self

    def remove(self, name: str) -> "FeatureBundle":
        """Remove a feature by name, returning self for chaining."""
        self.features.pop(name, None)
        return self

    def copy(self) -> "FeatureBundle":
        return FeatureBundle(features={k: deepcopy(v) for k, v in self.features.items()})

    # ── Display ──────────────────────────────────────────────────────────

    def __repr__(self) -> str:
        feats = ", ".join(repr(f) for f in self.features.values())
        return f"FeatureBundle({feats})"

    def compact(self) -> str:
        """Short string: Person=3.Number=sg.Tense=past"""
        return ".".join(f"{f.name}={f.value}" for f in self.features.values())


# ══════════════════════════════════════════════════════════════════════════
# PhiFeatures — Person-Number-Gender triad
# ══════════════════════════════════════════════════════════════════════════


class PhiFeatures(FeatureBundle):
    """Person-Number-Gender triad — the core of agreement morphology.

    After Harley & Ritter (2002) geometric feature theory:
      PARTICIPANT: [SPEAKER, ADDRESSEE]
      INDIVIDUATION: [MINIMAL, GROUP, SINGULAR]
      CLASS: [ANIMATE, FEMININE, MASCULINE, NEUTER, INANIMATE]

    Provides convenience accessors and constructors for the three
    universally attested φ-features.
    """

    def __init__(
        self,
        person: str = "3",
        number: str = "sg",
        gender: str = "_",
        animacy: str = "_",
        grade: Grade | None = None,
    ):
        g = grade or Grade.perfect()
        features: Dict[str, Feature] = {
            "Person": Feature("Person", person, g, True),
            "Number": Feature("Number", number, g, True),
        }
        if gender != "_":
            features["Gender"] = Feature("Gender", gender, g, True)
        if animacy != "_":
            features["Animacy"] = Feature("Animacy", animacy, g, True)
        super().__init__(features=features)

    @property
    def person(self) -> str:
        return self.value("Person") or "3"

    @property
    def number(self) -> str:
        return self.value("Number") or "sg"

    @property
    def gender(self) -> str:
        return self.value("Gender") or "_"

    @property
    def animacy(self) -> str:
        return self.value("Animacy") or "_"

    @classmethod
    def from_bundle(cls, bundle: FeatureBundle) -> "PhiFeatures":
        """Extract φ-features from a full FeatureBundle."""
        phi = cls(
            person=bundle.value("Person") or "3",
            number=bundle.value("Number") or "sg",
            gender=bundle.value("Gender") or "_",
            animacy=bundle.value("Animacy") or "_",
        )
        return phi

    def __repr__(self) -> str:
        parts = [f"Person={self.person}", f"Number={self.number}"]
        if self.gender != "_":
            parts.append(f"Gender={self.gender}")
        return f"PhiFeatures({', '.join(parts)})"


# ══════════════════════════════════════════════════════════════════════════
# CaseFeature — structural / inherent / quirky
# ══════════════════════════════════════════════════════════════════════════


class CaseFeature(Feature):
    """Case after Burzio (1986) and Marantz (1991).

    Structural case: assigned by syntactic configuration
      - Nominative: subject of finite clause (assigned by T)
      - Accusative: object of transitive verb (assigned by v*)
    Inherent case: assigned by specific heads
      - Dative: indirect object (applied by specific V)
      - Genitive: possessor (assigned by D/N)
    Quirky/lexical case: idiosyncratically specified
    """

    case_type: str = "nominative"  # the actual case value
    assignment: str = "structural"  # "structural", "inherent", "lexical", "quirky"

    def __init__(
        self,
        case_type: str = "nominative",
        assignment: str = "structural",
        grade: Grade | None = None,
    ):
        super().__init__(
            name="Case",
            value=case_type,
            grade=grade or Grade.perfect(),
            interpretable=False,
        )
        self.case_type = case_type
        self.assignment = assignment

    def __repr__(self) -> str:
        return f"[Case:{self.case_type}/{self.assignment}]"


# Common case constants
CASE_NOMINATIVE = CaseFeature("nominative", "structural")
CASE_ACCUSATIVE = CaseFeature("accusative", "structural")
CASE_DATIVE = CaseFeature("dative", "inherent")
CASE_GENITIVE = CaseFeature("genitive", "inherent")
CASE_LOCATIVE = CaseFeature("locative", "inherent")
CASE_INSTRUMENTAL = CaseFeature("instrumental", "inherent")


# ══════════════════════════════════════════════════════════════════════════
# TenseFeature — Tense-Aspect-Mood bundle
# ══════════════════════════════════════════════════════════════════════════


@dataclass
class TenseFeature:
    """Tense-Aspect-Mood feature bundle.

    After Reichenbach (1947): S (speech time), R (reference time), E (event time)
    and Comrie (1976, 1985) on tense/aspect universals.

    speech_time   — always 'present' (deictic anchor)
    reference_time — 'past', 'present', 'future' (relative to S)
    event_time    — 'before', 'at', 'after' (relative to R)
    aspect        — 'simple', 'progressive', 'perfect', 'prospective'
    mood          — 'indicative', 'subjunctive', 'conditional', 'imperative'
    """

    speech_time: str = "present"
    reference_time: str = "present"
    event_time: str = "at"
    aspect: str = "simple"
    mood: str = "indicative"
    grade: Grade = field(default_factory=Grade.perfect)

    def to_bundle(self) -> FeatureBundle:
        """Convert to a FeatureBundle for use in agreement/DM."""
        g = self.grade
        feats: Dict[str, Feature] = {
            "Tense": Feature("Tense", self.reference_time, g, True),
            "Aspect": Feature("Aspect", self.aspect, g, True),
            "Mood": Feature("Mood", self.mood, g, True),
        }
        return FeatureBundle(features=feats)

    @classmethod
    def from_bundle(cls, bundle: FeatureBundle) -> "TenseFeature":
        """Extract tense/aspect/mood from a FeatureBundle."""
        return cls(
            reference_time=bundle.value("Tense") or "present",
            aspect=bundle.value("Aspect") or "simple",
            mood=bundle.value("Mood") or "indicative",
        )

    @property
    def is_past(self) -> bool:
        return self.reference_time == "past"

    @property
    def is_present(self) -> bool:
        return self.reference_time == "present"

    @property
    def is_future(self) -> bool:
        return self.reference_time == "future"

    def tense_key(self) -> str:
        """Short key for paradigm lookup: e.g. 'past.simple', 'present.progressive'."""
        return f"{self.reference_time}.{self.aspect}"


# ══════════════════════════════════════════════════════════════════════════
# Builder utilities
# ══════════════════════════════════════════════════════════════════════════


def make_verb_features(
    tense: str = "present",
    aspect: str = "simple",
    person: str = "3",
    number: str = "sg",
    mood: str = "indicative",
    voice: str = "active",
    grade: Grade | None = None,
) -> FeatureBundle:
    """Build a complete verb feature bundle for inflection lookup."""
    g = grade or Grade.perfect()
    return FeatureBundle(features={
        "Tense": Feature("Tense", tense, g, True),
        "Aspect": Feature("Aspect", aspect, g, True),
        "Person": Feature("Person", person, g, True),
        "Number": Feature("Number", number, g, True),
        "Mood": Feature("Mood", mood, g, True),
        "Voice": Feature("Voice", voice, g, False),
    })


def make_noun_features(
    number: str = "sg",
    case: str = "nominative",
    definiteness: str = "indefinite",
    person: str = "3",
    gender: str = "_",
    grade: Grade | None = None,
) -> FeatureBundle:
    """Build a complete noun feature bundle."""
    g = grade or Grade.perfect()
    feats: Dict[str, Feature] = {
        "Number": Feature("Number", number, g, True),
        "Case": Feature("Case", case, g, False),
        "Definiteness": Feature("Definiteness", definiteness, g, True),
        "Person": Feature("Person", person, g, True),
    }
    if gender != "_":
        feats["Gender"] = Feature("Gender", gender, g, True)
    return FeatureBundle(features=feats)


def make_adj_features(
    degree: str = "positive",
    grade: Grade | None = None,
) -> FeatureBundle:
    """Build an adjective feature bundle."""
    g = grade or Grade.perfect()
    return FeatureBundle(features={
        "Degree": Feature("Degree", degree, g, True),
    })


# ══════════════════════════════════════════════════════════════════════════
# Pronoun feature table (English)
# ══════════════════════════════════════════════════════════════════════════

PRONOUN_PHI: Dict[str, PhiFeatures] = {
    "I":    PhiFeatures("1", "sg"),
    "me":   PhiFeatures("1", "sg"),
    "my":   PhiFeatures("1", "sg"),
    "we":   PhiFeatures("1", "pl"),
    "us":   PhiFeatures("1", "pl"),
    "our":  PhiFeatures("1", "pl"),
    "you":  PhiFeatures("2", "sg"),  # also pl
    "your": PhiFeatures("2", "sg"),
    "he":   PhiFeatures("3", "sg", "m"),
    "him":  PhiFeatures("3", "sg", "m"),
    "his":  PhiFeatures("3", "sg", "m"),
    "she":  PhiFeatures("3", "sg", "f"),
    "her":  PhiFeatures("3", "sg", "f"),
    "it":   PhiFeatures("3", "sg", "n"),
    "its":  PhiFeatures("3", "sg", "n"),
    "they": PhiFeatures("3", "pl"),
    "them": PhiFeatures("3", "pl"),
    "their":PhiFeatures("3", "pl"),
}

# Case forms for pronouns: (person, number, case) → surface form
PRONOUN_FORMS: Dict[Tuple[str, str, str], str] = {
    ("1", "sg", "nominative"): "I",
    ("1", "sg", "accusative"): "me",
    ("1", "sg", "genitive"):   "my",
    ("1", "pl", "nominative"): "we",
    ("1", "pl", "accusative"): "us",
    ("1", "pl", "genitive"):   "our",
    ("2", "sg", "nominative"): "you",
    ("2", "sg", "accusative"): "you",
    ("2", "sg", "genitive"):   "your",
    ("2", "pl", "nominative"): "you",
    ("2", "pl", "accusative"): "you",
    ("2", "pl", "genitive"):   "your",
    ("3", "sg", "nominative"): "he",   # default masculine; caller decides
    ("3", "sg", "accusative"): "him",
    ("3", "sg", "genitive"):   "his",
    ("3", "pl", "nominative"): "they",
    ("3", "pl", "accusative"): "them",
    ("3", "pl", "genitive"):   "their",
}
