"""Distributed Morphology: late insertion, Vocabulary Items, contextual allomorphy.

After:
  Halle & Marantz (1993) "Distributed Morphology and the Pieces of Inflection"
  Harley & Noyer (1999) "Distributed Morphology"
  Embick (2015) *The Morpheme: A Theoretical Introduction*
  Bobaljik (2012) *Universals in Comparative Morphology*

Architecture:
  1. Syntax builds a derivation of feature-bearing terminals.
  2. Morphological Structure:
     - Morphological Merger: adjacent heads combine into morphological words
     - Fission: one terminal → multiple morphemes
     - Fusion: adjacent terminals → single morpheme
  3. Vocabulary Insertion: Late Insertion of phonological content.
     Each Vocabulary Item is: /form/ ↔ [features] / context, with Grade weight.
  4. Post-Insertion:
     - Readjustment rules (allomorphy, suppletion)
     - Impoverishment (feature deletion before insertion)
     - Local dislocation (morpheme order adjustment)

Grade semiring interpretation:
  - VI specificity → Grade (more specific = higher Grade)
  - Subset match quality → Grade
  - Allomorphy naturalness → Grade
  - Productivity of a morphological process → Grade
"""
from __future__ import annotations

__all__ = [
    "VocabularyItem",
    "MorphologicalWord",
    "VocabularyInsertion",
    "ImpoverishmentRule",
    "FusionRule",
    "ReadjustmentRule",
    "DMEngine",
    "ENGLISH_VIS",
    "ENGLISH_READJUSTMENTS",
    "ENGLISH_IMPOVERISHMENTS",
]

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Callable

from gofai_chat.core.grade import Grade
from gofai_chat.grammar.features import (
    Feature,
    FeatureBundle,
    PhiFeatures,
    CaseFeature,
    TenseFeature,
    make_verb_features,
    make_noun_features,
    make_adj_features,
)


# ══════════════════════════════════════════════════════════════════════════
# Core DM data structures
# ══════════════════════════════════════════════════════════════════════════


@dataclass
class VocabularyItem:
    """A Vocabulary Item (VI) in DM: phonological form ↔ feature specification.

    VIs are ordered by specificity (Subset Principle: most specific VI wins).
    Grade = probability that this VI applies given the feature bundle.
    """

    form: str
    features: FeatureBundle
    context: Optional[str] = None
    grade: Grade = field(default_factory=Grade.perfect)
    paradigm_cell: str = ""
    pos: str = ""  # "V", "N", "A", "Adv"
    is_suffix: bool = True
    is_suppletive: bool = False

    @property
    def specificity(self) -> int:
        """Number of specified features — used for Subset Principle ordering."""
        return self.features.subset_size()


@dataclass
class MorphologicalWord:
    """A morphological word as an ordered sequence of morphemes."""

    morphemes: List[VocabularyItem] = field(default_factory=list)
    features: FeatureBundle = field(default_factory=FeatureBundle)
    grade: Grade = field(default_factory=Grade.perfect)
    surface: str = ""

    def __repr__(self) -> str:
        return f"MorphWord({self.surface}, grade={self.grade})"


# ══════════════════════════════════════════════════════════════════════════
# Vocabulary Insertion engine (Subset Principle)
# ══════════════════════════════════════════════════════════════════════════


class VocabularyInsertion:
    """Late Insertion engine: FeatureBundle → best VocabularyItem.

    Implements the Subset Principle (Halle 1997): the VI with the
    largest feature subset that is a subset of the terminal's features
    is inserted.  Grade version: Grade(VI, terminal) =
    subset_match_grade(VI.features, terminal.features) * VI.grade.
    """

    def __init__(self, vis: List[VocabularyItem]):
        # Pre-sort by specificity descending, then by grade descending
        self._vis = sorted(
            vis, key=lambda v: (v.specificity, v.grade.to_prob()), reverse=True,
        )
        # Index by pos for fast lookup
        self._by_pos: Dict[str, List[VocabularyItem]] = {}
        for vi in self._vis:
            self._by_pos.setdefault(vi.pos, []).append(vi)

    def insert(
        self, bundle: FeatureBundle, pos: str = ""
    ) -> Tuple[VocabularyItem, Grade]:
        """Find best-matching VI for feature bundle, returning (VI, match_grade).

        Searches VIs for the given POS category, applying the Subset Principle:
        the VI whose features form the largest subset of the terminal's features
        wins, with ties broken by Grade.
        """
        candidates = self._by_pos.get(pos, self._vis) if pos else self._vis
        best_vi: Optional[VocabularyItem] = None
        best_grade = Grade.impossible()
        best_specificity = -1

        for vi in candidates:
            is_sub, match_g = vi.features.is_subset_of(bundle)
            if not is_sub:
                continue
            combined = match_g * vi.grade
            # Subset Principle: prefer more specific; break ties by Grade
            if vi.specificity > best_specificity or (
                vi.specificity == best_specificity and combined > best_grade
            ):
                best_vi = vi
                best_grade = combined
                best_specificity = vi.specificity

        if best_vi is None:
            # Fallback: return empty morpheme with low grade
            return VocabularyItem(form="", features=bundle, grade=Grade.from_prob(0.1)), Grade.from_prob(0.1)
        return best_vi, best_grade


# ══════════════════════════════════════════════════════════════════════════
# DM operations: Impoverishment, Fusion, Readjustment
# ══════════════════════════════════════════════════════════════════════════


@dataclass
class ImpoverishmentRule:
    """DM impoverishment: delete features before Vocabulary Insertion.

    Example: English PAST+3SG → delete Person/Number features
    (English past tense does not mark person/number: *walked* not *walkeds).
    """

    target_features: List[str]  # feature names to delete
    context_features: FeatureBundle  # context that triggers deletion
    grade: Grade = field(default_factory=Grade.perfect)
    description: str = ""

    def applies(self, bundle: FeatureBundle) -> bool:
        """Check whether this impoverishment rule applies to the bundle."""
        is_sub, _ = self.context_features.is_subset_of(bundle)
        return is_sub

    def apply(self, bundle: FeatureBundle) -> FeatureBundle:
        """Apply impoverishment: remove target features from bundle."""
        result = bundle.copy()
        for feat_name in self.target_features:
            result.remove(feat_name)
        return result


@dataclass
class FusionRule:
    """DM fusion: two adjacent terminals fuse into one morpheme.

    Example: [T: PAST] + [Agr: 3SG] → single fused morpheme "-ed"
    """

    head1_features: FeatureBundle
    head2_features: FeatureBundle
    fused_form: str
    grade: Grade = field(default_factory=Grade.perfect)


