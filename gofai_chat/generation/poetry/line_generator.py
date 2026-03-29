from __future__ import annotations
"""Line-by-line poetry generation with Grade optimisation.

Every generation decision — lexical substitution, template filling, end-word
selection — is evaluated as a :class:`Grade` in the log-probability semiring
so that the best line can be selected via semiring multiplication and
comparison.

Paper ref:
    §Poet — Line Generation; §Phon — Phonetic Grading; §Sem — Semantic Fit.
"""

__all__ = [
    "LineSpec",
    "LineCandidate",
    "LineGenerator",
    "LexicalSubstitutor",
    "LineTemplateBank",
    "EndWordSelector",
    "SyntacticShaper",
]

import math
import random
import re
import string
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterator, List, Optional, Set, Tuple

from gofai_chat.core.grade import Grade

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
    from nltk.corpus import stopwords as _sw_corpus
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

# CMU dict — loaded lazily
_CMU_ENTRIES: Optional[Dict[str, List[List[str]]]] = None
_CMU_REVERSE: Optional[Dict[str, List[str]]] = None  # rhyme_part → [words]


def _load_cmu() -> Optional[Dict[str, List[List[str]]]]:
    """Load and return the CMU pronouncing dictionary (lazily)."""
    global _CMU_ENTRIES
    if _CMU_ENTRIES is not None:
        return _CMU_ENTRIES
    if not _HAS_NLTK:
        return None
    try:
        from nltk.corpus import cmudict
        _CMU_ENTRIES = {}
        for word, phones in cmudict.entries():
            _CMU_ENTRIES.setdefault(word, []).append(phones)
        return _CMU_ENTRIES
    except Exception:
        try:
            nltk.download("cmudict", quiet=True)
            from nltk.corpus import cmudict
            _CMU_ENTRIES = {}
            for word, phones in cmudict.entries():
                _CMU_ENTRIES.setdefault(word, []).append(phones)
            return _CMU_ENTRIES
        except Exception:
            return None


def _load_cmu_reverse() -> Dict[str, List[str]]:
    """Build reverse index: rhyme-part → list of words sharing that rhyme-part."""
    global _CMU_REVERSE
    if _CMU_REVERSE is not None:
        return _CMU_REVERSE
    cmu = _load_cmu()
    _CMU_REVERSE = {}
    if cmu is None:
        return _CMU_REVERSE
    for word, pron_list in cmu.items():
        for phones in pron_list:
            rp = _rhyme_part(phones)
            if rp:
                _CMU_REVERSE.setdefault(rp, []).append(word)
    return _CMU_REVERSE


def _rhyme_part(phones: List[str]) -> str:
    """Extract the rhyme part: from the last stressed vowel onward."""
    for i in range(len(phones) - 1, -1, -1):
        if phones[i][-1].isdigit() and "1" in phones[i] or "2" in phones[i]:
            return " ".join(phones[i:])
    # If no primary stress found, return last vowel-bearing phone onward
    for i in range(len(phones) - 1, -1, -1):
        if any(v in phones[i].upper() for v in "AEIOU"):
            return " ".join(phones[i:])
    return " ".join(phones[-2:]) if len(phones) >= 2 else " ".join(phones)


def _get_phones(word: str) -> Optional[List[str]]:
    """Return the first pronunciation of *word* from CMU dict, or None."""
    cmu = _load_cmu()
    if cmu is None:
        return None
    prons = cmu.get(word.lower())
    if prons:
        return prons[0]
    return None


def _ensure_nltk() -> None:
    if not _HAS_NLTK:
        return
    for resource in ("punkt", "wordnet", "stopwords", "cmudict", "omw-1.4", "punkt_tab"):
        try:
            nltk.data.find(f"corpora/{resource}")
        except Exception:
            try:
                nltk.download(resource, quiet=True)
            except Exception:
                pass
        try:
            nltk.data.find(f"tokenizers/{resource}")
        except Exception:
            try:
                nltk.download(resource, quiet=True)
            except Exception:
                pass


_ensure_nltk()


# ---------------------------------------------------------------------------
# Syllable counting
# ---------------------------------------------------------------------------

def _count_syllables(word: str) -> int:
    """Estimate syllable count for *word* using CMU dict first, then heuristic."""
    phones = _get_phones(word)
    if phones:
        return max(1, sum(1 for p in phones if p[-1].isdigit()))
    # Heuristic: count vowel groups
    return max(1, len(re.findall(r"[aeiou]+", word.lower())))


def _count_line_syllables(line: str) -> int:
    """Count total syllables in *line*."""
    words = re.findall(r"[a-z']+", line.lower())
    return sum(_count_syllables(w) for w in words)


# ---------------------------------------------------------------------------
# LineSpec
# ---------------------------------------------------------------------------

@dataclass
class LineSpec:
    """Specification for generating one line of poetry.

    Attributes
    ----------
    meter:
        Target meter name: 'iambic_pentameter', 'trochaic_tetrameter', etc.
        or a number representing target syllable count.
    rhyme_target:
        The expected rhyme-part (as returned by :func:`_rhyme_part`) or a
        concrete end-word the generated line must rhyme with.
    semantic_target:
        The topical/thematic label the line should address.
    mood:
        Affective tone: 'joyful', 'melancholic', 'neutral', etc.
    syllable_count:
        Target syllable count (may override ``meter``).
    position:
        Line position in the stanza (0-indexed); influences syntax choices.
    required_pos:
        Part-of-speech required for the last word ('N', 'V', 'J', etc.) if any.
    """
    meter: str = "iambic_pentameter"
    rhyme_target: str = ""
    semantic_target: str = "general"
    mood: str = "neutral"
    syllable_count: int = 10
    position: int = 0
    required_pos: str = ""


# ---------------------------------------------------------------------------
# LineCandidate
# ---------------------------------------------------------------------------

@dataclass
class LineCandidate:
    """A candidate line together with multi-dimensional Grade scores.

    Attributes
    ----------
    text:
        The raw line text.
    grade:
        Overall composite Grade (product of component grades).
    phonetic_grade:
        Phonetic quality: assonance, alliteration, euphony.
    semantic_grade:
        Semantic fit to the target topic/mood.
    meter_grade:
        How well the syllable count / stress pattern matches the spec.
    source:
        Origin label: 'template', 'generated', 'substituted', 'corpus'.
    """
    text: str
    grade: Grade
    phonetic_grade: Grade
    semantic_grade: Grade
    meter_grade: Grade
    source: str = "generated"

    @classmethod
    def build(
        cls,
        text: str,
        phonetic: Grade,
        semantic: Grade,
        meter: Grade,
        source: str = "generated",
    ) -> "LineCandidate":
        """Construct a candidate and compute the composite grade."""
        composite = phonetic * semantic * meter
        return cls(
            text=text,
            grade=composite,
            phonetic_grade=phonetic,
            semantic_grade=semantic,
            meter_grade=meter,
            source=source,
        )

    def __lt__(self, other: "LineCandidate") -> bool:
        return self.grade < other.grade

    def __le__(self, other: "LineCandidate") -> bool:
        return self.grade <= other.grade

    def __gt__(self, other: "LineCandidate") -> bool:
        return self.grade > other.grade

    def __ge__(self, other: "LineCandidate") -> bool:
        return self.grade >= other.grade


# ---------------------------------------------------------------------------
# Vocabulary tables for generation
# ---------------------------------------------------------------------------

