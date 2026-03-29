from __future__ import annotations
"""Stanza-level organisation with Grade harmony.

A stanza is the natural unit above the line in formal poetry.  This module
provides stanza construction, rhyme-scheme enforcement, thematic progression
planning, and volta detection — all evaluated through the Grade semiring so
that every decision is composable.

Paper ref:
    §Poet — Stanza Structure; §Phon — Rhyme Grading; §Info — Thematic Arc.
"""

__all__ = [
    "StanzaForm",
    "StanzaSpec",
    "StanzaBuilder",
    "RhymeSchemeEnforcer",
    "ThematicProgressionPlanner",
    "VoltaDetector",
]

import math
import random
import re
import string
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Dict, Iterator, List, Optional, Set, Tuple

from gofai_chat.core.grade import Grade
from gofai_chat.generation.poetry.line_generator import (
    LineSpec,
    LineCandidate,
    LineGenerator,
    EndWordSelector,
    LexicalSubstitutor,
    SyntacticShaper,
    _phonetic_heuristic_grade,
    _semantic_fit_grade,
    _meter_fit_grade,
    _count_line_syllables,
    _noun,
    _verb,
    _adj,
    _place,
    _NOUNS,
    _VERBS,
    _ADJS,
)

try:
    import numpy as np
    _HAS_NUMPY = True
except ImportError:
    np = None  # type: ignore[assignment]
    _HAS_NUMPY = False

try:
    import nltk
    from nltk.tokenize import word_tokenize
    from nltk.corpus import wordnet as wn
    _HAS_NLTK = True
except ImportError:
    nltk = None  # type: ignore[assignment]
    _HAS_NLTK = False

try:
    from rapidfuzz import fuzz as rfuzz
    _HAS_RAPIDFUZZ = True
except ImportError:
    rfuzz = None  # type: ignore[assignment]
    _HAS_RAPIDFUZZ = False


# ---------------------------------------------------------------------------
# StanzaForm
# ---------------------------------------------------------------------------

class StanzaForm(Enum):
    """Named stanza forms.

    The ``value`` is the standard English name.  :attr:`line_count` returns
    the number of lines in the stanza.
    """
    COUPLET  = "couplet"
    TERCET   = "tercet"
    QUATRAIN = "quatrain"
    QUINTET  = "quintet"
    SESTET   = "sestet"
    SEPTET   = "septet"
    OCTAVE   = "octave"

    @property
    def line_count(self) -> int:
        """Number of lines in this stanza form."""
        return {
            StanzaForm.COUPLET:  2,
            StanzaForm.TERCET:   3,
            StanzaForm.QUATRAIN: 4,
            StanzaForm.QUINTET:  5,
            StanzaForm.SESTET:   6,
            StanzaForm.SEPTET:   7,
            StanzaForm.OCTAVE:   8,
        }[self]

    @property
    def default_scheme(self) -> str:
        """Default rhyme scheme for this stanza form."""
        return {
            StanzaForm.COUPLET:  "AA",
            StanzaForm.TERCET:   "ABA",
            StanzaForm.QUATRAIN: "ABAB",
            StanzaForm.QUINTET:  "AABBA",
            StanzaForm.SESTET:   "ABABCC",
            StanzaForm.SEPTET:   "ABABBCC",
            StanzaForm.OCTAVE:   "ABABABCC",
        }[self]

    @classmethod
    def from_line_count(cls, n: int) -> "StanzaForm":
        """Return the StanzaForm matching *n* lines (clamps to valid range)."""
        mapping = {
            2: cls.COUPLET,
            3: cls.TERCET,
            4: cls.QUATRAIN,
            5: cls.QUINTET,
            6: cls.SESTET,
            7: cls.SEPTET,
            8: cls.OCTAVE,
        }
        return mapping.get(n, cls.QUATRAIN)


# ---------------------------------------------------------------------------
# StanzaSpec
# ---------------------------------------------------------------------------

@dataclass
class StanzaSpec:
    """Specification for generating one stanza.

    Attributes
    ----------
    form:
        The stanza form (:class:`StanzaForm`).
    rhyme_scheme:
        Letter-scheme string, e.g. ``"ABAB"``.  If empty, uses the form's
        default.
    meter:
        Target meter name: 'iambic_pentameter', 'trochaic_tetrameter', etc.
    thematic_role:
        Role of this stanza in the poem's arc: 'exposition', 'development',
        'complication', 'climax', 'resolution', 'volta', 'coda'.
    topic:
        Thematic focus (passed through to :class:`LineSpec`).
    mood:
        Affective tone.
    syllable_count:
        Target syllable count per line.
    position:
        Stanza position in the poem (0-indexed).
    """
    form: StanzaForm = StanzaForm.QUATRAIN
    rhyme_scheme: str = ""
    meter: str = "iambic_pentameter"
    thematic_role: str = "development"
    topic: str = "poetry"
    mood: str = "neutral"
    syllable_count: int = 10
    position: int = 0

    def effective_scheme(self) -> str:
        """Return the rhyme scheme to use (explicit or form default)."""
        return self.rhyme_scheme if self.rhyme_scheme else self.form.default_scheme

    def line_count(self) -> int:
        """Return the number of lines required for this stanza."""
        return self.form.line_count


# ---------------------------------------------------------------------------
# StanzaBuilder
# ---------------------------------------------------------------------------