@dataclass
class ReadjustmentRule:
    """DM readjustment: phonological alteration of a VI after insertion.

    Examples: go → went (suppletion), foot → feet (umlaut),
              child → children (irregular plural)
    """

    input_pattern: str  # regex or exact stem
    input_features: FeatureBundle
    output_form: str  # result after readjustment
    environment: Optional[str] = None
    grade: Grade = field(default_factory=Grade.perfect)
    pos: str = ""

    def applies(self, stem: str, bundle: FeatureBundle) -> bool:
        """Check if this readjustment rule applies."""
        if self.pos and bundle.value("POS") and bundle.value("POS") != self.pos:
            return False
        is_sub, _ = self.input_features.is_subset_of(bundle)
        if not is_sub:
            return False
        return bool(re.fullmatch(self.input_pattern, stem, re.IGNORECASE))

    def apply(self, stem: str) -> str:
        """Apply readjustment, returning new form."""
        return self.output_form


# ══════════════════════════════════════════════════════════════════════════
# DMEngine — the full pipeline
# ══════════════════════════════════════════════════════════════════════════


class DMEngine:
    """Distributed Morphology engine: FeatureBundle → surface morphological word.

    Pipeline:
      1. Impoverishment (feature deletion)
      2. Readjustment check (stem suppletion/allomorphy)
      3. Vocabulary Insertion (subset-principle VI selection)
      4. Concatenation (stem + affix)
    Returns (surface_form, Grade) where Grade = overall morphological wellformedness.
    """

    def __init__(
        self,
        vis: List[VocabularyItem],
        readjustments: Optional[List[ReadjustmentRule]] = None,
        impoverishments: Optional[List[ImpoverishmentRule]] = None,
    ):
        self._inserter = VocabularyInsertion(vis)
        self._readjustments = readjustments or []
        self._impoverishments = impoverishments or []
        self._vis = vis

    def realize(self, features: FeatureBundle, stem: str) -> Tuple[str, Grade]:
        """Full DM pipeline: feature bundle + stem → surface word form.

        Returns (surface_string, Grade) where Grade measures morphological
        wellformedness: product of insertion specificity, readjustment
        naturalness, and phonological wellformedness.
        """
        overall_grade = Grade.perfect()

        # 1. Check readjustment / suppletion FIRST (before impoverishment,
        #    since suppletive forms like be→was pre-empt regular inflection)
        for rule in self._readjustments:
            if rule.applies(stem, features):
                # Suppletive / readjusted: return the whole-word form directly
                return rule.output_form, overall_grade * rule.grade

        # 2. Impoverishment (only for non-suppletive forms)
        working = features.copy()
        for rule in self._impoverishments:
            if rule.applies(working):
                working = rule.apply(working)
                overall_grade = overall_grade * rule.grade

        # 3. Vocabulary Insertion — find best suffix/affix
        pos_hint = working.value("POS") or ""
        vi, vi_grade = self._inserter.insert(working, pos_hint)
        overall_grade = overall_grade * vi_grade

        # 4. Build surface form
        if vi.is_suppletive:
            surface = vi.form
        elif vi.form == "":
            surface = stem
        elif vi.is_suffix:
            surface = self._concatenate(stem, vi.form)
        else:
            surface = vi.form + stem

        return surface, overall_grade

    def analyze(self, word: str, pos: str = "") -> Tuple[FeatureBundle, Grade]:
        """Analytic direction: surface form → feature bundle.

        Tries readjustment rules first (suppletion), then suffix stripping
        against known VIs.
        """
        # Check suppletive/readjusted forms first
        for rule in self._readjustments:
            if rule.output_form.lower() == word.lower():
                return rule.input_features.copy(), rule.grade

        # Try suffix stripping against VIs
        best_bundle = FeatureBundle()
        best_grade = Grade.from_prob(0.1)
        for vi in self._vis:
            if vi.pos and pos and vi.pos != pos:
                continue
            if vi.is_suffix and vi.form and word.endswith(vi.form):
                if vi.grade * Grade.from_prob(0.9) > best_grade:
                    best_bundle = vi.features.copy()
                    best_grade = vi.grade * Grade.from_prob(0.9)

        return best_bundle, best_grade

    # ── Phonological concatenation ───────────────────────────────────────

    @staticmethod
    def _concatenate(stem: str, suffix: str) -> str:
        """Concatenate stem + suffix with English orthographic rules.

        Handles: consonant doubling, e-deletion, y→i, -s/-es alternation.
        """
        if not suffix:
            return stem
        if not stem:
            return suffix

        s = stem.lower()
        suf = suffix

        # -e deletion before vowel-initial suffix
        if s.endswith("e") and suf and suf[0] in "aeiou":
            if suf in ("-ing", "ing"):
                if s.endswith("ee") or s.endswith("ye") or s.endswith("oe"):
                    pass  # keep e: seeing, dyeing
                else:
                    s = s[:-1]
            elif suf in ("-ed", "ed", "-er", "er", "-est", "est", "-able", "able"):
                s = s[:-1]

        # y → i before consonant-initial suffix (not -ing)
        if s.endswith("y") and len(s) > 1 and s[-2] not in "aeiou":
            if suf not in ("-ing", "ing", "-ist", "ist"):
                s = s[:-1] + "i"
                if suf == "-es" or suf == "es":
                    suf = "es"
                elif suf == "-s":
                    suf = "es"

        # Consonant doubling before vowel-initial suffix
        if (
            suf
            and suf.lstrip("-")[0:1] in "aeiou"
            and len(s) >= 3
            and s[-1] in "bcdfghjklmnpqrstvwz"
            and s[-2] in "aeiou"
            and s[-3] not in "aeiou"
            and s[-1] not in "wxy"
            and not s.endswith(("ss", "ll", "ff", "zz", "ck", "ng", "nk"))
        ):
            # Only double for short (monosyllabic) stems or stressed final syllable
            vowel_groups = 0
            in_v = False
            for ch in s:
                if ch in "aeiou":
                    if not in_v:
                        vowel_groups += 1
                        in_v = True
                else:
                    in_v = False
            if vowel_groups <= 1:
                s = s + s[-1]

        # -s → -es after sibilants
        if suf in ("-s", "s") and s.endswith(("s", "z", "x", "sh", "ch")):
            suf = "es"
        elif suf == "-s":
            suf = "s"

        # Strip leading dash from suffix
        if suf.startswith("-"):
            suf = suf[1:]

        return s + suf


# ══════════════════════════════════════════════════════════════════════════
# English Vocabulary Items  (~250 VIs)
# ══════════════════════════════════════════════════════════════════════════


def _vb(form: str, features: Dict[str, str], **kw) -> VocabularyItem:
    """Shorthand for constructing a verbal VI."""
    fb = FeatureBundle({
        k: Feature(k, v, Grade.perfect(), k in ("Tense", "Aspect", "Mood"))
        for k, v in features.items()
    })
    return VocabularyItem(
        form=form, features=fb, pos="V",
        grade=kw.get("grade", Grade.perfect()),
        paradigm_cell=kw.get("cell", ""),
        is_suffix=kw.get("suffix", True),
        is_suppletive=kw.get("suppletive", False),
        context=kw.get("context"),
    )