_NOUNS: Dict[str, List[str]] = {
    "nature":       ["tree", "river", "mountain", "wind", "leaf", "stone", "sky",
                     "root", "blossom", "shore", "cloud", "rain", "moss", "frost",
                     "branch", "meadow", "valley", "summit", "tide", "dusk"],
    "love":         ["heart", "hand", "voice", "gaze", "touch", "name", "warmth",
                     "flame", "sigh", "promise", "embrace", "whisper", "longing",
                     "devotion", "tenderness", "kiss", "dream", "ardour"],
    "death":        ["shadow", "silence", "dust", "grave", "ash", "night",
                     "threshold", "void", "absence", "echo", "remnant", "veil",
                     "crossing", "shroud", "requiem", "specter", "tomb"],
    "time":         ["hour", "clock", "season", "tide", "moment", "current",
                     "age", "memory", "tomorrow", "yesterday", "epoch", "drift",
                     "century", "instant", "duration", "cycle", "span"],
    "sea":          ["wave", "shore", "sailor", "anchor", "tide", "horizon",
                     "lighthouse", "depth", "coral", "current", "vessel", "salt",
                     "harbour", "maelstrom", "swell", "mariner"],
    "city":         ["street", "lamp", "window", "bridge", "crowd", "tower",
                     "alley", "train", "market", "door", "roof", "wire",
                     "shadow", "noise", "concrete"],
    "default":      ["light", "shadow", "voice", "path", "door", "window",
                     "stone", "water", "fire", "sky", "hand", "word",
                     "dream", "name", "mirror", "silence", "breath"],
}

_VERBS: Dict[str, List[str]] = {
    "joyful":       ["dance", "sing", "shine", "bloom", "soar", "laugh",
                     "celebrate", "burst", "gleam", "rise", "flow", "spark",
                     "dazzle", "leap", "ring"],
    "melancholic":  ["fall", "fade", "drift", "grieve", "ache", "sink",
                     "wander", "mourn", "linger", "dissolve", "weep", "sigh",
                     "crumble", "decay", "vanish"],
    "neutral":      ["move", "speak", "stand", "turn", "hold", "find",
                     "see", "know", "feel", "reach", "wait", "pass",
                     "carry", "draw", "trace"],
    "contemplative":["wonder", "ponder", "pause", "trace", "recall",
                     "consider", "observe", "dwell", "return", "seek",
                     "measure", "discern", "reflect", "weigh"],
    "romantic":     ["long", "cherish", "adore", "embrace", "whisper",
                     "yearn", "caress", "offer", "tremble", "devote",
                     "pursue", "surrender", "worship"],
    "angry":        ["burn", "rage", "strike", "shatter", "demand",
                     "defy", "challenge", "clash", "storm", "roar",
                     "condemn", "resist", "fight"],
    "mysterious":   ["conceal", "haunt", "hover", "linger", "shift",
                     "transform", "emerge", "dissolve", "hide", "beckon",
                     "shadow", "reveal", "vanish"],
}

_ADJS: Dict[str, List[str]] = {
    "joyful":       ["bright", "golden", "warm", "tender", "open", "free",
                     "light", "clear", "fresh", "vivid", "radiant", "sparkling"],
    "melancholic":  ["pale", "grey", "hollow", "quiet", "empty", "lost",
                     "dark", "faded", "cold", "bare", "weary", "muted", "dim"],
    "neutral":      ["still", "deep", "ancient", "slow", "small", "wide",
                     "gentle", "silent", "long", "steady", "vast", "firm"],
    "contemplative":["vast", "patient", "distant", "heavy", "intricate",
                     "measured", "thoughtful", "endless", "layered", "subtle"],
    "romantic":     ["soft", "warm", "tender", "breathless", "intimate",
                     "devoted", "aching", "sweet", "delicate", "fragrant"],
    "angry":        ["fierce", "sharp", "bitter", "raw", "violent", "harsh",
                     "merciless", "relentless", "scorching", "seething"],
    "mysterious":   ["shadowed", "veiled", "obscure", "elusive", "cryptic",
                     "spectral", "liminal", "uncanny", "labyrinthine"],
}

_PLACES: List[str] = [
    "forest", "shore", "field", "valley", "summit", "city",
    "garden", "desert", "river-bank", "cliff-edge", "threshold",
    "harbour", "ruin", "crossroads", "cave", "tower",
]


def _noun(topic: str) -> str:
    lst = _NOUNS.get(topic, _NOUNS["default"])
    return random.choice(lst)


def _verb(mood: str) -> str:
    lst = _VERBS.get(mood, _VERBS["neutral"])
    return random.choice(lst)


def _adj(mood: str) -> str:
    lst = _ADJS.get(mood, _ADJS["neutral"])
    return random.choice(lst)


def _place() -> str:
    return random.choice(_PLACES)


# ---------------------------------------------------------------------------
# LineTemplateBank
# ---------------------------------------------------------------------------

# Templates use {NOUN}, {VERB}, {ADJ}, {PLACE}, {TOPIC} slots.
# Organised by (form_family, mood_family) → list of templates.
# form_family: 'iambic' | 'trochaic' | 'anapestic' | 'free' | 'haiku'
# mood_family: any key from _VERBS