class StanzaBuilder:
    """Builds complete stanzas from a :class:`StanzaSpec`.

    Uses :class:`LineGenerator` for individual lines and
    :class:`RhymeSchemeEnforcer` to post-process the rhyme scheme.
    """

    def __init__(self) -> None:
        self._line_gen = LineGenerator()
        self._enforcer = RhymeSchemeEnforcer()
        self._end_word_selector = EndWordSelector()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def build_stanza(self, spec: StanzaSpec, context: Dict[str, Any]) -> List[str]:
        """Build and return the lines of one stanza.

        Parameters
        ----------
        spec:
            Stanza specification.
        context:
            Additional generation context (merged with *spec* fields).

        Returns
        -------
        List[str]
            Lines of the stanza, in order.
        """
        scheme = spec.effective_scheme()
        n_lines = spec.line_count()
        topic = context.get("topic", spec.topic)
        mood  = context.get("mood", spec.mood)

        # Pre-select end words for each rhyme class
        end_words = self._end_word_selector.select_end_words(scheme, topic)

        lines: List[str] = []
        for i, letter in enumerate(scheme[:n_lines]):
            rhyme_target = end_words.get(letter.upper(), "")
            line_spec = LineSpec(
                meter=spec.meter,
                rhyme_target=rhyme_target,
                semantic_target=topic,
                mood=mood,
                syllable_count=spec.syllable_count,
                position=i,
            )
            candidate = self._line_gen.generate_line(line_spec)
            lines.append(candidate.text)

        # Enforce the scheme post-hoc
        lines = self._enforcer.enforce_scheme(lines, scheme)
        return lines

    def stanza_harmony_grade(self, lines: List[str]) -> Grade:
        """Compute a Grade for the internal harmony of a stanza.

        Combines:
        * Mean phonetic grade across all lines.
        * Syllable-count consistency (variance across lines).
        * Semantic cohesion (pairwise rapidfuzz similarity).
        """
        if not lines:
            return Grade.impossible()
        non_empty = [l for l in lines if l.strip()]
        if not non_empty:
            return Grade.impossible()

        # Phonetic grades
        ph_grades = [_phonetic_heuristic_grade(l) for l in non_empty]
        mean_ph = Grade.mean(ph_grades)

        # Syllable consistency
        syllable_counts = [_count_line_syllables(l) for l in non_empty]
        mean_syl = sum(syllable_counts) / len(syllable_counts) if syllable_counts else 0
        variance = (
            sum((s - mean_syl) ** 2 for s in syllable_counts) / len(syllable_counts)
            if syllable_counts else 0
        )
        # Low variance → high consistency grade
        consistency = max(0.0, 1.0 - variance / max(mean_syl ** 2, 1.0))
        consistency_g = Grade.from_prob(0.4 + 0.6 * consistency)

        # Semantic cohesion
        if len(non_empty) >= 2 and _HAS_RAPIDFUZZ:
            sims: List[float] = []
            for i in range(len(non_empty)):
                for j in range(i + 1, len(non_empty)):
                    sims.append(rfuzz.token_set_ratio(non_empty[i], non_empty[j]) / 100.0)
            cohesion = sum(sims) / len(sims) if sims else 0.5
            cohesion_g = Grade.from_prob(0.3 + 0.7 * cohesion)
        else:
            cohesion_g = Grade.from_prob(0.6)

        return mean_ph * consistency_g * cohesion_g

    def inter_stanza_grade(self, stanza1: List[str], stanza2: List[str]) -> Grade:
        """Compute a Grade for the transition between two adjacent stanzas.

        A good transition has moderate semantic overlap (continuity without
        repetition) and different end-words to avoid monotony.
        """
        if not stanza1 or not stanza2:
            return Grade.from_prob(0.5)

        # Semantic continuity via rapidfuzz over last/first lines
        last_line = stanza1[-1]
        first_line = stanza2[0]
        if _HAS_RAPIDFUZZ:
            continuity = rfuzz.token_sort_ratio(last_line, first_line) / 100.0
        else:
            words1 = set(re.findall(r"[a-z]+", last_line.lower()))
            words2 = set(re.findall(r"[a-z]+", first_line.lower()))
            union = words1 | words2
            continuity = len(words1 & words2) / max(len(union), 1)

        # We want moderate continuity: 0.2–0.5 overlap is best
        if 0.15 <= continuity <= 0.55:
            continuity_g = Grade.from_prob(0.85)
        elif continuity < 0.15:
            continuity_g = Grade.from_prob(0.5)  # too abrupt
        else:
            continuity_g = Grade.from_prob(0.6)  # too repetitive

        # End-word variety: penalise if stanzas share too many words
        ends1 = {re.findall(r"[a-z]+", l.lower())[-1] for l in stanza1 if re.findall(r"[a-z]+", l.lower())}
        ends2 = {re.findall(r"[a-z]+", l.lower())[-1] for l in stanza2 if re.findall(r"[a-z]+", l.lower())}
        shared_ends = ends1 & ends2
        variety_g = Grade.from_prob(max(0.3, 1.0 - len(shared_ends) * 0.2))

        return continuity_g * variety_g


# ---------------------------------------------------------------------------
# RhymeSchemeEnforcer
# ---------------------------------------------------------------------------