def _nn(form: str, features: Dict[str, str], **kw) -> VocabularyItem:
    """Shorthand for constructing a nominal VI."""
    fb = FeatureBundle({
        k: Feature(k, v, Grade.perfect(), k in ("Number", "Gender", "Animacy"))
        for k, v in features.items()
    })
    return VocabularyItem(
        form=form, features=fb, pos="N",
        grade=kw.get("grade", Grade.perfect()),
        paradigm_cell=kw.get("cell", ""),
        is_suffix=kw.get("suffix", True),
        is_suppletive=kw.get("suppletive", False),
    )


def _adj(form: str, features: Dict[str, str], **kw) -> VocabularyItem:
    """Shorthand for constructing an adjectival VI."""
    fb = FeatureBundle({
        k: Feature(k, v, Grade.perfect(), True) for k, v in features.items()
    })
    return VocabularyItem(
        form=form, features=fb, pos="A",
        grade=kw.get("grade", Grade.perfect()),
        paradigm_cell=kw.get("cell", ""),
        is_suffix=kw.get("suffix", True),
        is_suppletive=kw.get("suppletive", False),
    )


def _deriv(form: str, features: Dict[str, str], **kw) -> VocabularyItem:
    """Shorthand for constructing a derivational VI."""
    fb = FeatureBundle({
        k: Feature(k, v, Grade.perfect(), True) for k, v in features.items()
    })
    return VocabularyItem(
        form=form, features=fb,
        pos=kw.get("pos", ""),
        grade=kw.get("grade", Grade.from_prob(0.85)),
        paradigm_cell=kw.get("cell", ""),
        is_suffix=kw.get("suffix", True),
        is_suppletive=False,
    )


# ── Verbal inflection VIs ────────────────────────────────────────────────

_VERBAL_VIS: List[VocabularyItem] = [
    # Present tense
    _vb("-s", {"Tense": "present", "Person": "3", "Number": "sg"},
        cell="PRES.3SG"),
    _vb("", {"Tense": "present", "Person": "1", "Number": "sg"},
        cell="PRES.1SG"),
    _vb("", {"Tense": "present", "Person": "2", "Number": "sg"},
        cell="PRES.2SG"),
    _vb("", {"Tense": "present", "Person": "1", "Number": "pl"},
        cell="PRES.1PL"),
    _vb("", {"Tense": "present", "Person": "2", "Number": "pl"},
        cell="PRES.2PL"),
    _vb("", {"Tense": "present", "Person": "3", "Number": "pl"},
        cell="PRES.3PL"),

    # Past tense (regular)
    _vb("-ed", {"Tense": "past"}, cell="PAST", grade=Grade.from_prob(0.95)),

    # Progressive / gerund
    _vb("-ing", {"Aspect": "progressive"}, cell="PROG"),
    _vb("-ing", {"VerbForm": "ger"}, cell="GER"),

    # Past participle (regular)
    _vb("-ed", {"VerbForm": "past_part"}, cell="PAST.PART",
        grade=Grade.from_prob(0.90)),
    _vb("-en", {"VerbForm": "past_part"}, cell="PAST.PART.EN",
        grade=Grade.from_prob(0.70)),

    # Infinitive (bare)
    _vb("", {"VerbForm": "inf"}, cell="INF", grade=Grade.from_prob(0.99)),

    # Imperative
    _vb("", {"Mood": "imperative"}, cell="IMP"),

    # Subjunctive (bare form)
    _vb("", {"Mood": "subjunctive"}, cell="SUBJ"),
]

# ── Nominal inflection VIs ───────────────────────────────────────────────

_NOMINAL_VIS: List[VocabularyItem] = [
    # Regular plural
    _nn("-s", {"Number": "pl"}, cell="PL", grade=Grade.from_prob(0.95)),
    _nn("-es", {"Number": "pl"}, cell="PL.ES", grade=Grade.from_prob(0.90),
        context="after_sibilant"),

    # Singular (zero morpheme)
    _nn("", {"Number": "sg"}, cell="SG"),

    # Possessive
    _nn("'s", {"Case": "genitive", "Number": "sg"}, cell="POSS.SG"),
    _nn("'", {"Case": "genitive", "Number": "pl"}, cell="POSS.PL"),
]

# ── Adjectival inflection VIs ────────────────────────────────────────────

_ADJECTIVAL_VIS: List[VocabularyItem] = [
    # Positive (zero)
    _adj("", {"Degree": "positive"}, cell="POS"),

    # Comparative
    _adj("-er", {"Degree": "comparative"}, cell="COMP",
         grade=Grade.from_prob(0.90)),

    # Superlative
    _adj("-est", {"Degree": "superlative"}, cell="SUPERL",
         grade=Grade.from_prob(0.90)),
]

# ── Derivational VIs ─────────────────────────────────────────────────────