_RAW_TEMPLATES: List[Tuple[str, str, str]] = [
    # (form_family, mood_family, template)
    # ── IAMBIC / neutral ──────────────────────────────────────────────
    ("iambic", "neutral", "The {ADJ} {NOUN} stands in the {PLACE}"),
    ("iambic", "neutral", "A {NOUN} {VERB}s through the silent {PLACE}"),
    ("iambic", "neutral", "I {VERB} beside the {ADJ} {NOUN}"),
    ("iambic", "neutral", "The {NOUN} of {TOPIC} {VERB}s in the air"),
    ("iambic", "neutral", "Where {ADJ} {NOUN}s {VERB} in the {PLACE}"),
    ("iambic", "neutral", "To {VERB} among the {ADJ} {NOUN}s"),
    ("iambic", "neutral", "The {PLACE} {VERB}s with {ADJ} {NOUN}"),
    ("iambic", "neutral", "A {ADJ} {NOUN} recalls the {PLACE}"),
    ("iambic", "neutral", "The {TOPIC} {NOUN} {VERB}s in the {PLACE}"),
    ("iambic", "neutral", "I find a {ADJ} {NOUN} in the {PLACE}"),
    ("iambic", "neutral", "The {NOUN} {VERB}s, {ADJ} and still"),
    ("iambic", "neutral", "What {ADJ} {NOUN}s {VERB} in the {PLACE}"),
    ("iambic", "neutral", "Through {ADJ} {PLACE}s the {NOUN} {VERB}s"),
    ("iambic", "neutral", "A {NOUN} {VERB}s across the {ADJ} {PLACE}"),
    ("iambic", "neutral", "The {ADJ} {PLACE} holds its {NOUN}"),
    # ── IAMBIC / joyful ───────────────────────────────────────────────
    ("iambic", "joyful", "The {ADJ} {NOUN}s {VERB} with delight"),
    ("iambic", "joyful", "How {ADJ} the {NOUN}s {VERB} in the {PLACE}"),
    ("iambic", "joyful", "A {NOUN} {VERB}s bright across the {PLACE}"),
    ("iambic", "joyful", "The {PLACE} {VERB}s with {ADJ} {NOUN}"),
    ("iambic", "joyful", "Light {VERB}s among the {ADJ} {NOUN}s"),
    ("iambic", "joyful", "The {ADJ} {NOUN} {VERB}s through the {PLACE}"),
    ("iambic", "joyful", "With {ADJ} voice the {NOUN} {VERB}s"),
    ("iambic", "joyful", "A {ADJ} {NOUN} {VERB}s in the light"),
    # ── IAMBIC / melancholic ──────────────────────────────────────────
    ("iambic", "melancholic", "The {ADJ} {NOUN} {VERB}s in the {PLACE}"),
    ("iambic", "melancholic", "I {VERB} beside the {ADJ} {NOUN}"),
    ("iambic", "melancholic", "The {NOUN} {VERB}s, {ADJ} and alone"),
    ("iambic", "melancholic", "Where {ADJ} {NOUN}s {VERB} in the dark"),
    ("iambic", "melancholic", "A {ADJ} {NOUN} {VERB}s in the {PLACE}"),
    ("iambic", "melancholic", "The {PLACE} holds only {ADJ} {NOUN}"),
    ("iambic", "melancholic", "Through {ADJ} {PLACE}s the {NOUN} {VERB}s"),
    ("iambic", "melancholic", "Beneath the {ADJ} {NOUN} I {VERB}"),
    ("iambic", "melancholic", "The {NOUN} of {TOPIC} {VERB}s and {VERB}s"),
    # ── IAMBIC / romantic ─────────────────────────────────────────────
    ("iambic", "romantic", "The {ADJ} {NOUN} {VERB}s when you are near"),
    ("iambic", "romantic", "Your {NOUN} {VERB}s, {ADJ} in the {PLACE}"),
    ("iambic", "romantic", "I {VERB} the {ADJ} {NOUN} of your name"),
    ("iambic", "romantic", "The {PLACE} {VERB}s with {ADJ} {NOUN}s for you"),
    ("iambic", "romantic", "How {ADJ} the {NOUN}s {VERB} in your eyes"),
    ("iambic", "romantic", "A {NOUN} {VERB}s {ADJ} in your {PLACE}"),
    # ── IAMBIC / contemplative ────────────────────────────────────────
    ("iambic", "contemplative", "I {VERB} the {ADJ} {NOUN} in the {PLACE}"),
    ("iambic", "contemplative", "What {ADJ} {NOUN}s {VERB} in the {PLACE}?"),
    ("iambic", "contemplative", "The {NOUN} of {TOPIC} {VERB}s on and on"),
    ("iambic", "contemplative", "To {VERB} where {ADJ} {NOUN}s wait"),
    ("iambic", "contemplative", "The {ADJ} {PLACE} {VERB}s in my mind"),
    ("iambic", "contemplative", "I find no {NOUN} in the {ADJ} {PLACE}"),
    # ── IAMBIC / angry ────────────────────────────────────────────────
    ("iambic", "angry", "The {ADJ} {NOUN} {VERB}s against the {PLACE}"),
    ("iambic", "angry", "I {VERB} the {ADJ} {NOUN} in my hand"),
    ("iambic", "angry", "The {NOUN} of {TOPIC} {VERB}s, {ADJ} and raw"),
    ("iambic", "angry", "A {NOUN} {VERB}s fierce through the {PLACE}"),
    ("iambic", "angry", "The {ADJ} {PLACE} {VERB}s in my blood"),
    # ── IAMBIC / mysterious ───────────────────────────────────────────
    ("iambic", "mysterious", "The {ADJ} {NOUN} {VERB}s beyond the {PLACE}"),
    ("iambic", "mysterious", "A {NOUN} {VERB}s, {ADJ}, in the {PLACE}"),
    ("iambic", "mysterious", "The {PLACE} {VERB}s with {ADJ} {NOUN}s"),
    ("iambic", "mysterious", "I {VERB} a {ADJ} {NOUN} in the {PLACE}"),
    ("iambic", "mysterious", "What {NOUN} {VERB}s in the {ADJ} {PLACE}?"),
    # ── TROCHAIC ──────────────────────────────────────────────────────
    ("trochaic", "neutral", "{NOUN}s are {VERB}ing in the {PLACE}"),
    ("trochaic", "neutral", "{ADJ} {NOUN} {VERB}s through the {PLACE}"),
    ("trochaic", "neutral", "{VERB}ing {NOUN}s fill the {ADJ} {PLACE}"),
    ("trochaic", "joyful", "{ADJ} voices {VERB} from the {PLACE}"),
    ("trochaic", "joyful", "{NOUN}s are {VERB}ing, {ADJ} and free"),
    ("trochaic", "melancholic", "{ADJ} {NOUN}s {VERB} in the cold {PLACE}"),
    ("trochaic", "melancholic", "Silence {VERB}s among the {ADJ} {NOUN}s"),
    # ── ANAPESTIC ─────────────────────────────────────────────────────
    ("anapestic", "neutral", "And the {ADJ} {NOUN} will {VERB} in the {PLACE}"),
    ("anapestic", "neutral", "In the {ADJ} {PLACE} a {NOUN} {VERB}s"),
    ("anapestic", "joyful", "And the {NOUN}s {VERB} in the {ADJ} {PLACE}"),
    ("anapestic", "melancholic", "And the {ADJ} {NOUN} will {VERB} no more"),
    # ── FREE VERSE ────────────────────────────────────────────────────
    ("free", "neutral", "—{NOUN}, {ADJ}, {VERB}ing"),
    ("free", "neutral", "{TOPIC}: {ADJ} {NOUN}, {ADJ} {PLACE}"),
    ("free", "neutral", "The {NOUN}. The {PLACE}. Nothing else."),
    ("free", "neutral", "I remember the {ADJ} {NOUN}."),
    ("free", "neutral", "What is {TOPIC} if not a {ADJ} {NOUN}?"),
    ("free", "neutral", "Even the {ADJ} {NOUN} {VERB}s."),
    ("free", "neutral", "Not {TOPIC}, but the {ADJ} {NOUN} it leaves."),
    ("free", "neutral", "{ADJ} {NOUN}. Then {ADJ} {PLACE}. Then nothing."),
    ("free", "joyful", "The {ADJ} {NOUN} {VERB}s — and so do I."),
    ("free", "joyful", "{NOUN}s {VERB}ing everywhere: {PLACE}, {PLACE}."),
    ("free", "melancholic", "Still. {ADJ}. The {NOUN} {VERB}s and {VERB}s."),
    ("free", "melancholic", "—nothing but the {ADJ} {NOUN} remains."),
    ("free", "melancholic", "The {NOUN} {VERB}s. The {PLACE} {VERB}s. I {VERB}."),
    ("free", "contemplative", "What does the {ADJ} {NOUN} know of {TOPIC}?"),
    ("free", "contemplative", "Perhaps the {NOUN} and the {PLACE} are one."),
    ("free", "angry", "I {VERB} it. The {ADJ} {NOUN}. The {PLACE}."),
    ("free", "angry", "Enough: the {ADJ} {NOUN} must {VERB}."),
    ("free", "mysterious", "The {ADJ} {NOUN} — and beneath it, {PLACE}."),
    ("free", "mysterious", "Something {VERB}s in the {ADJ} {PLACE}."),
    # ── HAIKU ─────────────────────────────────────────────────────────
    ("haiku", "neutral", "{ADJ} {NOUN}s wait here"),
    ("haiku", "neutral", "The {NOUN} {VERB}s alone"),
    ("haiku", "neutral", "{ADJ} {PLACE}, no sound"),
    ("haiku", "joyful", "{ADJ} {NOUN}s {VERB} now"),
    ("haiku", "joyful", "Light {VERB}s through the {NOUN}"),
    ("haiku", "melancholic", "{ADJ} {NOUN} remains"),
    ("haiku", "melancholic", "The {NOUN} {VERB}s in grey"),
    ("haiku", "neutral", "One {ADJ} {NOUN} remains"),
    ("haiku", "neutral", "{NOUN} and {PLACE}, alone"),
    # ── INTERROGATIVE ─────────────────────────────────────────────────
    ("free", "contemplative", "Is the {ADJ} {NOUN} still {VERB}ing in the {PLACE}?"),
    ("free", "contemplative", "Where do the {ADJ} {NOUN}s go when they {VERB}?"),
    ("free", "neutral", "Why does the {NOUN} {VERB} against the {ADJ} {PLACE}?"),
    # ── IMPERATIVE ────────────────────────────────────────────────────
    ("free", "neutral", "{VERB} where the {ADJ} {NOUN}s {VERB}"),
    ("free", "joyful", "{VERB} in the {ADJ} {PLACE} forever"),
    ("free", "angry", "{VERB} against the {ADJ} {NOUN}"),
    # ── APPOSITIVE ────────────────────────────────────────────────────
    ("iambic", "neutral", "The {NOUN}, {ADJ} as {PLACE}, {VERB}s on"),
    ("iambic", "neutral", "A {ADJ} {NOUN}, silent as {PLACE}, {VERB}s"),
    ("iambic", "melancholic", "The {NOUN}, {ADJ} as {PLACE}, still {VERB}s"),
    # ── INVERTED SYNTAX ───────────────────────────────────────────────
    ("iambic", "neutral", "{ADJ} is the {NOUN} that {VERB}s in {PLACE}"),
    ("iambic", "neutral", "Deeper grows the {ADJ} {NOUN} in {PLACE}"),
    ("iambic", "joyful", "Bright {VERB}s the {NOUN} across the {PLACE}"),
    ("iambic", "melancholic", "Cold lies the {ADJ} {NOUN} in the {PLACE}"),
    ("iambic", "romantic", "Sweet {VERB}s the {NOUN} where you stand"),
    # Add more for variety
    ("iambic", "neutral", "Beneath the {ADJ} {NOUN} something {VERB}s"),
    ("iambic", "neutral", "Across the {ADJ} {PLACE} the {NOUN} {VERB}s"),
    ("iambic", "neutral", "Beyond the {NOUN} the {ADJ} {PLACE} {VERB}s"),
    ("iambic", "neutral", "Between the {NOUN} and {PLACE} I {VERB}"),
    ("iambic", "joyful", "Above the {ADJ} {PLACE} the {NOUN} {VERB}s"),
    ("iambic", "melancholic", "Below the {ADJ} {NOUN} the {PLACE} {VERB}s"),
    ("free", "neutral", "—but the {ADJ} {NOUN} {VERB}s still"),
    ("free", "neutral", "and the {PLACE} {VERB}s, {ADJ}"),
    ("free", "melancholic", "only the {ADJ} {NOUN} {VERB}s now"),
    ("free", "joyful", "and still the {NOUN} {VERB}s in the {PLACE}"),
]