class RhymeSchemeEnforcer:
    """Enforces a rhyme scheme on a list of lines.

    Uses :class:`EndWordSelector` to replace end-words that do not conform to
    the target scheme.
    """

    def __init__(self) -> None:
        self._selector = EndWordSelector()
        self._substitutor = LexicalSubstitutor()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def enforce_scheme(self, lines: List[str], scheme: str) -> List[str]:
        """Rewrite line end-words to satisfy *scheme*.

        For each unique letter in *scheme*, one representative end-word is
        chosen and all lines sharing that letter have their last word replaced.

        Parameters
        ----------
        lines:
            Lines to modify (length must equal ``len(scheme)``).
        scheme:
            Letter-scheme string.  Lines with the same letter must rhyme.

        Returns
        -------
        List[str]
            Modified lines.
        """
        if len(lines) != len(scheme):
            # Pad or trim scheme to match lines
            if len(lines) > len(scheme):
                scheme = scheme + scheme[-1] * (len(lines) - len(scheme))
            else:
                scheme = scheme[:len(lines)]

        # Build letter → representative end-word mapping
        letter_word: Dict[str, str] = {}
        for i, (line, letter) in enumerate(zip(lines, scheme)):
            if letter.upper() not in letter_word:
                end_word = self._last_word(line)
                if end_word:
                    letter_word[letter.upper()] = end_word

        # Replace end words to match scheme
        result: List[str] = []
        for i, (line, letter) in enumerate(zip(lines, scheme)):
            target_word = letter_word.get(letter.upper(), "")
            if not target_word:
                result.append(line)
                continue
            end_word = self._last_word(line)
            if end_word and end_word.lower() != target_word.lower():
                # Find a rhyme for the target word to use as replacement
                rhymes = self._selector.find_rhyming_words(target_word, n=8)
                if rhymes:
                    replacement = random.choice(rhymes)
                    line = self._replace_last_word(line, replacement)
            result.append(line)
        return result

    def scheme_grade(self, lines: List[str], scheme: str) -> Grade:
        """Measure how well *lines* conform to *scheme*.

        Returns a Grade: perfect → all rhymes correct; lower → violations.
        """
        if not lines or not scheme:
            return Grade.from_prob(0.5)
        n = min(len(lines), len(scheme))
        letter_end_words: Dict[str, List[str]] = defaultdict(list)
        for i in range(n):
            end = self._last_word(lines[i])
            if end:
                letter_end_words[scheme[i].upper()].append(end)

        grades: List[Grade] = []
        for letter, words in letter_end_words.items():
            if len(words) < 2:
                continue
            # Grade pairwise rhyme quality
            for j in range(len(words)):
                for k in range(j + 1, len(words)):
                    rg = self._selector.rhyme_grade(words[j], words[k])
                    grades.append(rg)

        if not grades:
            return Grade.from_prob(0.6)  # no pairs to evaluate
        return Grade.mean(grades)

    def detect_scheme(self, lines: List[str]) -> str:
        """Infer the rhyme scheme from *lines*.

        Returns a letter-scheme string (e.g. ``"ABAB"``).
        """
        if not lines:
            return ""
        end_words = [self._last_word(l) or "" for l in lines]
        # Cluster by rhyme group
        clusters: List[int] = [-1] * len(end_words)
        group_id = 0
        letter_map: Dict[int, str] = {}
        letters = list("ABCDEFGHIJKLMNOPQRSTUVWXYZ")

        for i, word in enumerate(end_words):
            if clusters[i] != -1:
                continue
            # Check if it rhymes with an already-assigned group
            matched = False
            for j in range(i):
                if clusters[j] != -1 and end_words[j] and word:
                    rg = self._selector.rhyme_grade(word, end_words[j])
                    if rg.exceeds(0.7):
                        clusters[i] = clusters[j]
                        matched = True
                        break
            if not matched:
                clusters[i] = group_id
                letter_map[group_id] = letters[min(group_id, len(letters) - 1)]
                group_id += 1

        scheme_letters = [letter_map.get(c, "X") for c in clusters]
        return "".join(scheme_letters)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _last_word(line: str) -> str:
        """Return the last alphabetic word of *line*, lowercase."""
        words = re.findall(r"[a-z]+", line.lower())
        return words[-1] if words else ""

    @staticmethod
    def _replace_last_word(line: str, replacement: str) -> str:
        """Replace the last word of *line* with *replacement*."""
        parts = line.rstrip().rsplit(None, 1)
        if len(parts) == 2:
            # Preserve trailing punctuation
            trailing = re.search(r"[.,;:!?]+$", line.rstrip())
            punct = trailing.group() if trailing else ""
            return parts[0] + " " + replacement + punct
        return replacement


# ---------------------------------------------------------------------------
# ThematicProgressionPlanner
# ---------------------------------------------------------------------------

# Thematic arc patterns for different poem types
_PROGRESSION_PATTERNS: Dict[str, List[str]] = {
    "lyric":      ["exposition", "development", "complication", "resolution"],
    "sonnet":     ["exposition", "development", "complication", "volta", "resolution"],
    "narrative":  ["exposition", "rising_action", "climax", "falling_action", "resolution"],
    "elegy":      ["lamentation", "memory", "acceptance", "consolation"],
    "ode":        ["invocation", "praise", "meditation", "return", "coda"],
    "ballad":     ["exposition", "development", "crisis", "resolution"],
    "free_verse": ["image", "development", "turn", "coda"],
    "default":    ["exposition", "development", "turn", "resolution"],
}

# What each thematic role is trying to do (drives word-choice in generation)
_ROLE_SEMANTICS: Dict[str, Dict[str, Any]] = {
    "exposition":    {"focus": "introduce",   "mood_shift":  0.0, "abstract": 0.2},
    "development":   {"focus": "expand",      "mood_shift":  0.1, "abstract": 0.3},
    "complication":  {"focus": "complicate",  "mood_shift":  0.2, "abstract": 0.4},
    "climax":        {"focus": "intensify",   "mood_shift":  0.4, "abstract": 0.3},
    "volta":         {"focus": "turn",        "mood_shift":  0.5, "abstract": 0.5},
    "resolution":    {"focus": "resolve",     "mood_shift": -0.2, "abstract": 0.3},
    "lamentation":   {"focus": "grieve",      "mood_shift":  0.3, "abstract": 0.2},
    "memory":        {"focus": "recall",      "mood_shift":  0.1, "abstract": 0.4},
    "acceptance":    {"focus": "accept",      "mood_shift": -0.3, "abstract": 0.5},
    "consolation":   {"focus": "comfort",     "mood_shift": -0.4, "abstract": 0.4},
    "invocation":    {"focus": "address",     "mood_shift":  0.0, "abstract": 0.3},
    "praise":        {"focus": "celebrate",   "mood_shift": -0.1, "abstract": 0.2},
    "meditation":    {"focus": "ponder",      "mood_shift":  0.1, "abstract": 0.6},
    "return":        {"focus": "return",      "mood_shift": -0.2, "abstract": 0.3},
    "coda":          {"focus": "conclude",    "mood_shift": -0.1, "abstract": 0.4},
    "rising_action": {"focus": "build",       "mood_shift":  0.2, "abstract": 0.2},
    "falling_action":{"focus": "wind_down",   "mood_shift": -0.2, "abstract": 0.3},
    "image":         {"focus": "image",       "mood_shift":  0.0, "abstract": 0.1},
    "turn":          {"focus": "turn",        "mood_shift":  0.4, "abstract": 0.5},
    "crisis":        {"focus": "crisis",      "mood_shift":  0.5, "abstract": 0.2},
    "development":   {"focus": "develop",     "mood_shift":  0.1, "abstract": 0.3},
}

# Subtopic progressions: how sub-themes evolve across a poem
_SUBTOPIC_CHAINS: Dict[str, List[str]] = {
    "nature":   ["landscape", "flora", "fauna", "weather", "season", "cycle"],
    "love":     ["attraction", "encounter", "devotion", "conflict", "separation", "longing"],
    "death":    ["presage", "dying", "loss", "grief", "memory", "acceptance"],
    "time":     ["moment", "duration", "change", "memory", "eternity", "return"],
    "sea":      ["voyage", "storm", "calm", "depth", "horizon", "shore"],
    "default":  ["image", "context", "conflict", "turn", "insight", "coda"],
}