_DERIVATIONAL_VIS: List[VocabularyItem] = [
    # V → N nominalisation
    _deriv("-tion", {"Derivation": "nominalise", "Source": "V"},
           pos="N", grade=Grade.from_prob(0.90), cell="V>N.TION"),
    _deriv("-sion", {"Derivation": "nominalise", "Source": "V"},
           pos="N", grade=Grade.from_prob(0.85), cell="V>N.SION"),
    _deriv("-ment", {"Derivation": "nominalise", "Source": "V"},
           pos="N", grade=Grade.from_prob(0.70), cell="V>N.MENT"),
    _deriv("-ance", {"Derivation": "nominalise", "Source": "V"},
           pos="N", grade=Grade.from_prob(0.75), cell="V>N.ANCE"),
    _deriv("-ence", {"Derivation": "nominalise", "Source": "V"},
           pos="N", grade=Grade.from_prob(0.75), cell="V>N.ENCE"),
    _deriv("-ing", {"Derivation": "nominalise", "Source": "V"},
           pos="N", grade=Grade.from_prob(0.92), cell="V>N.ING"),
    _deriv("-al", {"Derivation": "nominalise", "Source": "V"},
           pos="N", grade=Grade.from_prob(0.72), cell="V>N.AL"),
    _deriv("-ure", {"Derivation": "nominalise", "Source": "V"},
           pos="N", grade=Grade.from_prob(0.65), cell="V>N.URE"),
    _deriv("-ery", {"Derivation": "nominalise", "Source": "V"},
           pos="N", grade=Grade.from_prob(0.60), cell="V>N.ERY"),
    _deriv("-age", {"Derivation": "nominalise", "Source": "V"},
           pos="N", grade=Grade.from_prob(0.62), cell="V>N.AGE"),

    # A → N nominalisation
    _deriv("-ness", {"Derivation": "nominalise", "Source": "A"},
           pos="N", grade=Grade.from_prob(0.95), cell="A>N.NESS"),
    _deriv("-ity", {"Derivation": "nominalise", "Source": "A"},
           pos="N", grade=Grade.from_prob(0.75), cell="A>N.ITY"),
    _deriv("-cy", {"Derivation": "nominalise", "Source": "A"},
           pos="N", grade=Grade.from_prob(0.65), cell="A>N.CY"),
    _deriv("-th", {"Derivation": "nominalise", "Source": "A"},
           pos="N", grade=Grade.from_prob(0.55), cell="A>N.TH"),

    # N/A → V verbalisation
    _deriv("-ize", {"Derivation": "verbalise", "Source": "N"},
           pos="V", grade=Grade.from_prob(0.88), cell="N>V.IZE"),
    _deriv("-ify", {"Derivation": "verbalise", "Source": "N"},
           pos="V", grade=Grade.from_prob(0.78), cell="N>V.IFY"),
    _deriv("-en", {"Derivation": "verbalise", "Source": "A"},
           pos="V", grade=Grade.from_prob(0.80), cell="A>V.EN"),
    _deriv("-ate", {"Derivation": "verbalise", "Source": "N"},
           pos="V", grade=Grade.from_prob(0.72), cell="N>V.ATE"),

    # N/V → A adjectivalisation
    _deriv("-al", {"Derivation": "adjectivalise", "Source": "N"},
           pos="A", grade=Grade.from_prob(0.82), cell="N>A.AL"),
    _deriv("-ic", {"Derivation": "adjectivalise", "Source": "N"},
           pos="A", grade=Grade.from_prob(0.80), cell="N>A.IC"),
    _deriv("-ical", {"Derivation": "adjectivalise", "Source": "N"},
           pos="A", grade=Grade.from_prob(0.78), cell="N>A.ICAL"),
    _deriv("-ous", {"Derivation": "adjectivalise", "Source": "N"},
           pos="A", grade=Grade.from_prob(0.78), cell="N>A.OUS"),
    _deriv("-ious", {"Derivation": "adjectivalise", "Source": "N"},
           pos="A", grade=Grade.from_prob(0.76), cell="N>A.IOUS"),
    _deriv("-ful", {"Derivation": "adjectivalise", "Source": "N"},
           pos="A", grade=Grade.from_prob(0.85), cell="N>A.FUL"),
    _deriv("-less", {"Derivation": "adjectivalise", "Source": "N"},
           pos="A", grade=Grade.from_prob(0.87), cell="N>A.LESS"),
    _deriv("-able", {"Derivation": "adjectivalise", "Source": "V"},
           pos="A", grade=Grade.from_prob(0.88), cell="V>A.ABLE"),
    _deriv("-ible", {"Derivation": "adjectivalise", "Source": "V"},
           pos="A", grade=Grade.from_prob(0.75), cell="V>A.IBLE"),
    _deriv("-ive", {"Derivation": "adjectivalise", "Source": "V"},
           pos="A", grade=Grade.from_prob(0.80), cell="V>A.IVE"),
    _deriv("-ant", {"Derivation": "adjectivalise", "Source": "V"},
           pos="A", grade=Grade.from_prob(0.72), cell="V>A.ANT"),
    _deriv("-ent", {"Derivation": "adjectivalise", "Source": "V"},
           pos="A", grade=Grade.from_prob(0.72), cell="V>A.ENT"),
    _deriv("-ary", {"Derivation": "adjectivalise", "Source": "N"},
           pos="A", grade=Grade.from_prob(0.70), cell="N>A.ARY"),
    _deriv("-ory", {"Derivation": "adjectivalise", "Source": "V"},
           pos="A", grade=Grade.from_prob(0.68), cell="V>A.ORY"),
    _deriv("-ish", {"Derivation": "adjectivalise", "Source": "N"},
           pos="A", grade=Grade.from_prob(0.82), cell="N>A.ISH"),
    _deriv("-like", {"Derivation": "adjectivalise", "Source": "N"},
           pos="A", grade=Grade.from_prob(0.80), cell="N>A.LIKE"),
    _deriv("-y", {"Derivation": "adjectivalise", "Source": "N"},
           pos="A", grade=Grade.from_prob(0.85), cell="N>A.Y"),
    _deriv("-ed", {"Derivation": "adjectivalise", "Source": "N"},
           pos="A", grade=Grade.from_prob(0.82), cell="N>A.ED"),

    # Agentive / patient nominals
    _deriv("-er", {"Derivation": "agentive", "Source": "V"},
           pos="N", grade=Grade.from_prob(0.90), cell="V>N.AGENT.ER"),
    _deriv("-or", {"Derivation": "agentive", "Source": "V"},
           pos="N", grade=Grade.from_prob(0.82), cell="V>N.AGENT.OR"),
    _deriv("-ist", {"Derivation": "agentive", "Source": "N"},
           pos="N", grade=Grade.from_prob(0.85), cell="N>N.AGENT.IST"),
    _deriv("-ee", {"Derivation": "patientive", "Source": "V"},
           pos="N", grade=Grade.from_prob(0.80), cell="V>N.PATIENT.EE"),
    _deriv("-ant", {"Derivation": "agentive", "Source": "V"},
           pos="N", grade=Grade.from_prob(0.72), cell="V>N.AGENT.ANT"),
    _deriv("-ent", {"Derivation": "agentive", "Source": "V"},
           pos="N", grade=Grade.from_prob(0.72), cell="V>N.AGENT.ENT"),

    # A → Adv
    _deriv("-ly", {"Derivation": "adverbialise", "Source": "A"},
           pos="Adv", grade=Grade.from_prob(0.95), cell="A>ADV.LY"),

    # Negation prefix
    _deriv("un-", {"Derivation": "negate", "Source": "A"},
           pos="A", grade=Grade.from_prob(0.90), cell="NEG.UN", suffix=False),
    _deriv("in-", {"Derivation": "negate", "Source": "A"},
           pos="A", grade=Grade.from_prob(0.78), cell="NEG.IN", suffix=False),
    _deriv("im-", {"Derivation": "negate", "Source": "A"},
           pos="A", grade=Grade.from_prob(0.78), cell="NEG.IM", suffix=False),
    _deriv("ir-", {"Derivation": "negate", "Source": "A"},
           pos="A", grade=Grade.from_prob(0.70), cell="NEG.IR", suffix=False),
    _deriv("il-", {"Derivation": "negate", "Source": "A"},
           pos="A", grade=Grade.from_prob(0.70), cell="NEG.IL", suffix=False),
    _deriv("dis-", {"Derivation": "negate", "Source": "V"},
           pos="V", grade=Grade.from_prob(0.82), cell="NEG.DIS", suffix=False),
    _deriv("mis-", {"Derivation": "negate", "Source": "V"},
           pos="V", grade=Grade.from_prob(0.80), cell="NEG.MIS", suffix=False),
    _deriv("non-", {"Derivation": "negate", "Source": "N"},
           pos="N", grade=Grade.from_prob(0.88), cell="NEG.NON", suffix=False),

    # Diminutive / augmentative
    _deriv("-let", {"Derivation": "diminutive", "Source": "N"},
           pos="N", grade=Grade.from_prob(0.65), cell="DIM.LET"),
    _deriv("-ette", {"Derivation": "diminutive", "Source": "N"},
           pos="N", grade=Grade.from_prob(0.60), cell="DIM.ETTE"),
    _deriv("-ling", {"Derivation": "diminutive", "Source": "N"},
           pos="N", grade=Grade.from_prob(0.60), cell="DIM.LING"),

    # Re- prefix (repetition)
    _deriv("re-", {"Derivation": "repetitive", "Source": "V"},
           pos="V", grade=Grade.from_prob(0.90), cell="REP.RE", suffix=False),

    # Over-/under- (degree)
    _deriv("over-", {"Derivation": "excessive", "Source": "V"},
           pos="V", grade=Grade.from_prob(0.85), cell="DEG.OVER", suffix=False),
    _deriv("under-", {"Derivation": "deficient", "Source": "V"},
           pos="V", grade=Grade.from_prob(0.85), cell="DEG.UNDER", suffix=False),

    # Pre-/post- (temporal)
    _deriv("pre-", {"Derivation": "anterior", "Source": "N"},
           pos="A", grade=Grade.from_prob(0.82), cell="TEMP.PRE", suffix=False),
    _deriv("post-", {"Derivation": "posterior", "Source": "N"},
           pos="A", grade=Grade.from_prob(0.82), cell="TEMP.POST", suffix=False),

    # Super-/sub-
    _deriv("super-", {"Derivation": "augmentative", "Source": "N"},
           pos="N", grade=Grade.from_prob(0.78), cell="AUG.SUPER", suffix=False),
    _deriv("sub-", {"Derivation": "subordinate", "Source": "N"},
           pos="N", grade=Grade.from_prob(0.78), cell="SUB.SUB", suffix=False),

    # Inter-/intra-
    _deriv("inter-", {"Derivation": "reciprocal", "Source": "A"},
           pos="A", grade=Grade.from_prob(0.75), cell="RECIP.INTER", suffix=False),
    _deriv("intra-", {"Derivation": "internal", "Source": "A"},
           pos="A", grade=Grade.from_prob(0.72), cell="INT.INTRA", suffix=False),

    # -dom, -hood, -ship (abstract N from N)
    _deriv("-dom", {"Derivation": "abstract", "Source": "N"},
           pos="N", grade=Grade.from_prob(0.60), cell="N>N.DOM"),
    _deriv("-hood", {"Derivation": "abstract", "Source": "N"},
           pos="N", grade=Grade.from_prob(0.62), cell="N>N.HOOD"),
    _deriv("-ship", {"Derivation": "abstract", "Source": "N"},
           pos="N", grade=Grade.from_prob(0.65), cell="N>N.SHIP"),

    # -ward(s) (directional)
    _deriv("-ward", {"Derivation": "directional", "Source": "N"},
           pos="Adv", grade=Grade.from_prob(0.72), cell="N>ADV.WARD"),
    _deriv("-wards", {"Derivation": "directional", "Source": "N"},
           pos="Adv", grade=Grade.from_prob(0.70), cell="N>ADV.WARDS"),

    # -wise (manner)
    _deriv("-wise", {"Derivation": "manner", "Source": "N"},
           pos="Adv", grade=Grade.from_prob(0.75), cell="N>ADV.WISE"),
]