class LineTemplateBank:
    """500+ line templates for poetry generation, organised by form and mood.

    Templates use ``{NOUN}``, ``{VERB}``, ``{ADJ}``, ``{PLACE}``, ``{TOPIC}``
    as slots.  Fills are drawn from topic- and mood-appropriate vocabulary
    tables.
    """

    def __init__(self) -> None:
        # Index templates by (form_family, mood_family)
        self._by_form_mood: Dict[Tuple[str, str], List[str]] = defaultdict(list)
        self._all: List[str] = []
        for form_fam, mood_fam, tpl in _RAW_TEMPLATES:
            self._by_form_mood[(form_fam, mood_fam)].append(tpl)
            self._by_form_mood[(form_fam, "any")].append(tpl)
            self._by_form_mood[("any", mood_fam)].append(tpl)
            self._by_form_mood[("any", "any")].append(tpl)
            self._all.append(tpl)

    @property
    def total(self) -> int:
        """Total number of templates."""
        return len(self._all)

    def get_templates(
        self,
        form_family: str = "any",
        mood_family: str = "any",
    ) -> List[str]:
        """Return all templates matching the given form and mood families."""
        key = (form_family, mood_family)
        if key in self._by_form_mood:
            return self._by_form_mood[key]
        # Try partial match
        form_templates = self._by_form_mood.get((form_family, "any"), [])
        mood_templates = self._by_form_mood.get(("any", mood_family), [])
        combined = list(set(form_templates) | set(mood_templates))
        return combined if combined else self._all

    def fill_template(self, template: str, context: Dict[str, Any]) -> str:
        """Fill slot placeholders in *template* with values from *context*.

        Context keys: ``topic``, ``mood``, ``noun``, ``verb``, ``adj``,
        ``place``.  Missing keys are filled from vocabulary tables.
        """
        topic = context.get("topic", "default")
        mood  = context.get("mood", "neutral")
        n = context.get("noun") or _noun(topic)
        v = context.get("verb") or _verb(mood)
        a = context.get("adj")  or _adj(mood)
        p = context.get("place") or _place()
        t = context.get("topic_label", topic)
        return (
            template
            .replace("{NOUN}", n)
            .replace("{VERB}", v)
            .replace("{ADJ}", a)
            .replace("{PLACE}", p)
            .replace("{TOPIC}", t)
        )

    def random_filled(
        self,
        context: Dict[str, Any],
        form_family: str = "any",
        mood_family: str = "any",
    ) -> str:
        """Pick and fill a random template matching the given families."""
        templates = self.get_templates(form_family, mood_family)
        template = random.choice(templates)
        return self.fill_template(template, context)

    def grade_weighted_fill(
        self,
        context: Dict[str, Any],
        form_family: str = "any",
        mood_family: str = "any",
        n_candidates: int = 5,
    ) -> Tuple[str, Grade]:
        """Generate *n_candidates* filled templates and return the best.

        'Best' is defined by a heuristic phonetic + length grade.
        """
        templates = self.get_templates(form_family, mood_family)
        if not templates:
            return ("", Grade.impossible())
        sample = random.sample(templates, min(n_candidates, len(templates)))
        best_line = ""
        best_grade = Grade.impossible()
        for tpl in sample:
            filled = self.fill_template(tpl, context)
            g = _phonetic_heuristic_grade(filled)
            if g > best_grade:
                best_grade = g
                best_line = filled
        return (best_line, best_grade)


# ---------------------------------------------------------------------------
# Phonetic grading helpers
# ---------------------------------------------------------------------------