class ThematicProgressionPlanner:
    """Plans thematic development across multiple stanzas.

    Given a topic and stanza count, produces a sequence of
    :class:`StanzaSpec` contexts that guide generation toward a coherent arc.
    """

    def plan_progression(
        self,
        topic: str,
        n_stanzas: int,
        form_type: str = "default",
        mood: str = "neutral",
        meter: str = "iambic_pentameter",
        stanza_form: StanzaForm = StanzaForm.QUATRAIN,
    ) -> List[Dict[str, Any]]:
        """Return a list of *n_stanzas* context dicts guiding poem generation.

        Each dict has keys:
        * ``thematic_role``: str
        * ``subtopic``: str
        * ``mood``: str (may shift from stanza to stanza)
        * ``focus_words``: List[str]
        * ``position``: int

        Parameters
        ----------
        topic:
            Overarching theme.
        n_stanzas:
            Number of stanzas planned.
        form_type:
            Arc pattern key (lyric, sonnet, elegy, ode, narrative, etc.).
        mood:
            Starting mood.
        meter:
            Default meter for all stanzas.
        stanza_form:
            Stanza form for all stanzas.
        """
        arc = _PROGRESSION_PATTERNS.get(form_type, _PROGRESSION_PATTERNS["default"])
        subtopics = _SUBTOPIC_CHAINS.get(topic, _SUBTOPIC_CHAINS["default"])

        # Use NarrativeAnalyzer arc when the built-in subtopic chain is generic.
        if topic not in _SUBTOPIC_CHAINS:
            narrative_subtopics = self._get_narrative_arc(topic, n_stanzas)
            if narrative_subtopics and len(narrative_subtopics) == n_stanzas:
                subtopics = narrative_subtopics

        # Interpolate arc and subtopics to n_stanzas
        arc_interp = _interpolate_list(arc, n_stanzas)
        sub_interp = _interpolate_list(subtopics, n_stanzas)

        moods = self._mood_arc(mood, n_stanzas, arc_interp)
        result: List[Dict[str, Any]] = []
        for i in range(n_stanzas):
            role = arc_interp[i]
            sem = _ROLE_SEMANTICS.get(role, {"focus": "develop", "abstract": 0.3})
            context: Dict[str, Any] = {
                "thematic_role": role,
                "subtopic": sub_interp[i],
                "topic": topic,
                "mood": moods[i],
                "focus": sem["focus"],
                "abstract": sem["abstract"],
                "focus_words": self._role_focus_words(role, topic, moods[i]),
                "position": i,
                "meter": meter,
                "stanza_form": stanza_form,
            }
            result.append(context)
        return result

    def build_stanza_specs(
        self,
        topic: str,
        n_stanzas: int,
        form_type: str = "default",
        mood: str = "neutral",
        meter: str = "iambic_pentameter",
        stanza_form: StanzaForm = StanzaForm.QUATRAIN,
    ) -> List[StanzaSpec]:
        """Return a list of :class:`StanzaSpec` objects for *n_stanzas*."""
        contexts = self.plan_progression(
            topic, n_stanzas, form_type, mood, meter, stanza_form
        )
        specs: List[StanzaSpec] = []
        for ctx in contexts:
            specs.append(StanzaSpec(
                form=ctx.get("stanza_form", stanza_form),
                meter=ctx.get("meter", meter),
                thematic_role=ctx["thematic_role"],
                topic=ctx["topic"],
                mood=ctx["mood"],
                position=ctx["position"],
            ))
        return specs

    def progression_grade(self, stanzas: List[List[str]]) -> Grade:
        """Measure how well the stanza sequence forms a coherent progression.

        Combines:
        * Monotonic complexity increase (longer lines over time).
        * Vocabulary variety across stanzas (no stanza repeats another).
        * Semantic continuity between adjacent stanzas.
        """
        if len(stanzas) < 2:
            return Grade.from_prob(0.7)

        # Syllable arc: should be roughly monotone (or non-decreasing then decreasing)
        avg_syllables = [
            sum(_count_line_syllables(l) for l in s if l.strip()) / max(len(s), 1)
            for s in stanzas
        ]
        if _HAS_NUMPY:
            arc = np.array(avg_syllables)
            # Score monotone-ish arc: low variance in first-difference
            diffs = np.diff(arc)
            arc_score = max(0.3, 1.0 - float(np.std(diffs)) * 0.1)
        else:
            diffs = [avg_syllables[i + 1] - avg_syllables[i] for i in range(len(avg_syllables) - 1)]
            mean_diff = sum(diffs) / max(len(diffs), 1)
            variance = sum((d - mean_diff) ** 2 for d in diffs) / max(len(diffs), 1)
            arc_score = max(0.3, 1.0 - math.sqrt(variance) * 0.1)
        arc_g = Grade.from_prob(arc_score)

        # Vocabulary variety
        word_sets = [
            set(re.findall(r"[a-z]+", " ".join(s).lower()))
            for s in stanzas
        ]
        overlaps: List[float] = []
        for i in range(len(word_sets) - 1):
            union = word_sets[i] | word_sets[i + 1]
            inter = word_sets[i] & word_sets[i + 1]
            jaccard = len(inter) / max(len(union), 1)
            overlaps.append(jaccard)
        avg_overlap = sum(overlaps) / max(len(overlaps), 1)
        # 0.2–0.4 overlap is ideal: connected but varied
        if 0.15 <= avg_overlap <= 0.45:
            variety_g = Grade.from_prob(0.88)
        elif avg_overlap < 0.15:
            variety_g = Grade.from_prob(0.55)  # too disconnected
        else:
            variety_g = Grade.from_prob(0.65)  # too repetitive

        return arc_g * variety_g

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _get_narrative_arc(self, topic: str, n_stanzas: int) -> List[str]:
        """Return subtopics per stanza following an emotion-driven narrative arc.

        Uses EmotionAnalyzer.genre_arc_template to map each story beat to a
        dominant-emotion label, which serves as a per-stanza subtopic modifier.

        Args:
            topic: Overarching theme.
            n_stanzas: Number of stanzas.

        Returns:
            List of *n_stanzas* subtopic strings, or empty list on failure.
        """
        try:
            from gofai_chat.lexicon.emotion import EmotionAnalyzer
            ea = EmotionAnalyzer()
            topic_lower = topic.lower()
            genre = (
                'elegy' if any(w in topic_lower for w in ('loss', 'death', 'autumn', 'grief'))
                else 'romance'
            )
            arc = ea.genre_arc_template(genre)
            if arc and len(arc) >= n_stanzas:
                result: List[str] = []
                for beat in arc[:n_stanzas]:
                    dominant = ea.dominant_emotion(beat)
                    result.append(str(dominant) if dominant else topic)
                return result
        except Exception:
            pass
        # Fallback simple arc
        if n_stanzas == 3:
            return [topic, f"{topic} transformed", f"{topic} resolved"]
        if n_stanzas == 4:
            return [topic, f"{topic} deepened", f"{topic} transformed", f"{topic} resolved"]
        return [topic] * n_stanzas

    def _mood_arc(
        self, start_mood: str, n: int, arc: List[str]
    ) -> List[str]:
        """Compute per-stanza moods following the thematic arc.

        Roles like 'volta' and 'complication' trigger mood shifts;
        'resolution' and 'coda' soften back toward a stable mood.
        """
        moods = [start_mood]
        current = start_mood
        for i in range(1, n):
            role = arc[i] if i < len(arc) else "development"
            sem = _ROLE_SEMANTICS.get(role, {})
            shift = sem.get("mood_shift", 0.0)
            if abs(shift) > 0.3:
                # Significant shift: use contrastive mood
                current = self._shifted_mood(current, shift)
            moods.append(current)
        return moods

    @staticmethod
    def _shifted_mood(mood: str, shift: float) -> str:
        """Return a mood appropriate for a shift of magnitude *shift*."""
        positive_moods = ["joyful", "hopeful", "romantic", "serene", "contemplative"]
        negative_moods = ["melancholic", "elegiac", "somber", "angry", "anguished"]
        neutral_moods  = ["neutral", "contemplative", "mysterious"]
        if shift > 0:
            # Shift toward more intense / darker
            if mood in positive_moods:
                return random.choice(neutral_moods)
            if mood in neutral_moods:
                return random.choice(negative_moods)
            return mood
        else:
            # Shift toward lighter / calmer
            if mood in negative_moods:
                return random.choice(neutral_moods)
            if mood in neutral_moods:
                return random.choice(positive_moods)
            return mood

    @staticmethod
    def _role_focus_words(role: str, topic: str, mood: str) -> List[str]:
        """Generate a small list of focus words for a given role/topic/mood."""
        topic_ns = _NOUNS.get(topic, _NOUNS["default"])
        mood_vs = _VERBS.get(mood, _VERBS["neutral"])
        mood_as = _ADJS.get(mood, _ADJS["neutral"])
        base = random.sample(topic_ns, min(2, len(topic_ns)))
        base += random.sample(mood_vs, min(1, len(mood_vs)))
        base += random.sample(mood_as, min(1, len(mood_as)))
        role_extras: Dict[str, List[str]] = {
            "volta":       ["but", "yet", "however", "still", "nevertheless"],
            "climax":      ["suddenly", "at last", "now", "finally"],
            "resolution":  ["peace", "rest", "home", "quiet", "end"],
            "lamentation": ["grief", "loss", "absence", "weep", "mourn"],
            "consolation": ["comfort", "solace", "balm", "ease"],
            "meditation":  ["wonder", "contemplate", "ask", "ponder"],
        }
        base += role_extras.get(role, [])[:2]
        return base