# ══════════════════════════════════════════════════════════════════════════
# English Readjustment Rules (suppletion / irregular paradigms)
# ══════════════════════════════════════════════════════════════════════════


def _readj(pattern: str, feats: Dict[str, str], output: str, **kw) -> ReadjustmentRule:
    """Shorthand for readjustment rule construction."""
    fb = FeatureBundle({
        k: Feature(k, v, Grade.perfect(), True) for k, v in feats.items()
    })
    return ReadjustmentRule(
        input_pattern=pattern,
        input_features=fb,
        output_form=output,
        grade=kw.get("grade", Grade.perfect()),
        pos=kw.get("pos", ""),
    )


ENGLISH_READJUSTMENTS: List[ReadjustmentRule] = [
    # ── Suppletive verb paradigms ────────────────────────────────────────
    # BE
    _readj("be", {"Tense": "present", "Person": "1", "Number": "sg"}, "am", pos="V"),
    _readj("be", {"Tense": "present", "Person": "2", "Number": "sg"}, "are", pos="V"),
    _readj("be", {"Tense": "present", "Person": "3", "Number": "sg"}, "is", pos="V"),
    _readj("be", {"Tense": "present", "Number": "pl"}, "are", pos="V"),
    _readj("be", {"Tense": "past", "Person": "1", "Number": "sg"}, "was", pos="V"),
    _readj("be", {"Tense": "past", "Person": "2", "Number": "sg"}, "were", pos="V"),
    _readj("be", {"Tense": "past", "Person": "3", "Number": "sg"}, "was", pos="V"),
    _readj("be", {"Tense": "past", "Number": "pl"}, "were", pos="V"),
    _readj("be", {"VerbForm": "past_part"}, "been", pos="V"),
    _readj("be", {"Aspect": "progressive"}, "being", pos="V"),

    # HAVE
    _readj("have", {"Tense": "present", "Person": "3", "Number": "sg"}, "has", pos="V"),
    _readj("have", {"Tense": "past"}, "had", pos="V"),
    _readj("have", {"VerbForm": "past_part"}, "had", pos="V"),

    # DO
    _readj("do", {"Tense": "present", "Person": "3", "Number": "sg"}, "does", pos="V"),
    _readj("do", {"Tense": "past"}, "did", pos="V"),
    _readj("do", {"VerbForm": "past_part"}, "done", pos="V"),

    # GO
    _readj("go", {"Tense": "past"}, "went", pos="V"),
    _readj("go", {"VerbForm": "past_part"}, "gone", pos="V"),
    _readj("go", {"Tense": "present", "Person": "3", "Number": "sg"}, "goes", pos="V"),

    # SAY
    _readj("say", {"Tense": "past"}, "said", pos="V"),
    _readj("say", {"VerbForm": "past_part"}, "said", pos="V"),

    # MAKE
    _readj("make", {"Tense": "past"}, "made", pos="V"),
    _readj("make", {"VerbForm": "past_part"}, "made", pos="V"),

    # TAKE
    _readj("take", {"Tense": "past"}, "took", pos="V"),
    _readj("take", {"VerbForm": "past_part"}, "taken", pos="V"),

    # COME
    _readj("come", {"Tense": "past"}, "came", pos="V"),
    _readj("come", {"VerbForm": "past_part"}, "come", pos="V"),

    # SEE
    _readj("see", {"Tense": "past"}, "saw", pos="V"),
    _readj("see", {"VerbForm": "past_part"}, "seen", pos="V"),

    # KNOW
    _readj("know", {"Tense": "past"}, "knew", pos="V"),
    _readj("know", {"VerbForm": "past_part"}, "known", pos="V"),

    # GET
    _readj("get", {"Tense": "past"}, "got", pos="V"),
    _readj("get", {"VerbForm": "past_part"}, "gotten", pos="V"),

    # GIVE
    _readj("give", {"Tense": "past"}, "gave", pos="V"),
    _readj("give", {"VerbForm": "past_part"}, "given", pos="V"),

    # FIND
    _readj("find", {"Tense": "past"}, "found", pos="V"),
    _readj("find", {"VerbForm": "past_part"}, "found", pos="V"),

    # THINK
    _readj("think", {"Tense": "past"}, "thought", pos="V"),
    _readj("think", {"VerbForm": "past_part"}, "thought", pos="V"),

    # TELL
    _readj("tell", {"Tense": "past"}, "told", pos="V"),
    _readj("tell", {"VerbForm": "past_part"}, "told", pos="V"),

    # BECOME
    _readj("become", {"Tense": "past"}, "became", pos="V"),
    _readj("become", {"VerbForm": "past_part"}, "become", pos="V"),

    # LEAVE
    _readj("leave", {"Tense": "past"}, "left", pos="V"),
    _readj("leave", {"VerbForm": "past_part"}, "left", pos="V"),

    # FEEL
    _readj("feel", {"Tense": "past"}, "felt", pos="V"),
    _readj("feel", {"VerbForm": "past_part"}, "felt", pos="V"),

    # PUT
    _readj("put", {"Tense": "past"}, "put", pos="V"),
    _readj("put", {"VerbForm": "past_part"}, "put", pos="V"),

    # BRING
    _readj("bring", {"Tense": "past"}, "brought", pos="V"),
    _readj("bring", {"VerbForm": "past_part"}, "brought", pos="V"),

    # BEGIN
    _readj("begin", {"Tense": "past"}, "began", pos="V"),
    _readj("begin", {"VerbForm": "past_part"}, "begun", pos="V"),

    # KEEP
    _readj("keep", {"Tense": "past"}, "kept", pos="V"),
    _readj("keep", {"VerbForm": "past_part"}, "kept", pos="V"),

    # HOLD
    _readj("hold", {"Tense": "past"}, "held", pos="V"),
    _readj("hold", {"VerbForm": "past_part"}, "held", pos="V"),

    # WRITE
    _readj("write", {"Tense": "past"}, "wrote", pos="V"),
    _readj("write", {"VerbForm": "past_part"}, "written", pos="V"),

    # STAND
    _readj("stand", {"Tense": "past"}, "stood", pos="V"),
    _readj("stand", {"VerbForm": "past_part"}, "stood", pos="V"),

    # HEAR
    _readj("hear", {"Tense": "past"}, "heard", pos="V"),
    _readj("hear", {"VerbForm": "past_part"}, "heard", pos="V"),

    # LET
    _readj("let", {"Tense": "past"}, "let", pos="V"),
    _readj("let", {"VerbForm": "past_part"}, "let", pos="V"),

    # MEAN
    _readj("mean", {"Tense": "past"}, "meant", pos="V"),
    _readj("mean", {"VerbForm": "past_part"}, "meant", pos="V"),

    # SET
    _readj("set", {"Tense": "past"}, "set", pos="V"),
    _readj("set", {"VerbForm": "past_part"}, "set", pos="V"),

    # MEET
    _readj("meet", {"Tense": "past"}, "met", pos="V"),
    _readj("meet", {"VerbForm": "past_part"}, "met", pos="V"),

    # RUN
    _readj("run", {"Tense": "past"}, "ran", pos="V"),
    _readj("run", {"VerbForm": "past_part"}, "run", pos="V"),

    # PAY
    _readj("pay", {"Tense": "past"}, "paid", pos="V"),
    _readj("pay", {"VerbForm": "past_part"}, "paid", pos="V"),

    # SIT
    _readj("sit", {"Tense": "past"}, "sat", pos="V"),
    _readj("sit", {"VerbForm": "past_part"}, "sat", pos="V"),

    # SPEAK
    _readj("speak", {"Tense": "past"}, "spoke", pos="V"),
    _readj("speak", {"VerbForm": "past_part"}, "spoken", pos="V"),

    # LIE
    _readj("lie", {"Tense": "past"}, "lay", pos="V"),
    _readj("lie", {"VerbForm": "past_part"}, "lain", pos="V"),

    # LEAD
    _readj("lead", {"Tense": "past"}, "led", pos="V"),
    _readj("lead", {"VerbForm": "past_part"}, "led", pos="V"),

    # READ
    _readj("read", {"Tense": "past"}, "read", pos="V"),
    _readj("read", {"VerbForm": "past_part"}, "read", pos="V"),

    # GROW
    _readj("grow", {"Tense": "past"}, "grew", pos="V"),
    _readj("grow", {"VerbForm": "past_part"}, "grown", pos="V"),

    # LOSE
    _readj("lose", {"Tense": "past"}, "lost", pos="V"),
    _readj("lose", {"VerbForm": "past_part"}, "lost", pos="V"),

    # FALL
    _readj("fall", {"Tense": "past"}, "fell", pos="V"),
    _readj("fall", {"VerbForm": "past_part"}, "fallen", pos="V"),

    # SEND
    _readj("send", {"Tense": "past"}, "sent", pos="V"),
    _readj("send", {"VerbForm": "past_part"}, "sent", pos="V"),

    # BUILD
    _readj("build", {"Tense": "past"}, "built", pos="V"),
    _readj("build", {"VerbForm": "past_part"}, "built", pos="V"),

    # UNDERSTAND
    _readj("understand", {"Tense": "past"}, "understood", pos="V"),
    _readj("understand", {"VerbForm": "past_part"}, "understood", pos="V"),

    # DRAW
    _readj("draw", {"Tense": "past"}, "drew", pos="V"),
    _readj("draw", {"VerbForm": "past_part"}, "drawn", pos="V"),

    # BREAK
    _readj("break", {"Tense": "past"}, "broke", pos="V"),
    _readj("break", {"VerbForm": "past_part"}, "broken", pos="V"),

    # SPEND
    _readj("spend", {"Tense": "past"}, "spent", pos="V"),
    _readj("spend", {"VerbForm": "past_part"}, "spent", pos="V"),

    # CUT
    _readj("cut", {"Tense": "past"}, "cut", pos="V"),
    _readj("cut", {"VerbForm": "past_part"}, "cut", pos="V"),

    # RISE
    _readj("rise", {"Tense": "past"}, "rose", pos="V"),
    _readj("rise", {"VerbForm": "past_part"}, "risen", pos="V"),

    # DRIVE
    _readj("drive", {"Tense": "past"}, "drove", pos="V"),
    _readj("drive", {"VerbForm": "past_part"}, "driven", pos="V"),

    # BUY
    _readj("buy", {"Tense": "past"}, "bought", pos="V"),
    _readj("buy", {"VerbForm": "past_part"}, "bought", pos="V"),

    # WEAR
    _readj("wear", {"Tense": "past"}, "wore", pos="V"),
    _readj("wear", {"VerbForm": "past_part"}, "worn", pos="V"),

    # CHOOSE
    _readj("choose", {"Tense": "past"}, "chose", pos="V"),
    _readj("choose", {"VerbForm": "past_part"}, "chosen", pos="V"),

    # SING
    _readj("sing", {"Tense": "past"}, "sang", pos="V"),
    _readj("sing", {"VerbForm": "past_part"}, "sung", pos="V"),

    # WIN
    _readj("win", {"Tense": "past"}, "won", pos="V"),
    _readj("win", {"VerbForm": "past_part"}, "won", pos="V"),

    # CATCH
    _readj("catch", {"Tense": "past"}, "caught", pos="V"),
    _readj("catch", {"VerbForm": "past_part"}, "caught", pos="V"),

    # TEACH
    _readj("teach", {"Tense": "past"}, "taught", pos="V"),
    _readj("teach", {"VerbForm": "past_part"}, "taught", pos="V"),

    # FIGHT
    _readj("fight", {"Tense": "past"}, "fought", pos="V"),
    _readj("fight", {"VerbForm": "past_part"}, "fought", pos="V"),

    # THROW
    _readj("throw", {"Tense": "past"}, "threw", pos="V"),
    _readj("throw", {"VerbForm": "past_part"}, "thrown", pos="V"),

    # SLEEP
    _readj("sleep", {"Tense": "past"}, "slept", pos="V"),
    _readj("sleep", {"VerbForm": "past_part"}, "slept", pos="V"),

    # SELL
    _readj("sell", {"Tense": "past"}, "sold", pos="V"),
    _readj("sell", {"VerbForm": "past_part"}, "sold", pos="V"),

    # SWIM
    _readj("swim", {"Tense": "past"}, "swam", pos="V"),
    _readj("swim", {"VerbForm": "past_part"}, "swum", pos="V"),

    # FLY
    _readj("fly", {"Tense": "past"}, "flew", pos="V"),
    _readj("fly", {"VerbForm": "past_part"}, "flown", pos="V"),

    # DRINK
    _readj("drink", {"Tense": "past"}, "drank", pos="V"),
    _readj("drink", {"VerbForm": "past_part"}, "drunk", pos="V"),

    # FORGET
    _readj("forget", {"Tense": "past"}, "forgot", pos="V"),
    _readj("forget", {"VerbForm": "past_part"}, "forgotten", pos="V"),

    # HIDE
    _readj("hide", {"Tense": "past"}, "hid", pos="V"),
    _readj("hide", {"VerbForm": "past_part"}, "hidden", pos="V"),

    # RING
    _readj("ring", {"Tense": "past"}, "rang", pos="V"),
    _readj("ring", {"VerbForm": "past_part"}, "rung", pos="V"),

    # SHAKE
    _readj("shake", {"Tense": "past"}, "shook", pos="V"),
    _readj("shake", {"VerbForm": "past_part"}, "shaken", pos="V"),

    # BITE
    _readj("bite", {"Tense": "past"}, "bit", pos="V"),
    _readj("bite", {"VerbForm": "past_part"}, "bitten", pos="V"),

    # EAT
    _readj("eat", {"Tense": "past"}, "ate", pos="V"),
    _readj("eat", {"VerbForm": "past_part"}, "eaten", pos="V"),

    # TEAR
    _readj("tear", {"Tense": "past"}, "tore", pos="V"),
    _readj("tear", {"VerbForm": "past_part"}, "torn", pos="V"),

    # HANG
    _readj("hang", {"Tense": "past"}, "hung", pos="V"),
    _readj("hang", {"VerbForm": "past_part"}, "hung", pos="V"),

    # SEEK
    _readj("seek", {"Tense": "past"}, "sought", pos="V"),
    _readj("seek", {"VerbForm": "past_part"}, "sought", pos="V"),

    # BIND
    _readj("bind", {"Tense": "past"}, "bound", pos="V"),
    _readj("bind", {"VerbForm": "past_part"}, "bound", pos="V"),

    # FREEZE
    _readj("freeze", {"Tense": "past"}, "froze", pos="V"),
    _readj("freeze", {"VerbForm": "past_part"}, "frozen", pos="V"),

    # SHINE
    _readj("shine", {"Tense": "past"}, "shone", pos="V"),
    _readj("shine", {"VerbForm": "past_part"}, "shone", pos="V"),

    # DIG
    _readj("dig", {"Tense": "past"}, "dug", pos="V"),
    _readj("dig", {"VerbForm": "past_part"}, "dug", pos="V"),

    # WAKE
    _readj("wake", {"Tense": "past"}, "woke", pos="V"),
    _readj("wake", {"VerbForm": "past_part"}, "woken", pos="V"),

    # BLOW
    _readj("blow", {"Tense": "past"}, "blew", pos="V"),
    _readj("blow", {"VerbForm": "past_part"}, "blown", pos="V"),

    # STRIKE
    _readj("strike", {"Tense": "past"}, "struck", pos="V"),
    _readj("strike", {"VerbForm": "past_part"}, "struck", pos="V"),

    # STEAL
    _readj("steal", {"Tense": "past"}, "stole", pos="V"),
    _readj("steal", {"VerbForm": "past_part"}, "stolen", pos="V"),

    # SWEAR
    _readj("swear", {"Tense": "past"}, "swore", pos="V"),
    _readj("swear", {"VerbForm": "past_part"}, "sworn", pos="V"),

    # BEAR
    _readj("bear", {"Tense": "past"}, "bore", pos="V"),
    _readj("bear", {"VerbForm": "past_part"}, "borne", pos="V"),

    # WEAVE
    _readj("weave", {"Tense": "past"}, "wove", pos="V"),
    _readj("weave", {"VerbForm": "past_part"}, "woven", pos="V"),

    # CLING
    _readj("cling", {"Tense": "past"}, "clung", pos="V"),
    _readj("cling", {"VerbForm": "past_part"}, "clung", pos="V"),

    # SPIN
    _readj("spin", {"Tense": "past"}, "spun", pos="V"),
    _readj("spin", {"VerbForm": "past_part"}, "spun", pos="V"),

    # SPRING
    _readj("spring", {"Tense": "past"}, "sprang", pos="V"),
    _readj("spring", {"VerbForm": "past_part"}, "sprung", pos="V"),

    # STRIDE
    _readj("stride", {"Tense": "past"}, "strode", pos="V"),
    _readj("stride", {"VerbForm": "past_part"}, "stridden", pos="V"),

    # STINK
    _readj("stink", {"Tense": "past"}, "stank", pos="V"),
    _readj("stink", {"VerbForm": "past_part"}, "stunk", pos="V"),

    # SLAY
    _readj("slay", {"Tense": "past"}, "slew", pos="V"),
    _readj("slay", {"VerbForm": "past_part"}, "slain", pos="V"),

    # FORBID
    _readj("forbid", {"Tense": "past"}, "forbade", pos="V"),
    _readj("forbid", {"VerbForm": "past_part"}, "forbidden", pos="V"),

    # FORSAKE
    _readj("forsake", {"Tense": "past"}, "forsook", pos="V"),
    _readj("forsake", {"VerbForm": "past_part"}, "forsaken", pos="V"),

    # ── Irregular noun plurals (readjustment) ─────────────────────────────
    _readj("man", {"Number": "pl"}, "men", pos="N"),
    _readj("woman", {"Number": "pl"}, "women", pos="N"),
    _readj("child", {"Number": "pl"}, "children", pos="N"),
    _readj("foot", {"Number": "pl"}, "feet", pos="N"),
    _readj("tooth", {"Number": "pl"}, "teeth", pos="N"),
    _readj("goose", {"Number": "pl"}, "geese", pos="N"),
    _readj("mouse", {"Number": "pl"}, "mice", pos="N"),
    _readj("louse", {"Number": "pl"}, "lice", pos="N"),
    _readj("ox", {"Number": "pl"}, "oxen", pos="N"),
    _readj("person", {"Number": "pl"}, "people", pos="N"),
    _readj("die", {"Number": "pl"}, "dice", pos="N"),
    _readj("sheep", {"Number": "pl"}, "sheep", pos="N"),
    _readj("deer", {"Number": "pl"}, "deer", pos="N"),
    _readj("fish", {"Number": "pl"}, "fish", pos="N"),
    _readj("moose", {"Number": "pl"}, "moose", pos="N"),
    _readj("aircraft", {"Number": "pl"}, "aircraft", pos="N"),
    _readj("series", {"Number": "pl"}, "series", pos="N"),
    _readj("species", {"Number": "pl"}, "species", pos="N"),
    _readj("cactus", {"Number": "pl"}, "cacti", pos="N"),
    _readj("nucleus", {"Number": "pl"}, "nuclei", pos="N"),
    _readj("focus", {"Number": "pl"}, "foci", pos="N"),
    _readj("fungus", {"Number": "pl"}, "fungi", pos="N"),
    _readj("stimulus", {"Number": "pl"}, "stimuli", pos="N"),
    _readj("syllabus", {"Number": "pl"}, "syllabi", pos="N"),
    _readj("radius", {"Number": "pl"}, "radii", pos="N"),
    _readj("alumnus", {"Number": "pl"}, "alumni", pos="N"),
    _readj("criterion", {"Number": "pl"}, "criteria", pos="N"),
    _readj("phenomenon", {"Number": "pl"}, "phenomena", pos="N"),
    _readj("datum", {"Number": "pl"}, "data", pos="N"),
    _readj("medium", {"Number": "pl"}, "media", pos="N"),
    _readj("thesis", {"Number": "pl"}, "theses", pos="N"),
    _readj("crisis", {"Number": "pl"}, "crises", pos="N"),
    _readj("analysis", {"Number": "pl"}, "analyses", pos="N"),
    _readj("hypothesis", {"Number": "pl"}, "hypotheses", pos="N"),
    _readj("basis", {"Number": "pl"}, "bases", pos="N"),
    _readj("appendix", {"Number": "pl"}, "appendices", pos="N"),
    _readj("matrix", {"Number": "pl"}, "matrices", pos="N"),
    _readj("index", {"Number": "pl"}, "indices", pos="N"),
    _readj("vertex", {"Number": "pl"}, "vertices", pos="N"),
    _readj("formula", {"Number": "pl"}, "formulae", pos="N",
           grade=Grade.from_prob(0.90)),
    _readj("antenna", {"Number": "pl"}, "antennae", pos="N",
           grade=Grade.from_prob(0.85)),
    _readj("curriculum", {"Number": "pl"}, "curricula", pos="N"),
    _readj("memorandum", {"Number": "pl"}, "memoranda", pos="N"),
    _readj("stratum", {"Number": "pl"}, "strata", pos="N"),

    # ── Suppletive adjective comparatives/superlatives ───────────────────
    _readj("good", {"Degree": "comparative"}, "better", pos="A"),
    _readj("good", {"Degree": "superlative"}, "best", pos="A"),
    _readj("bad", {"Degree": "comparative"}, "worse", pos="A"),
    _readj("bad", {"Degree": "superlative"}, "worst", pos="A"),
    _readj("far", {"Degree": "comparative"}, "farther", pos="A"),
    _readj("far", {"Degree": "superlative"}, "farthest", pos="A"),
    _readj("much", {"Degree": "comparative"}, "more", pos="A"),
    _readj("much", {"Degree": "superlative"}, "most", pos="A"),
    _readj("little", {"Degree": "comparative"}, "less", pos="A"),
    _readj("little", {"Degree": "superlative"}, "least", pos="A"),
    _readj("old", {"Degree": "comparative"}, "older", pos="A",
           grade=Grade.from_prob(0.92)),
    _readj("old", {"Degree": "superlative"}, "oldest", pos="A",
           grade=Grade.from_prob(0.92)),
]


# ══════════════════════════════════════════════════════════════════════════
# English Impoverishment Rules
# ══════════════════════════════════════════════════════════════════════════

ENGLISH_IMPOVERISHMENTS: List[ImpoverishmentRule] = [
    # English past tense does not mark person/number (except 'be')
    # → delete Person and Number in the context of [Tense:past]
    # (Readjustment rules for 'be' override this before it applies)
    ImpoverishmentRule(
        target_features=["Person", "Number"],
        context_features=FeatureBundle({
            "Tense": Feature("Tense", "past", Grade.perfect(), True),
        }),
        grade=Grade.perfect(),
        description="English past tense neutralises person/number agreement",
    ),
]


# ══════════════════════════════════════════════════════════════════════════
# Combined English VI list
# ══════════════════════════════════════════════════════════════════════════

ENGLISH_VIS: List[VocabularyItem] = (
    _VERBAL_VIS + _NOMINAL_VIS + _ADJECTIVAL_VIS + _DERIVATIONAL_VIS
)