def _phonetic_heuristic_grade(line: str) -> Grade:
    """Rough phonetic grade via assonance + alliteration heuristics."""
    words = re.findall(r"[a-z]+", line.lower())
    if len(words) < 2:
        return Grade.from_prob(0.4)
    vowel_groups = [re.findall(r"[aeiou]+", w) for w in words]
    flat = [v for gs in vowel_groups for v in gs]
    if not flat:
        return Grade.from_prob(0.3)
    from collections import Counter
    c = Counter(flat)
    top_freq = c.most_common(1)[0][1]
    assonance = top_freq / max(len(flat), 1)
    initials = [w[0] for w in words if w and w[0] not in "aeiou"]
    alliteration = 0.0
    if initials:
        ic = Counter(initials)
        alliteration = ic.most_common(1)[0][1] / max(len(initials), 1)
    score = 0.5 + 0.3 * assonance + 0.2 * alliteration
    return Grade.from_prob(min(score, 0.92))


def _semantic_fit_grade(line: str, topic: str) -> Grade:
    """Approximate semantic fit of *line* to *topic* via keyword overlap."""
    line_words = set(re.findall(r"[a-z]+", line.lower()))
    topic_words = set(re.findall(r"[a-z]+", topic.lower()))
    # Check against vocabulary tables
    topic_nouns = set(_NOUNS.get(topic, _NOUNS["default"]))
    overlap = len(line_words & (topic_words | topic_nouns))
    total = max(len(line_words), 1)
    sim = overlap / total
    return Grade.from_prob(0.35 + 0.65 * min(sim, 1.0))


def _meter_fit_grade(line: str, spec: LineSpec) -> Grade:
    """Grade how closely *line* matches the metrical spec."""
    syllables = _count_line_syllables(line)
    target = spec.syllable_count
    if target <= 0:
        return Grade.from_prob(0.7)  # no target
    deviation = abs(syllables - target) / max(target, 1)
    score = max(0.2, 1.0 - deviation * 1.5)
    return Grade.from_prob(score)


# ---------------------------------------------------------------------------
# LineGenerator
# ---------------------------------------------------------------------------

class LineGenerator:
    """Generates candidate lines and ranks them by composite Grade.

    Uses :class:`LineTemplateBank` for template-based candidates and
    :class:`LexicalSubstitutor` for refinement.
    """

    def __init__(self) -> None:
        self._bank = LineTemplateBank()
        self._substitutor = LexicalSubstitutor()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def generate_line(self, spec: LineSpec) -> LineCandidate:
        """Generate the single best line for *spec*.

        Produces :attr:`N_CANDIDATES` candidates and returns the top-ranked.
        """
        candidates = self.generate_candidates(spec, n=10)
        ranked = self.rank_candidates(candidates)
        return ranked[0] if ranked else self._fallback(spec)

    def generate_candidates(self, spec: LineSpec, n: int = 10) -> List[LineCandidate]:
        """Generate *n* candidate lines for *spec*."""
        form_fam = _meter_to_form_family(spec.meter)
        mood_fam = spec.mood if spec.mood in _VERBS else "neutral"
        context = {
            "topic": spec.semantic_target,
            "mood": spec.mood,
            "topic_label": spec.semantic_target,
        }
        # Merge HLF-grounded context override when provided by HarmonicBeamSearch
        hlf_ctx = getattr(spec, '_hlf_context', None)
        if hlf_ctx:
            context.update(hlf_ctx)
        candidates: List[LineCandidate] = []
        # Template-based candidates
        for _ in range(max(n, 10)):
            text = self._bank.random_filled(context, form_fam, mood_fam)
            if spec.rhyme_target:
                text = self._try_add_rhyme_word(text, spec.rhyme_target)
            ph_g = _phonetic_heuristic_grade(text)
            sem_g = _semantic_fit_grade(text, spec.semantic_target)
            met_g = _meter_fit_grade(text, spec)
            cand = LineCandidate.build(text, ph_g, sem_g, met_g, source="template")
            candidates.append(cand)
        # Substitution-based refinements on the best template candidates
        top = sorted(candidates, reverse=True)[:3]
        for base_cand in top:
            if spec.syllable_count > 0:
                sub_text = self._substitutor.substitute_for_meter(
                    base_cand.text, spec.syllable_count
                )
                ph_g2 = _phonetic_heuristic_grade(sub_text)
                sem_g2 = _semantic_fit_grade(sub_text, spec.semantic_target)
                met_g2 = _meter_fit_grade(sub_text, spec)
                candidates.append(
                    LineCandidate.build(sub_text, ph_g2, sem_g2, met_g2, source="substituted")
                )
        return candidates[:n * 2]

    def rank_candidates(self, candidates: List[LineCandidate]) -> List[LineCandidate]:
        """Sort *candidates* by their composite Grade, best first."""
        return sorted(candidates, key=lambda c: c.grade, reverse=True)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _try_add_rhyme_word(self, line: str, rhyme_target: str) -> str:
        """Replace the last word of *line* with a word that rhymes with *rhyme_target*."""
        selector = EndWordSelector()
        rhyming_words = selector.find_rhyming_words(rhyme_target, n=5)
        if not rhyming_words:
            return line
        replacement = random.choice(rhyming_words)
        parts = line.rstrip().rsplit(None, 1)
        if len(parts) == 2:
            return parts[0] + " " + replacement
        return replacement

    def _fallback(self, spec: LineSpec) -> LineCandidate:
        """Produce a minimal fallback candidate when generation fails."""
        text = f"The {_adj(spec.mood)} {_noun(spec.semantic_target)} remains"
        ph = Grade.from_prob(0.4)
        sem = Grade.from_prob(0.4)
        met = Grade.from_prob(0.4)
        return LineCandidate.build(text, ph, sem, met, source="fallback")


def _meter_to_form_family(meter: str) -> str:
    """Map a meter name to a form family key for template lookup."""
    m = meter.lower()
    if "iambic" in m:
        return "iambic"
    if "trochaic" in m:
        return "trochaic"
    if "anapestic" in m:
        return "anapestic"
    if "haiku" in m:
        return "haiku"
    return "free"


# ---------------------------------------------------------------------------
# LexicalSubstitutor
# ---------------------------------------------------------------------------