def _interpolate_list(lst: List[str], n: int) -> List[str]:
    """Stretch or compress *lst* to length *n* by repeating or dropping entries."""
    if not lst:
        return ["development"] * n
    if len(lst) == n:
        return lst
    if len(lst) > n:
        # Sub-sample
        indices = [int(i * (len(lst) - 1) / max(n - 1, 1)) for i in range(n)]
        return [lst[i] for i in indices]
    # Repeat: tile and trim
    result: List[str] = []
    for i in range(n):
        result.append(lst[i % len(lst)])
    return result


# ---------------------------------------------------------------------------
# VoltaDetector
# ---------------------------------------------------------------------------

# Volta marker words / phrases
_VOLTA_MARKERS: List[re.Pattern] = [
    re.compile(r"\b(but|yet|however|nevertheless|still|though|although)\b", re.I),
    re.compile(r"\b(turn(s|ed)?|shift(s|ed)?|change(s|d)?)\b", re.I),
    re.compile(r"\band\s+yet\b", re.I),
    re.compile(r"\bbut\s+(now|then|still)\b", re.I),
    re.compile(r"\bno[,.]?\s+(wait|hold|stop)\b", re.I),
    re.compile(r"\buntil\b", re.I),
    re.compile(r"\band\s+so\b", re.I),
    re.compile(r"--|\u2014", re.I),   # em-dash often signals volta
    re.compile(r"\bwait\b", re.I),
    re.compile(r"\bnot\s+so\b", re.I),
]

# High-level semantic contrast for volta detection
_POSITIVE_WORDS = {
    "light", "hope", "love", "bright", "joy", "bloom", "warm", "rise",
    "smile", "song", "peace", "free", "open", "dance", "glad", "spring",
}
_NEGATIVE_WORDS = {
    "dark", "grief", "loss", "cold", "fall", "fade", "mourn", "sorrow",
    "pain", "death", "fear", "alone", "silence", "end", "shadow", "cry",
}


def _sentiment_score(line: str) -> float:
    """Simple sentiment: fraction of positive minus fraction of negative words."""
    words = set(re.findall(r"[a-z]+", line.lower()))
    pos = len(words & _POSITIVE_WORDS)
    neg = len(words & _NEGATIVE_WORDS)
    total = max(pos + neg, 1)
    return (pos - neg) / total