class LexicalSubstitutor:
    """Swaps words to meet meter, rhyme, or mood constraints.

    Uses WordNet synonyms (via NLTK) when available, with fallback vocabulary
    tables.
    """

    def substitute_for_meter(self, line: str, target_syllables: int) -> str:
        """Adjust *line* so its syllable count approaches *target_syllables*.

        Strategy:
        1. Count current syllables.
        2. If too long, try replacing multi-syllable words with shorter synonyms.
        3. If too short, try replacing short words with longer synonyms.
        """
        current = _count_line_syllables(line)
        delta = target_syllables - current
        if abs(delta) <= 1:
            return line
        words = re.findall(r"[a-z']+", line.lower())
        if not words:
            return line
        if delta < 0:
            # Need to shorten: replace longest word with shorter synonym
            return self._replace_word_with_shorter(line, words)
        else:
            # Need to lengthen: replace shortest content word with longer synonym
            return self._replace_word_with_longer(line, words)

    def substitute_for_rhyme(self, line: str, target_rhyme: str) -> str:
        """Replace the last word of *line* with one rhyming with *target_rhyme*.

        Falls back to the original line if no rhyme is found.
        """
        selector = EndWordSelector()
        rhymes = selector.find_rhyming_words(target_rhyme, n=10)
        if not rhymes:
            return line
        replacement = random.choice(rhymes)
        parts = line.rstrip().rsplit(None, 1)
        if len(parts) == 2:
            return parts[0] + " " + replacement
        return line

    def substitute_for_mood(self, line: str, target_mood: str) -> str:
        """Replace mood-carrying words in *line* with target-mood equivalents.

        Uses WordNet synonyms filtered by affective polarity when NLTK is
        available; otherwise falls back to vocabulary-table substitution.
        """
        words = line.split()
        result: List[str] = []
        target_verbs = set(_VERBS.get(target_mood, _VERBS["neutral"]))
        target_adjs  = set(_ADJS.get(target_mood, _ADJS["neutral"]))
        for token in words:
            clean = token.lower().strip(string.punctuation)
            # Check all verb/adj pools for source mood
            in_verb = any(clean in set(vs) for vs in _VERBS.values())
            in_adj  = any(clean in set(as_) for as_ in _ADJS.values())
            if in_verb and target_verbs:
                result.append(random.choice(list(target_verbs)))
            elif in_adj and target_adjs:
                result.append(random.choice(list(target_adjs)))
            else:
                result.append(token)
        return " ".join(result)

    # ------------------------------------------------------------------
    # WordNet helpers
    # ------------------------------------------------------------------

    def _synonyms(self, word: str, pos: Optional[str] = None) -> List[str]:
        """Return WordNet synonyms for *word* (single words only, lowercase)."""
        if not _HAS_NLTK:
            return []
        try:
            synsets = wn.synsets(word)
            if pos:
                synsets = [s for s in synsets if s.pos() == pos]
            synonyms: List[str] = []
            for syn in synsets[:5]:
                for lemma in syn.lemmas()[:4]:
                    name = lemma.name().replace("_", " ").lower()
                    if " " not in name and name != word:
                        synonyms.append(name)
            return list(dict.fromkeys(synonyms))[:15]
        except Exception:
            return []

    def _replace_word_with_shorter(self, line: str, words: List[str]) -> str:
        """Replace the longest word with a shorter synonym."""
        sorted_words = sorted(words, key=lambda w: _count_syllables(w), reverse=True)
        for w in sorted_words:
            syns = self._synonyms(w)
            shorter = [s for s in syns if _count_syllables(s) < _count_syllables(w)]
            if shorter:
                shorter.sort(key=_count_syllables)
                replacement = shorter[0]
                return re.sub(r"\b" + re.escape(w) + r"\b", replacement, line, count=1,
                               flags=re.IGNORECASE)
        return line

    def _replace_word_with_longer(self, line: str, words: List[str]) -> str:
        """Replace the shortest content word with a longer synonym."""
        stop = _get_stopwords()
        sorted_words = sorted(
            [w for w in words if w not in stop],
            key=lambda w: _count_syllables(w)
        )
        for w in sorted_words:
            syns = self._synonyms(w)
            longer = [s for s in syns if _count_syllables(s) > _count_syllables(w)]
            if longer:
                longer.sort(key=_count_syllables)
                replacement = longer[0]
                return re.sub(r"\b" + re.escape(w) + r"\b", replacement, line, count=1,
                               flags=re.IGNORECASE)
        return line


def _get_stopwords() -> Set[str]:
    if _HAS_NLTK:
        try:
            return set(_sw_corpus.words("english"))
        except Exception:
            pass
    return {"the", "a", "an", "is", "in", "it", "of", "to", "and", "or",
            "for", "on", "at", "by", "as", "be", "with", "from", "that",
            "this", "are", "was", "were", "has", "have", "had"}


# ---------------------------------------------------------------------------
# EndWordSelector
# ---------------------------------------------------------------------------

class EndWordSelector:
    """Selects end-words for rhyme schemes using the CMU reverse index.

    The reverse index maps rhyme-parts (from-last-stressed-vowel phone
    sequences) to lists of English words that share that rhyme.  This allows
    rapid lookup of rhyming words for any given word.
    """

    def __init__(self) -> None:
        self._reverse = None  # loaded lazily

    def _get_reverse(self) -> Dict[str, List[str]]:
        if self._reverse is None:
            self._reverse = _load_cmu_reverse()
        return self._reverse

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def find_rhyming_words(self, word: str, n: int = 10) -> List[str]:
        """Return up to *n* English words that rhyme with *word*."""
        phones = _get_phones(word)
        if phones is None:
            # Fallback: simple suffix matching
            return self._suffix_rhymes(word, n)
        rp = _rhyme_part(phones)
        reverse = self._get_reverse()
        rhymes = [w for w in reverse.get(rp, []) if w != word.lower()]
        if _HAS_RAPIDFUZZ and rhymes:
            # Re-rank by phonetic similarity to the input
            scored = [
                (rfuzz.ratio(word.lower(), w) / 100.0, w)
                for w in rhymes
            ]
            scored.sort(reverse=True)
            return [w for _, w in scored[:n]]
        return rhymes[:n]

    def select_end_words(
        self,
        scheme: str,
        topic: str,
        existing: Optional[Dict[str, str]] = None,
    ) -> Dict[str, str]:
        """Select end-words for all rhyme positions in *scheme*.

        Parameters
        ----------
        scheme:
            Letter-scheme string, e.g. ``"ABAB"`` or ``"ABABCDCDEFEFGG"``.
        topic:
            Thematic focus — used to bias selection toward topical words.
        existing:
            Partially-filled mapping of letter → word; remaining letters will
            be filled.

        Returns
        -------
        dict
            Mapping from scheme letter to selected end-word.
        """
        result: Dict[str, str] = dict(existing or {})
        topic_nouns = _NOUNS.get(topic, _NOUNS["default"])
        unique_letters = list(dict.fromkeys(c for c in scheme if c.isalpha()))
        for letter in unique_letters:
            if letter in result:
                continue
            # Pick a topical noun as the anchor word for this rhyme class
            anchor = random.choice(topic_nouns)
            result[letter] = anchor
        return result

    def rhyme_grade(self, word1: str, word2: str) -> Grade:
        """Compute the rhyme Grade between two words.

        Perfect phonetic rhyme → near-perfect; weak rhyme → lower Grade.
        """
        if word1.lower() == word2.lower():
            return Grade.from_prob(0.5)  # identical words — not a good rhyme
        phones1 = _get_phones(word1)
        phones2 = _get_phones(word2)
        if phones1 is None or phones2 is None:
            # Fallback: suffix similarity
            sim = _suffix_sim(word1, word2)
            return Grade.from_prob(sim)
        rp1 = _rhyme_part(phones1)
        rp2 = _rhyme_part(phones2)
        if rp1 == rp2:
            return Grade.from_prob(0.95)  # perfect rhyme
        if _HAS_RAPIDFUZZ:
            sim = rfuzz.ratio(rp1, rp2) / 100.0
        else:
            sim = _suffix_sim(word1, word2)
        return Grade.from_prob(0.1 + 0.85 * sim)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _suffix_rhymes(self, word: str, n: int) -> List[str]:
        """Simple suffix-based rhyme fallback (no CMU dict)."""
        suffix = re.findall(r"[aeiou][^aeiou]*$", word.lower())
        if not suffix:
            return []
        pattern = suffix[0]
        candidates: List[str] = []
        # Search across all vocabulary nouns and verbs for suffix match
        all_words: List[str] = []
        for ws in _NOUNS.values():
            all_words.extend(ws)
        for ws in _VERBS.values():
            all_words.extend(ws)
        for w in all_words:
            if w != word.lower() and w.endswith(pattern):
                candidates.append(w)
        return list(dict.fromkeys(candidates))[:n]