class VoltaDetector:
    """Detects and generates the volta — the turn in a sonnet or lyric poem.

    The volta is the moment of reversal or surprise that divides the poem's
    two movements.  In a Shakespearean sonnet it typically occurs at line 9
    (or at the couplet); in a Petrarchan sonnet at line 9.
    """

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def detect_volta(self, lines: List[str]) -> int:
        """Return the 0-indexed line number of the strongest volta candidate.

        Searches for:
        1. Explicit volta markers (conjunctions, em-dashes).
        2. Sentiment reversal between adjacent lines.
        3. Lexical pivot: introduction of a new topic cluster.

        Returns ``-1`` if no volta is detected.
        """
        if len(lines) < 2:
            return -1

        scores: List[Tuple[float, int]] = []
        prev_sentiment = _sentiment_score(lines[0])

        for i in range(1, len(lines)):
            line = lines[i]
            score = 0.0

            # Explicit markers
            for pat in _VOLTA_MARKERS:
                if pat.search(line):
                    score += 1.5
                    break

            # Sentiment reversal
            curr_sentiment = _sentiment_score(line)
            if prev_sentiment * curr_sentiment < 0:  # sign change
                score += 1.0 + abs(curr_sentiment - prev_sentiment)
            prev_sentiment = curr_sentiment

            # Lexical pivot: check for new high-frequency words not in prior lines
            prior_words = set(re.findall(r"[a-z]+", " ".join(lines[:i]).lower()))
            curr_words = set(re.findall(r"[a-z]+", line.lower()))
            new_words = curr_words - prior_words
            if len(new_words) >= 3:
                score += 0.5

            scores.append((score, i))

        if not scores:
            return -1
        best_score, best_idx = max(scores, key=lambda x: x[0])
        if best_score < 0.5:
            return -1  # no strong volta signal
        return best_idx

    def volta_grade(self, lines: List[str]) -> Grade:
        """Grade how effectively a volta is realised in *lines*.

        A strong, unambiguous volta → near-perfect grade.
        No volta detected → low grade (for volta-requiring forms like sonnets).
        """
        idx = self.detect_volta(lines)
        if idx < 0:
            return Grade.from_prob(0.2)

        # Check how strong the marker is
        line = lines[idx]
        marker_strength = 0.0
        for pat in _VOLTA_MARKERS:
            if pat.search(line):
                marker_strength = 0.7
                break

        # Check sentiment reversal magnitude
        if idx > 0:
            before_sent = sum(_sentiment_score(l) for l in lines[:idx]) / idx
            after_sent = sum(_sentiment_score(l) for l in lines[idx:]) / max(len(lines) - idx, 1)
            reversal = abs(before_sent - after_sent)
        else:
            reversal = 0.0

        score = min(0.95, 0.3 + marker_strength + reversal * 0.4)
        return Grade.from_prob(score)

    def generate_volta_line(
        self,
        before_lines: List[str],
        topic: str = "default",
        mood: str = "neutral",
    ) -> str:
        """Generate a volta line that turns the poem after *before_lines*.

        The volta line:
        1. Starts with a contrastive conjunction or em-dash.
        2. Introduces new semantic content (shift in topic/mood).
        3. Is metrically compatible with the preceding lines.
        """
        # Detect prevailing sentiment and produce contrast
        prev_sentiment = (
            sum(_sentiment_score(l) for l in before_lines) / max(len(before_lines), 1)
            if before_lines else 0.0
        )
        # Contrastive mood: flip the sign
        if prev_sentiment >= 0.1:
            new_mood = "melancholic"
        elif prev_sentiment <= -0.1:
            new_mood = "hopeful"
        else:
            new_mood = "contemplative"

        # Generate a new line in the contrastive mood
        shaper = SyntacticShaper()
        gen = LineGenerator()
        avg_syllables = (
            int(sum(_count_line_syllables(l) for l in before_lines) / max(len(before_lines), 1))
            if before_lines else 10
        )
        spec = LineSpec(
            meter="iambic_pentameter",
            semantic_target=topic,
            mood=new_mood,
            syllable_count=avg_syllables,
        )
        cand = gen.generate_line(spec)
        base_line = cand.text

        # Prepend a volta marker
        markers = [
            "But ", "Yet ", "And yet — ", "Still, ", "Though — ",
            "—but ", "—yet ", "Until ", "Now ",
        ]
        marker = random.choice(markers)
        volta_line = marker + base_line[0].lower() + base_line[1:] if len(base_line) > 1 else marker + base_line

        return volta_line

    def volta_position_grade(self, n_lines: int, volta_idx: int) -> Grade:
        """Grade whether *volta_idx* is at the expected position for a *n_lines* poem.

        Expected positions (0-indexed):
        * 14-line sonnet → line 8 (after the octave) or line 12 (couplet)
        * 8-line poem → line 4
        * other → middle third
        """
        if n_lines == 14:
            expected = {8, 12}
        elif n_lines == 8:
            expected = {4}
        else:
            lo = n_lines // 3
            hi = (2 * n_lines) // 3
            expected = set(range(lo, hi + 1))

        if volta_idx in expected:
            return Grade.from_prob(0.95)
        # Penalise proportionally to distance from expected range
        closest = min(abs(volta_idx - e) for e in expected)
        penalty = closest / max(n_lines, 1)
        return Grade.from_prob(max(0.2, 1.0 - penalty * 2))


# ---------------------------------------------------------------------------
# High-level poem assembly
# ---------------------------------------------------------------------------