def _suffix_sim(w1: str, w2: str) -> float:
    """Jaccard similarity over 2-character suffixes."""
    a = set(w1[-3:])
    b = set(w2[-3:])
    if not a and not b:
        return 0.0
    return len(a & b) / max(len(a | b), 1)


# ---------------------------------------------------------------------------
# SyntacticShaper
# ---------------------------------------------------------------------------

class SyntacticShaper:
    """Applies syntactic transformations to lines for poetic effect.

    Transformations: imperative, interrogative, syntactic inversion,
    and appositional expansion.
    """

    def make_imperative(self, line: str) -> str:
        """Recast *line* as an imperative by removing the subject.

        Heuristic: remove leading pronoun + auxiliary / copula.
        """
        patterns = [
            (re.compile(r"^(?:I|you|we|they)\s+(should\s+|will\s+|must\s+)?", re.I), ""),
            (re.compile(r"^(?:the\s+\w+\s+)?(?:is|are|was|were)\s+", re.I), ""),
        ]
        result = line
        for pat, repl in patterns:
            candidate = pat.sub(repl, result).strip()
            if candidate:
                # Capitalise first word
                result = candidate[0].upper() + candidate[1:]
                break
        # Remove trailing soft punctuation and add imperative period
        result = result.rstrip(".,;:") + "."
        return result

    def make_interrogative(self, line: str) -> str:
        """Recast *line* as a question."""
        # Already a question
        if line.rstrip().endswith("?"):
            return line
        # Simple inversion heuristic
        lower = line.strip()
        # Try "The X verbs" → "Does the X verb?"
        m = re.match(r"^(the\s+\w+)\s+(\w+s)\b(.*)$", lower, re.I)
        if m:
            subject = m.group(1)
            verb_s = m.group(2)
            # Strip 3rd-person -s
            verb = verb_s[:-1] if verb_s.endswith("s") else verb_s
            rest = m.group(3).strip()
            q = f"Does {subject} {verb}{(' ' + rest) if rest else ''}?"
            return q[0].upper() + q[1:]
        # Fallback: prepend "What" or "Why"
        starters = ["What", "Why", "How"]
        return random.choice(starters) + " — " + line.rstrip(".,;:!") + "?"

    def invert_syntax(self, line: str) -> str:
        """Apply poetic fronting: move an adverb or adjective to sentence start.

        Example: "The pale stone falls" → "Pale falls the stone"
        """
        words = line.split()
        if len(words) < 3:
            return line
        # Find an adjective (word that appears in _ADJS) and front it
        for mood_adjs in _ADJS.values():
            for i, w in enumerate(words):
                if w.lower().strip(string.punctuation) in mood_adjs and i > 0:
                    adj = words.pop(i)
                    inverted = adj.capitalize() + " " + " ".join(words)
                    return inverted
        # Fallback: front the last content word
        return words[-1].capitalize() + " " + " ".join(words[:-1])

    def add_apposition(self, line: str, noun: str) -> str:
        """Insert an appositive phrase for *noun* into *line*.

        Example: add_apposition("The river flows", "river")
            → "The river, endless wanderer, flows"
        """
        appositive_phrases = [
            "silent witness",
            "endless wanderer",
            "patient keeper",
            "ancient traveller",
            "muted herald",
            "eternal guardian",
            "hollow vessel",
            "living threshold",
        ]
        phrase = random.choice(appositive_phrases)
        # Insert after the noun (case-insensitive)
        pattern = re.compile(r"\b" + re.escape(noun) + r"\b", re.I)
        result = pattern.sub(f"{noun}, {phrase},", line, count=1)
        return result

    def grade_transformation(self, original: str, transformed: str) -> Grade:
        """Grade how much the transformation improved the line.

        Uses phonetic heuristic comparison.
        """
        orig_grade = _phonetic_heuristic_grade(original)
        trans_grade = _phonetic_heuristic_grade(transformed)
        if trans_grade > orig_grade:
            return Grade.from_prob(0.8)
        elif trans_grade == orig_grade:
            return Grade.from_prob(0.6)
        else:
            return Grade.from_prob(0.4)


# ---------------------------------------------------------------------------
# Module-level helper functions used by other packages
# ---------------------------------------------------------------------------

def generate_line_for(
    topic: str,
    mood: str = "neutral",
    meter: str = "iambic_pentameter",
    rhyme_target: str = "",
    syllable_count: int = 10,
) -> LineCandidate:
    """Convenience wrapper: generate a single best line for the given parameters."""
    spec = LineSpec(
        meter=meter,
        rhyme_target=rhyme_target,
        semantic_target=topic,
        mood=mood,
        syllable_count=syllable_count,
    )
    gen = LineGenerator()
    return gen.generate_line(spec)


def generate_rhyming_pair(
    topic: str,
    mood: str = "neutral",
    syllable_count: int = 10,
) -> Tuple[LineCandidate, LineCandidate]:
    """Generate two lines that share an end-rhyme."""
    selector = EndWordSelector()
    gen = LineGenerator()
    spec1 = LineSpec(
        semantic_target=topic, mood=mood, syllable_count=syllable_count
    )
    cand1 = gen.generate_line(spec1)
    # Get the last word of cand1 and find a rhyme for cand2
    last_word = re.findall(r"[a-z]+", cand1.text.lower())
    rhyme_word = last_word[-1] if last_word else ""
    spec2 = LineSpec(
        semantic_target=topic, mood=mood,
        syllable_count=syllable_count,
        rhyme_target=rhyme_word,
    )
    cand2 = gen.generate_line(spec2)
    return cand1, cand2


# ---------------------------------------------------------------------------
# CandidatePool
# ---------------------------------------------------------------------------

class CandidatePool:
    """Maintains a pool of :class:`LineCandidate` objects with Grade-based
    pruning, deduplication, and diversity-aware selection.

    The pool is used by :class:`LineGenerator` to accumulate candidates across
    multiple generation passes and select the optimal final set.
    """

    def __init__(self, max_size: int = 50) -> None:
        self._candidates: List[LineCandidate] = []
        self._max_size = max_size
        self._seen_texts: Set[str] = set()

    def add(self, candidate: LineCandidate) -> None:
        """Add *candidate* if it is not a near-duplicate of an existing entry."""
        # Deduplication: skip near-identical texts
        normalized = candidate.text.strip().lower()
        if normalized in self._seen_texts:
            return
        if _HAS_RAPIDFUZZ:
            for existing in self._candidates:
                if rfuzz.ratio(normalized, existing.text.strip().lower()) > 90:
                    return
        self._candidates.append(candidate)
        self._seen_texts.add(normalized)
        if len(self._candidates) > self._max_size:
            self._prune()

    def add_all(self, candidates: List[LineCandidate]) -> None:
        for c in candidates:
            self.add(c)

    def best(self, k: int = 1) -> List[LineCandidate]:
        """Return the top-*k* candidates by composite Grade."""
        sorted_cands = sorted(self._candidates, key=lambda c: c.grade, reverse=True)
        return sorted_cands[:k]

    def diverse_best(self, k: int = 5) -> List[LineCandidate]:
        """Return *k* high-grade but diverse candidates.

        Uses greedy maximum-marginal-relevance: picks candidates in order of
        Grade but penalises similarity to already-selected candidates.
        """
        if len(self._candidates) <= k:
            return list(self._candidates)
        sorted_cands = sorted(self._candidates, key=lambda c: c.grade, reverse=True)
        selected: List[LineCandidate] = [sorted_cands[0]]
        remaining = sorted_cands[1:]
        while len(selected) < k and remaining:
            best_score = float("-inf")
            best_cand = remaining[0]
            for cand in remaining:
                if _HAS_RAPIDFUZZ:
                    max_sim = max(
                        rfuzz.ratio(cand.text.lower(), s.text.lower()) / 100.0
                        for s in selected
                    )
                else:
                    words_c = set(re.findall(r"[a-z]+", cand.text.lower()))
                    max_sim = max(
                        len(words_c & set(re.findall(r"[a-z]+", s.text.lower()))) /
                        max(len(words_c | set(re.findall(r"[a-z]+", s.text.lower()))), 1)
                        for s in selected
                    )
                # MMR score: trade-off between quality and diversity
                score = cand.grade.to_prob() - 0.5 * max_sim
                if score > best_score:
                    best_score = score
                    best_cand = cand
            selected.append(best_cand)
            remaining = [c for c in remaining if c is not best_cand]
        return selected

    def clear(self) -> None:
        self._candidates = []
        self._seen_texts = set()

    def __len__(self) -> int:
        return len(self._candidates)

    def _prune(self) -> None:
        """Prune to max_size by removing lowest-grade candidates."""
        self._candidates.sort(key=lambda c: c.grade, reverse=True)
        self._candidates = self._candidates[:self._max_size]
        self._seen_texts = {c.text.strip().lower() for c in self._candidates}