class PoemAssembler:
    """Assembles a full multi-stanza poem from a progression plan.

    Combines :class:`ThematicProgressionPlanner`, :class:`StanzaBuilder`, and
    :class:`VoltaDetector` into a single high-level interface.
    """

    def __init__(self) -> None:
        self._planner = ThematicProgressionPlanner()
        self._stanza_builder = StanzaBuilder()
        self._volta_detector = VoltaDetector()

    def assemble_poem(
        self,
        topic: str,
        n_stanzas: int = 4,
        form_type: str = "default",
        mood: str = "neutral",
        meter: str = "iambic_pentameter",
        stanza_form: StanzaForm = StanzaForm.QUATRAIN,
        with_volta: bool = False,
    ) -> Tuple[List[List[str]], Grade]:
        """Assemble a complete poem as a list of stanzas.

        Parameters
        ----------
        topic:
            Thematic focus.
        n_stanzas:
            Number of stanzas.
        form_type:
            Arc pattern (lyric, sonnet, elegy, …).
        mood:
            Starting mood.
        meter:
            Target meter.
        stanza_form:
            Stanza form for all stanzas.
        with_volta:
            If True, force-insert a volta line at the expected position.

        Returns
        -------
        Tuple[List[List[str]], Grade]
            (stanzas, overall_grade).
        """
        contexts = self._planner.plan_progression(
            topic, n_stanzas, form_type, mood, meter, stanza_form
        )
        all_stanzas: List[List[str]] = []
        stanza_grades: List[Grade] = []

        for ctx in contexts:
            spec = StanzaSpec(
                form=ctx.get("stanza_form", stanza_form),
                meter=ctx.get("meter", meter),
                thematic_role=ctx["thematic_role"],
                topic=ctx["topic"],
                mood=ctx["mood"],
                position=ctx["position"],
            )
            stanza_lines = self._stanza_builder.build_stanza(spec, ctx)

            # Inject volta if required
            if with_volta and ctx["thematic_role"] == "volta":
                volta = self._volta_detector.generate_volta_line(
                    [l for s in all_stanzas for l in s],
                    topic=topic,
                    mood=ctx["mood"],
                )
                stanza_lines = [volta] + stanza_lines[1:]

            all_stanzas.append(stanza_lines)
            sg = self._stanza_builder.stanza_harmony_grade(stanza_lines)
            stanza_grades.append(sg)

        # Add inter-stanza grades
        inter_grades: List[Grade] = []
        for i in range(len(all_stanzas) - 1):
            ig = self._stanza_builder.inter_stanza_grade(all_stanzas[i], all_stanzas[i + 1])
            inter_grades.append(ig)

        # Progression grade
        prog_g = self._planner.progression_grade(all_stanzas)

        # Overall: mean of stanza grades * mean of inter-stanza grades * progression
        overall = Grade.mean(stanza_grades)
        if inter_grades:
            overall = overall * Grade.mean(inter_grades)
        overall = overall * prog_g

        return all_stanzas, overall

    def format_poem(self, stanzas: List[List[str]], title: str = "") -> str:
        """Format stanzas as a single string with blank lines between stanzas."""
        parts: List[str] = []
        if title:
            parts.append(title)
            parts.append("")
        for stanza in stanzas:
            parts.extend(stanza)
            parts.append("")
        return "\n".join(parts).rstrip()


# ---------------------------------------------------------------------------
# Module-level convenience functions
# ---------------------------------------------------------------------------

def build_quatrain(
    topic: str = "nature",
    mood: str = "neutral",
    scheme: str = "ABAB",
    meter: str = "iambic_pentameter",
) -> Tuple[List[str], Grade]:
    """Build a single quatrain and return (lines, grade)."""
    spec = StanzaSpec(
        form=StanzaForm.QUATRAIN,
        rhyme_scheme=scheme,
        meter=meter,
        topic=topic,
        mood=mood,
        thematic_role="development",
    )
    builder = StanzaBuilder()
    lines = builder.build_stanza(spec, {"topic": topic, "mood": mood})
    grade = builder.stanza_harmony_grade(lines)
    return lines, grade


def build_sonnet(
    topic: str = "love",
    mood: str = "romantic",
    meter: str = "iambic_pentameter",
) -> Tuple[List[List[str]], Grade]:
    """Build a Shakespearean sonnet (3 quatrains + couplet) and return (stanzas, grade)."""
    assembler = PoemAssembler()
    # 3 quatrains + 1 couplet = 4 stanza-objects
    contexts = [
        StanzaSpec(form=StanzaForm.QUATRAIN, rhyme_scheme="ABAB", meter=meter,
                   topic=topic, mood=mood, thematic_role="exposition",   position=0),
        StanzaSpec(form=StanzaForm.QUATRAIN, rhyme_scheme="CDCD", meter=meter,
                   topic=topic, mood=mood, thematic_role="development",  position=1),
        StanzaSpec(form=StanzaForm.QUATRAIN, rhyme_scheme="EFEF", meter=meter,
                   topic=topic, mood=mood, thematic_role="complication", position=2),
        StanzaSpec(form=StanzaForm.COUPLET,  rhyme_scheme="GG",   meter=meter,
                   topic=topic, mood=mood, thematic_role="resolution",  position=3),
    ]
    builder = StanzaBuilder()
    stanzas: List[List[str]] = []
    grades: List[Grade] = []
    for spec in contexts:
        lines = builder.build_stanza(spec, {"topic": topic, "mood": mood})
        stanzas.append(lines)
        grades.append(builder.stanza_harmony_grade(lines))

    # Inject volta at line 9 (stanza 3, first line)
    if len(stanzas) >= 3 and stanzas[2]:
        all_prior = [l for s in stanzas[:2] for l in s]
        detector = VoltaDetector()
        volta_line = detector.generate_volta_line(all_prior, topic=topic, mood=mood)
        stanzas[2][0] = volta_line

    overall = Grade.mean(grades)
    return stanzas, overall


# ---------------------------------------------------------------------------
# SonnetBuilder
# ---------------------------------------------------------------------------