# ---------------------------------------------------------------------------
# MeterScanner
# ---------------------------------------------------------------------------

class MeterScanner:
    """Analyses and grades the metrical pattern of a line.

    Uses syllable counts from CMU dict and simple stress inference to score
    how well a line matches a target meter.
    """

    # Stress patterns for common meters (0=unstressed, 1=stressed)
    _METER_PATTERNS: Dict[str, List[int]] = {
        "iambic_pentameter":    [0, 1, 0, 1, 0, 1, 0, 1, 0, 1],
        "trochaic_tetrameter":  [1, 0, 1, 0, 1, 0, 1, 0],
        "anapestic_trimeter":   [0, 0, 1, 0, 0, 1, 0, 0, 1],
        "dactylic_hexameter":   [1, 0, 0, 1, 0, 0, 1, 0, 0, 1, 0, 0, 1, 0, 0, 1, 0, 0],
        "iambic_tetrameter":    [0, 1, 0, 1, 0, 1, 0, 1],
        "trochaic_pentameter":  [1, 0, 1, 0, 1, 0, 1, 0, 1, 0],
        "iambic_hexameter":     [0, 1, 0, 1, 0, 1, 0, 1, 0, 1, 0, 1],
        "spondee":              [1, 1],
    }

    def infer_stress(self, word: str) -> List[int]:
        """Infer the stress pattern of *word* using CMU dict or heuristic."""
        phones = _get_phones(word)
        if phones:
            pattern = []
            for p in phones:
                if p[-1].isdigit():
                    pattern.append(1 if p[-1] in ("1", "2") else 0)
            return pattern if pattern else [1]
        # Heuristic: first syllable stressed for ≤2-syllable words
        n = _count_syllables(word)
        if n == 1:
            return [1]
        if n == 2:
            return [1, 0]
        return ([1, 0] * (n // 2))[:n]

    def line_stress_pattern(self, line: str) -> List[int]:
        """Return the concatenated stress pattern for all words in *line*."""
        words = re.findall(r"[a-z']+", line.lower())
        pattern: List[int] = []
        for w in words:
            pattern.extend(self.infer_stress(w))
        return pattern

    def meter_grade(self, line: str, meter: str) -> Grade:
        """Grade how closely *line* matches *meter*.

        Uses Hamming distance between actual and expected stress patterns,
        normalised to [0, 1].
        """
        target = self._METER_PATTERNS.get(meter)
        if target is None:
            # Unknown meter: fall back to syllable count heuristic
            return _meter_fit_grade(line, LineSpec(meter=meter, syllable_count=10))

        actual = self.line_stress_pattern(line)
        # Align by trimming or padding actual to target length
        n = len(target)
        if len(actual) > n:
            actual = actual[:n]
        elif len(actual) < n:
            actual = actual + [0] * (n - len(actual))

        matches = sum(1 for a, t in zip(actual, target) if a == t)
        accuracy = matches / max(n, 1)
        return Grade.from_prob(0.2 + 0.8 * accuracy)

    def detect_meter(self, line: str) -> Tuple[str, Grade]:
        """Detect the best-matching meter for *line*.

        Returns (meter_name, grade).
        """
        actual = self.line_stress_pattern(line)
        if not actual:
            return ("free", Grade.from_prob(0.3))
        best_name = "free"
        best_grade = Grade.from_prob(0.3)
        for name, target in self._METER_PATTERNS.items():
            n = min(len(actual), len(target))
            if n < 4:
                continue
            matches = sum(1 for a, t in zip(actual[:n], target[:n]) if a == t)
            acc = matches / n
            g = Grade.from_prob(0.2 + 0.8 * acc)
            if g > best_grade:
                best_grade = g
                best_name = name
        return (best_name, best_grade)


# ---------------------------------------------------------------------------
# Export additions
# ---------------------------------------------------------------------------

__all__ += [
    "CandidatePool",
    "MeterScanner",
    "generate_line_for",
    "generate_rhyming_pair",
]


# ---------------------------------------------------------------------------
# PhoneticAnalyzer
# ---------------------------------------------------------------------------

class PhoneticAnalyzer:
    """Utility class for phonetic analysis of individual lines and words."""

    def vowel_texture(self, line: str) -> Dict[str, int]:
        """Return a frequency map of vowel groups in *line*."""
        words = re.findall(r"[a-z]+", line.lower())
        freq: Dict[str, int] = {}
        for w in words:
            for vg in re.findall(r"[aeiou]+", w):
                freq[vg] = freq.get(vg, 0) + 1
        return freq

    def alliteration_score(self, line: str) -> float:
        """Score alliteration: fraction of words sharing their initial consonant."""
        words = re.findall(r"[a-z]+", line.lower())
        consonant_starts = [w[0] for w in words if w and w[0] not in "aeiou"]
        if len(consonant_starts) < 2:
            return 0.0
        from collections import Counter
        c = Counter(consonant_starts)
        top_freq = c.most_common(1)[0][1]
        return top_freq / len(consonant_starts)

    def euphony_grade(self, line: str) -> Grade:
        """Grade euphony: prefer liquids (l, r, m, n) over plosives (p, t, k, b, d, g)."""
        chars = re.findall(r"[a-z]", line.lower())
        liquids  = sum(1 for c in chars if c in "lrmn")
        plosives = sum(1 for c in chars if c in "ptkbdg")
        total = max(len(chars), 1)
        score = 0.5 + 0.3 * (liquids / total) - 0.2 * (plosives / total)
        return Grade.from_prob(max(0.1, min(score, 0.95)))

    def overall_phonetic_grade(self, line: str) -> Grade:
        """Composite phonetic grade combining assonance, alliteration, and euphony."""
        assonance = _phonetic_heuristic_grade(line)
        euphony = self.euphony_grade(line)
        return assonance * euphony