class SonnetBuilder:
    """Specialised builder for sonnet forms.

    Supports Shakespearean (ABABCDCDEFEFGG) and Petrarchan (ABBAABBACDECDE)
    sonnets.  Automatically places the volta at the correct position and
    grades the result.
    """

    SHAKESPEAREAN_SCHEME = "ABABCDCDEFEFGG"
    PETRARCHAN_SCHEME    = "ABBAABBACDECDE"

    def __init__(self) -> None:
        self._stanza_builder = StanzaBuilder()
        self._enforcer = RhymeSchemeEnforcer()
        self._volta_detector = VoltaDetector()
        self._planner = ThematicProgressionPlanner()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def build_shakespearean(
        self,
        topic: str = "love",
        mood: str = "romantic",
        meter: str = "iambic_pentameter",
    ) -> Tuple[str, Grade]:
        """Build a 14-line Shakespearean sonnet and return (poem_text, grade)."""
        specs = [
            StanzaSpec(form=StanzaForm.QUATRAIN, rhyme_scheme="ABAB", meter=meter,
                       topic=topic, mood=mood, thematic_role="exposition",   position=0),
            StanzaSpec(form=StanzaForm.QUATRAIN, rhyme_scheme="CDCD", meter=meter,
                       topic=topic, mood=mood, thematic_role="development",  position=1),
            StanzaSpec(form=StanzaForm.QUATRAIN, rhyme_scheme="EFEF", meter=meter,
                       topic=topic, mood=mood, thematic_role="complication", position=2),
            StanzaSpec(form=StanzaForm.COUPLET,  rhyme_scheme="GG",   meter=meter,
                       topic=topic, mood=mood, thematic_role="resolution",   position=3),
        ]
        return self._build_from_specs(specs, topic, mood, volta_stanza=2)

    def build_petrarchan(
        self,
        topic: str = "love",
        mood: str = "romantic",
        meter: str = "iambic_pentameter",
    ) -> Tuple[str, Grade]:
        """Build a 14-line Petrarchan sonnet and return (poem_text, grade)."""
        octave_specs = [
            StanzaSpec(form=StanzaForm.QUATRAIN, rhyme_scheme="ABBA", meter=meter,
                       topic=topic, mood=mood, thematic_role="exposition",  position=0),
            StanzaSpec(form=StanzaForm.QUATRAIN, rhyme_scheme="ABBA", meter=meter,
                       topic=topic, mood=mood, thematic_role="development", position=1),
        ]
        sestet_specs = [
            StanzaSpec(form=StanzaForm.TERCET, rhyme_scheme="CDE", meter=meter,
                       topic=topic, mood=mood, thematic_role="volta",      position=2),
            StanzaSpec(form=StanzaForm.TERCET, rhyme_scheme="CDE", meter=meter,
                       topic=topic, mood=mood, thematic_role="resolution", position=3),
        ]
        return self._build_from_specs(octave_specs + sestet_specs, topic, mood, volta_stanza=2)

    def grade_sonnet(self, poem_text: str) -> Grade:
        """Grade a 14-line poem as a sonnet.

        Checks: line count (14), volta presence, rhyme scheme compliance.
        """
        lines = [l for l in poem_text.splitlines() if l.strip()]
        if len(lines) != 14:
            # Penalise wrong line count
            penalty = abs(len(lines) - 14) * 0.05
            line_count_g = Grade.from_prob(max(0.1, 1.0 - penalty))
        else:
            line_count_g = Grade.perfect()

        volta_g = self._volta_detector.volta_grade(lines)
        volta_pos_g = self._volta_detector.volta_position_grade(len(lines),
                          self._volta_detector.detect_volta(lines))

        # Rhyme scheme: check lines against Shakespearean scheme
        scheme = self.SHAKESPEAREAN_SCHEME[:len(lines)]
        rhyme_g = self._enforcer.scheme_grade(lines, scheme)

        return line_count_g * volta_g * volta_pos_g * rhyme_g

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _build_from_specs(
        self,
        specs: List[StanzaSpec],
        topic: str,
        mood: str,
        volta_stanza: int = 2,
    ) -> Tuple[str, Grade]:
        stanzas: List[List[str]] = []
        grades: List[Grade] = []
        all_prior_lines: List[str] = []

        for i, spec in enumerate(specs):
            ctx = {"topic": topic, "mood": spec.mood}
            lines = self._stanza_builder.build_stanza(spec, ctx)

            # Inject volta
            if i == volta_stanza and lines:
                volta_line = self._volta_detector.generate_volta_line(
                    all_prior_lines, topic=topic, mood=mood
                )
                lines[0] = volta_line

            stanzas.append(lines)
            all_prior_lines.extend(lines)
            grades.append(self._stanza_builder.stanza_harmony_grade(lines))

        # Format poem
        parts: List[str] = []
        for i, stanza in enumerate(stanzas):
            parts.extend(stanza)
            if i < len(stanzas) - 1:
                parts.append("")

        poem_text = "\n".join(parts)
        overall_grade = Grade.mean(grades)
        return poem_text, overall_grade


# ---------------------------------------------------------------------------
# HaikuBuilder
# ---------------------------------------------------------------------------

class HaikuBuilder:
    """Specialised builder for haiku (5-7-5 syllable structure).

    Generates three lines with strict syllable targets and optional kigo
    (seasonal reference word).
    """

    _KIGO: Dict[str, List[str]] = {
        "spring": ["blossom", "petal", "mist", "sparrow", "brook", "thaw"],
        "summer": ["cicada", "swelter", "thunder", "sunlight", "noon"],
        "autumn": ["crimson", "harvest", "fallen", "scarecrow", "dusk"],
        "winter": ["snowfall", "frozen", "bare branch", "silence", "frost"],
    }

    def build(
        self,
        topic: str = "nature",
        mood: str = "neutral",
        season: Optional[str] = None,
    ) -> Tuple[str, Grade]:
        """Build a haiku and return (poem_text, grade)."""
        from gofai_chat.generation.poetry.line_generator import (
            LineSpec, LineGenerator, _count_line_syllables,
        )
        gen = LineGenerator()
        targets = [5, 7, 5]
        lines: List[str] = []
        for i, target in enumerate(targets):
            spec = LineSpec(
                meter="haiku",
                semantic_target=topic,
                mood=mood,
                syllable_count=target,
                position=i,
            )
            candidates = gen.generate_candidates(spec, n=20)
            # Filter by syllable count proximity
            scored = [
                (abs(_count_line_syllables(c.text) - target), c)
                for c in candidates
            ]
            scored.sort(key=lambda x: x[0])
            best_cand = scored[0][1] if scored else gen._fallback(spec)
            lines.append(best_cand.text)

        # Optionally insert kigo
        if season and season in self._KIGO:
            kigo = random.choice(self._KIGO[season])
            if kigo.lower() not in lines[1].lower():
                words = lines[1].split()
                if words:
                    idx = random.randint(0, len(words))
                    words.insert(idx, kigo)
                    lines[1] = " ".join(words)

        poem_text = "\n".join(lines)
        builder = StanzaBuilder()
        grade = builder.stanza_harmony_grade(lines)
        return poem_text, grade

    def grade_haiku(self, poem_text: str) -> Grade:
        """Grade a haiku for 5-7-5 compliance and phonetic quality."""
        from gofai_chat.generation.poetry.line_generator import _count_line_syllables
        lines = [l for l in poem_text.splitlines() if l.strip()]
        if len(lines) != 3:
            return Grade.from_prob(0.2)
        targets = [5, 7, 5]
        syllable_grades: List[Grade] = []
        for line, target in zip(lines, targets):
            actual = _count_line_syllables(line)
            deviation = abs(actual - target) / max(target, 1)
            syllable_grades.append(Grade.from_prob(max(0.2, 1.0 - deviation)))
        phonetic_grades = [_phonetic_heuristic_grade(l) for l in lines]
        return Grade.mean(syllable_grades) * Grade.mean(phonetic_grades)


# ---------------------------------------------------------------------------
# Export additions
# ---------------------------------------------------------------------------

__all__ += [
    "PoemAssembler",
    "SonnetBuilder",
    "HaikuBuilder",
    "build_quatrain",
    "build_sonnet",
]
